"""
Tests for IMD.region() and imdlib.regions.

- Unit tests use a small synthetic region dataset (same format as the
  shipped one) and synthetic IMD data; no network, no shipped data.
- Shipped-data tests check the integrity of imdlib/data/regions.

Real IMD files are used in test_regions_real.py (marked slow).
"""
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

import imdlib as imd
from imdlib import util
from imdlib import regions
from imdlib.core import IMD
from imdlib.regions import _STATE as STATE, _DISTRICT as DISTRICT, _BASIN as BASIN
from imdlib.regions import _SUBBASIN as SUBBASIN, _NO_CELL as NO_CELL
from test_load import isolated  # noqa: F401  (autouse: isolated cache, no network)
from test_lazy import reads, put_archive_eager_names, eager, put_realtime  # noqa: F401

LAT = {'r025': np.linspace(6.5, 38.5, 129), 't100': np.linspace(7.5, 37.5, 31),
       't050': np.linspace(7.5, 37.5, 61), 'gpm': np.linspace(-30.0, 40.0, 281)}
LON = {'r025': np.linspace(66.5, 100.0, 135), 't100': np.linspace(67.5, 97.5, 31),
       't050': np.linspace(67.5, 97.5, 61), 'gpm': np.linspace(50.0, 110.0, 241)}
NLAT = {'r025': 129, 't100': 31, 't050': 61}


###############################################################################
# Synthetic region data (format of scripts/build_regions.py)
###############################################################################

# type, name, parent, aliases, cells per grid: [(ilon, ilat, fraction)]
REGIONS = [
    ('state', 'Alpha', -1, ['Old Alpha'],
     {'r025': [(40, 10, 1.0), (40, 100, 1.0), (41, 50, 0.5)], 't100': [(5, 5, 1.0)],
      't050': [(10, 10, 1.0)]}),
    ('state', 'Beta', -1, [],
     {'r025': [(60, 60, 1.0), (61, 60, 1.0)], 't100': [(8, 8, 1.0)], 't050': [(16, 16, 1.0)]}),
    ('district', 'Pune', 0, ['Poona'],
     {'r025': [(40, 50, 1.0), (41, 50, 0.5)], 't100': [(5, 5, 0.3)], 't050': [(10, 10, 0.6)]}),
    ('district', 'Bilaspur', 0, [],
     {'r025': [(42, 50, 1.0)], 't100': [(6, 5, 1.0)], 't050': [(11, 10, 1.0)]}),
    ('district', 'Bilaspur', 1, [],
     {'r025': [(60, 60, 1.0)], 't100': [(8, 8, 1.0)], 't050': [(16, 16, 1.0)]}),
    ('district', 'Gurugram', 1, ['Gurgaon'],
     {'r025': [(61, 60, 1.0)], 't100': [(8, 9, 1.0)], 't050': [(16, 17, 1.0)]}),
    ('basin', 'Godavari', -1, ['Godavari Basin'],
     {'r025': [(70, 40, 1.0), (71, 40, 1.0), (72, 41, 0.25)], 't100': [(10, 4, 1.0)],
      't050': [(20, 8, 1.0)]}),
    ('subbasin', 'Godavari Upper', 6, [],
     {'r025': [(70, 40, 1.0)], 't100': [(10, 4, 0.5)], 't050': [(20, 8, 0.5)]}),
    ('subbasin', 'Godavari Lower', 6, [],
     {'r025': [(71, 40, 1.0), (72, 41, 0.25)], 't100': [(10, 4, 0.5)], 't050': [(20, 8, 0.5)]}),
    # An alias of one district that is the official name of another (Raigad/Rayagada)
    ('district', 'Raigad', 0, ['Rayagada'],
     {'r025': [(43, 50, 1.0)], 't100': [(6, 6, 1.0)], 't050': [(12, 10, 1.0)]}),
    ('district', 'Rayagada', 1, [],
     {'r025': [(62, 60, 1.0)], 't100': [(9, 9, 1.0)], 't050': [(17, 17, 1.0)]}),
    # Districts made from the old district 'Gamma'
    ('district', 'North Gamma', 1, [],
     {'r025': [(63, 60, 1.0)], 't100': [(9, 8, 1.0)], 't050': [(18, 17, 1.0)]}),
    ('district', 'South Gamma', 1, [],
     {'r025': [(64, 60, 1.0)], 't100': [(9, 7, 1.0)], 't050': [(18, 16, 1.0)]}),
]
ORPHANS = [('Hansi', 1, 5)]           # name, state id, parent district id
SPLITS = [(1, 'Gamma', [12, 11])]     # state id, old name, districts (in LGD code order)
NOT_DISTRICT = [('Mahe', 1, 'Beta')]  # name, state id, part of
# name, state id, district id, hq, r025 cell (ilon, ilat), aliases (text or (text, rank))
CITIES = [
    ('Gurgaon', 0, 3, 0, (42, 50), []),
    ('Gurugram', 1, 5, 1, (61, 60), ['Gurgaon']),
    ('Pune', 0, 2, 1, (40, 50), ['Poona']),
    ('Pure', 0, 2, 0, (40, 50), []),
    ('Rampur', 0, 2, 0, (41, 50), []),
    ('Rampur', 0, 3, 0, (42, 50), []),
    ('Rampur', 1, 5, 1, (61, 60), []),
    ('Rampur', 1, 4, 0, (60, 60), ['Rampur Kalan']),
    ('Rampur', 1, 4, 0, (60, 60), []),
    ('Shoreville', 1, 4, 0, (60, 60), []),
    ('Seaview', 0, 2, 0, (40, 50), ['Shoreville']),
    ('Mahe', 1, 4, 1, (60, 60), []),
    # A capital named like another capital's other name (Delhi, New Delhi)
    ('Old Town', 1, 5, 2, (61, 60), []),
    ('Capitol', 1, 5, 2, (61, 60), ['Old Town']),
    # An HQ whose former name is the name of another HQ (Aurangabad)
    ('Newtown', 1, 4, 1, (60, 60), [('Oldtown', 1)]),
    ('Oldtown', 0, 3, 1, (42, 50), []),
    ('Island', 1, 4, 0, (60, 60), []),
    # A capital's old name that is also the name of a town and of a village (Jaipur, Jeypore)
    ('Pinkcity', 0, 2, 2, (40, 50), ['Jeypore']),
    ('Jeypore', 1, 5, 0, (61, 60), []),
    ('Jeypore', 0, 3, 0, (42, 50), []),
    # Two towns of one name, and a village of that name
    ('Twinton', 0, 2, 0, (40, 50), []),
    ('Twinton', 1, 4, 0, (60, 60), []),
    ('Twinton', 1, 5, 0, (61, 60), []),
    # A town known by an old name that is also a village's name (Rajahmundry)
    ('Bigtown', 1, 4, 0, (60, 60), ['Oldbig']),
    ('Oldbig', 0, 2, 0, (40, 50), []),
    # A village's name and another village's alternate name: one level
    ('Hamlet', 0, 2, 0, (40, 50), []),
    ('Smallvil', 1, 5, 0, (61, 60), ['Hamlet']),
    # Two villages of one name in one state
    ('Samename', 0, 2, 0, (40, 50), []),
    ('Samename', 0, 3, 0, (42, 50), []),
]
# Towns (15,000+ people or administrative centres): (name, state id, district id)
TOWNS = {('Jeypore', 1, 5), ('Twinton', 0, 2), ('Twinton', 1, 4), ('Bigtown', 1, 4),
         ('Shoreville', 1, 4)}
# t100 cells: all at (5, 5) except places outside the grid
T100_NONE = {'Island'}


def write_synthetic(folder):
    types = list(regions._TYPES)
    norm = regions._normalise
    reg = {'reg_type': np.array([types.index(r[0]) for r in REGIONS], np.uint8),
           'reg_name': np.array([r[1] for r in REGIONS]),
           'reg_parent': np.array([r[2] for r in REGIONS], np.int16),
           'reg_code': np.array([''] * len(REGIONS)),
           'reg_area_km2': np.ones(len(REGIONS))}
    keys = []
    for k, (t, name, _, aliases, _) in enumerate(REGIONS):
        keys.append((types.index(t), norm(name), k, ''))
        keys += [(types.index(t), norm(a), k, a) for a in aliases]
    keys.sort()
    reg.update(key_type=np.array([x[0] for x in keys], np.uint8),
               key_text=np.array([x[1] for x in keys]),
               key_region=np.array([x[2] for x in keys], np.int32),
               key_alias=np.array([x[3] for x in keys]),
               split_state=np.array([x[0] for x in SPLITS], np.int16),
               split_key=np.array([norm(x[1]) for x in SPLITS]),
               split_text=np.array([x[1] for x in SPLITS]),
               split_ptr=np.cumsum([0] + [len(x[2]) for x in SPLITS]).astype(np.int32),
               split_region=np.array([r for x in SPLITS for r in x[2]], np.int16),
               orphan_name=np.array([o[0] for o in ORPHANS]),
               orphan_key=np.array([norm(o[0]) for o in ORPHANS]),
               orphan_state=np.array([o[1] for o in ORPHANS], np.int16),
               orphan_parent=np.array([o[2] for o in ORPHANS], np.int16),
               orphan_code=np.array(['792']),
               notdist_name=np.array([x[0] for x in NOT_DISTRICT]),
               notdist_key=np.array([norm(x[0]) for x in NOT_DISTRICT]),
               notdist_state=np.array([x[1] for x in NOT_DISTRICT], np.int16),
               notdist_part=np.array([x[2] for x in NOT_DISTRICT]))
    for g in ('r025', 't100', 't050'):
        ptr, cell, frac = [0], [], []
        for r in REGIONS:
            for i, j, f in r[4][g]:
                cell.append(i * NLAT[g] + j)
                frac.append(f)
            ptr.append(len(cell))
        reg[g + '_ptr'] = np.array(ptr, np.int32)
        reg[g + '_cell'] = np.array(cell, np.uint16)
        reg[g + '_frac'] = np.array(frac, np.float32)
        reg[g + '_status'] = np.zeros(len(REGIONS), np.uint8)
    np.savez_compressed(os.path.join(folder, 'regions.npz'), **reg)

    def blob(strings):
        return np.frombuffer('\n'.join(strings).encode('utf-8'), np.uint8)
    cities = sorted(CITIES, key=lambda c: (norm(c[0]), -c[3]))
    keys = [norm(c[0]) for c in cities]
    other = [k for k, c in enumerate(cities) if c[0].lower() != keys[k]]
    r025 = np.array([c[4][0] * 129 + c[4][1] for c in cities], np.uint16)
    alts = sorted((norm(a if isinstance(a, str) else a[0]), k,
                   a if isinstance(a, str) else a[0], 0 if isinstance(a, str) else a[1])
                  for k, c in enumerate(cities) for a in c[5])
    np.savez_compressed(
        os.path.join(folder, 'cities.npz'),
        name=blob([c[0] for c in cities]),
        xkey_idx=np.array(other, np.int32), xkey=blob([keys[k] for k in other]),
        state=np.array([c[1] for c in cities], np.uint8),
        district=np.array([c[2] for c in cities], np.uint16),
        hq=np.array([c[3] for c in cities], np.uint8),
        town=np.array([c[:3] in TOWNS for c in cities], np.uint8),
        cell_gpm=r025, r025_idx=np.zeros(0, np.int32), r025_cell=np.zeros(0, np.uint16),
        cell_t100=np.array([NO_CELL if c[0] in T100_NONE else 5 * 31 + 5 for c in cities],
                           np.uint16),
        cell_t050=np.full(len(cities), 10 * 61 + 10, np.uint16),
        alt_key=blob([a[0] for a in alts]), alt_text=blob([a[2] for a in alts]),
        alt_city=np.array([a[1] for a in alts], np.int32),
        alt_rank=np.array([a[3] for a in alts], np.uint8))
    with open(os.path.join(folder, 'meta.json'), 'w') as f:
        f.write('{"sources": {"names": {"description": "test", '
                '"date": "2026-01-01"}}, "counts": {"states": 2}}')


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    folder = tmp_path / 'regions'
    folder.mkdir()
    write_synthetic(str(folder))
    monkeypatch.setattr(regions, '_DATA_DIR', str(folder))
    monkeypatch.setattr(regions, '_cache', {})
    return folder


def grid_obj(grid='r025', days=3, cat='rain', seed=0, mask=True, start='2020-01-01'):
    """Eager IMD object with random values on an IMD grid."""
    rng = np.random.default_rng(seed)
    lon, lat = LON[grid], LAT[grid]
    data = rng.gamma(0.5, 5.0, (days, len(lon), len(lat)))
    land_mask = np.ones((len(lon), len(lat)), bool) if mask else None
    end = (pd.Timestamp(start) + pd.Timedelta(days=days - 1)).strftime('%Y-%m-%d')
    return IMD(data, cat, start, end, days, lat.copy(), lon.copy(), land_mask)


def expected(obj, cells, grid='r025', off=(0, 0)):
    """Weighted mean of (ilon, ilat, fraction) cells of the object, computed directly."""
    lat0, d = {'r025': (6.5, 0.25), 't100': (7.5, 1.0), 't050': (7.5, 0.5)}[grid]
    num = np.zeros(obj.data.shape[0])
    den = np.zeros(obj.data.shape[0])
    for i, j, f in cells:
        w = f * np.cos(np.deg2rad(lat0 + d * j))
        v = obj.data[:, i + off[0], j + off[1]]
        ok = np.isfinite(v)
        if obj.land_mask is not None:
            ok &= obj.land_mask[i + off[0], j + off[1]]
        num += np.where(ok, v, 0) * w
        den += ok * w
    with np.errstate(invalid='ignore'):
        return np.where(den > 0, num / den, np.nan)


def cells_of(name):
    return [r for r in REGIONS if r[1] == name][0][4]


###############################################################################
# Weights
###############################################################################

def test_fraction_of_half_a_cell():
    shapely = pytest.importorskip('shapely')
    lon_e = np.arange(5) * 1.0
    y_e = np.sin(np.deg2rad(np.arange(5) * 1.0 + 10))
    # Left half of cell (1, 2) in x = lon, y = sin(lat)
    square = shapely.box(1.0, y_e[2], 1.5, y_e[3])
    i, j, f = regions._cell_fractions(square, lon_e, y_e)
    assert (i.tolist(), j.tolist()) == ([1], [2])
    assert f[0] == pytest.approx(0.5, abs=1e-12)
    # A square over four cells, a quarter of each
    sq = shapely.box(1.5, (y_e[1] + y_e[2]) / 2, 2.5, (y_e[2] + y_e[3]) / 2)
    i, j, f = regions._cell_fractions(sq, lon_e, y_e)
    assert sorted(zip(i, j)) == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert np.allclose(f, 0.25)
    # Fully covered cells
    i, j, f = regions._cell_fractions(shapely.box(-1, -1, 9, 1), lon_e, y_e)
    assert len(f) == 16 and np.all(f == 1.0)


def test_cos_lat_weighting(synthetic):
    data = grid_obj(days=2)
    data.data[:, 40, 10] = 1.0       # lat 9.0
    data.data[:, 40, 100] = 2.0      # lat 31.5
    data.data[:, 41, 50] = 3.0       # lat 19.0, half a cell
    w = np.cos(np.deg2rad([9.0, 31.5, 19.0])) * [1, 1, 0.5]
    out = data.region(state='Alpha')
    assert list(out.columns) == ['Alpha']
    assert np.allclose(out['Alpha'], np.sum(w * [1, 2, 3]) / np.sum(w), rtol=1e-14)


def test_nan_mask_and_sentinel_renormalise(synthetic):
    data = grid_obj(days=4)
    data.data[1, 41, 50] = np.nan          # NaN on day 2
    data.data[2, 40, 50] = -999.0          # sentinel on day 3
    data.data[3, :, :] = -999.0            # nothing on day 4
    out = data.region(district='Pune')['Pune (Alpha)'].values
    v = data.data
    assert out[0] == pytest.approx((v[0, 40, 50] + 0.5 * v[0, 41, 50]) / 1.5, rel=1e-14)
    assert out[1] == pytest.approx(v[1, 40, 50], rel=1e-14)
    assert out[2] == pytest.approx(v[2, 41, 50], rel=1e-14)
    assert np.isnan(out[3])
    # Masked cell
    data.land_mask[40, 50] = False
    assert data.region(district='Pune').iloc[0, 0] == pytest.approx(v[0, 41, 50], rel=1e-14)
    data.land_mask[41, 50] = False
    assert data.region(district='Pune').isna().all().all()


def test_temperature_sentinel_and_daily_index(synthetic):
    data = grid_obj('t100', days=3, cat='tmax', mask=False)
    data.data[:, 0, 0] = 99.9                  # corner: sentinel of temperature files
    data.data[1, 5, 5] = 99.9
    out = data.region(district='Pune')
    assert out.index.equals(pd.date_range('2020-01-01', periods=3))
    assert out.iloc[0, 0] == pytest.approx(data.data[0, 5, 5])
    assert np.isnan(out.iloc[1, 0])


def test_region_does_not_modify_data(synthetic):
    data = grid_obj()
    before = (data.data.copy(), data.land_mask.copy(), data.lat_array.copy(),
              data.lon_array.copy())
    out = data.region(state=['Alpha', 'Beta'])
    assert isinstance(out, pd.DataFrame) and out.shape == (3, 2)
    for a, b in zip(before, (data.data, data.land_mask, data.lat_array, data.lon_array)):
        assert np.array_equal(a, b)
    assert data.computed is False


###############################################################################
# Selecting regions
###############################################################################

def test_lists_and_by_column_order(synthetic):
    data = grid_obj()
    out = data.region(district=['Gurugram', 'Bilaspur'], state='Beta')
    assert list(out.columns) == ['Gurugram (Beta)', 'Bilaspur (Beta)']
    out = data.region(district=['Gurugram', 'Pune'])
    assert list(out.columns) == ['Gurugram (Beta)', 'Pune (Alpha)']
    out = data.region(state='Alpha', by='district')
    assert list(out.columns) == ['Bilaspur (Alpha)', 'Pune (Alpha)', 'Raigad (Alpha)']
    out = data.region(state=['Beta', 'Alpha'], by='district')
    assert list(out.columns) == ['Bilaspur (Beta)', 'Gurugram (Beta)', 'North Gamma (Beta)',
                                 'Rayagada (Beta)', 'South Gamma (Beta)', 'Bilaspur (Alpha)',
                                 'Pune (Alpha)', 'Raigad (Alpha)']
    out = data.region(basin='Godavari', by='subbasin')
    assert list(out.columns) == ['Godavari Lower', 'Godavari Upper']
    # Same region twice: one column
    assert list(data.region(district=['Pune', 'Poona']).columns) == ['Pune (Alpha)']
    by_sub = data.region(basin='Godavari', by='subbasin')
    lower = expected(data, cells_of('Godavari Lower')['r025'])
    assert np.allclose(by_sub['Godavari Lower'], lower, rtol=1e-14)
    by_dist = data.region(state='Alpha', by='district')
    assert np.allclose(by_dist['Bilaspur (Alpha)'], data.data[:, 42, 50], rtol=1e-14)


def test_narrowing_in_any_order(synthetic):
    data = grid_obj()
    a = data.region(district='Bilaspur', state='Beta')
    b = data.region(state='Beta', district='Bilaspur')
    assert list(a.columns) == list(b.columns) == ['Bilaspur (Beta)']
    assert a.equals(b)
    c = data.region(city='Rampur', district='Bilaspur', state='Alpha')
    d = data.region(state='Alpha', city='Rampur', district='Bilaspur')
    assert c.equals(d) and list(c.columns) == ['Rampur (Alpha)']
    assert np.allclose(c.iloc[:, 0], data.data[:, 42, 50])


def test_names_case_alias_and_official_columns(synthetic):
    data = grid_obj()
    for name in ('gurgaon', 'GURGAON', 'Gurgaon', ' Gurugram ', 'GURUGRAM'):
        assert list(data.region(district=name).columns) == ['Gurugram (Beta)']
    with pytest.raises(imd.RegionNotFoundError, match="Did you mean: Gurugram"):
        data.region(district='guru-gram')
    assert list(data.region(state='old alpha').columns) == ['Alpha']
    assert list(data.region(basin='Godavari basin').columns) == ['Godavari']


def test_errors_did_you_mean_and_ambiguity(synthetic):
    data = grid_obj()
    with pytest.raises(imd.RegionNotFoundError,
                       match=r"^No district named 'Pume'\. Did you mean: Pune \(Alpha\)\?$"):
        data.region(district='Pume')
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^'Bilaspur' is a district in Alpha and Beta\. Add state=, e\.g\. "
            r"region\(district='Bilaspur', state='Beta'\)\.$")):
        data.region(district='Bilaspur')
    with pytest.raises(imd.RegionNotFoundError, match=(
            r"^Hansi \(Beta\) is newer than the Survey of India boundaries used here\. "
            r"It lies within Gurugram: use district='Gurugram'\.$")):
        data.region(district='Hansi')
    with pytest.raises(imd.RegionNotFoundError, match=r"No district named 'Pune' in Beta\. "
                                                      r"Found: Pune \(Alpha\)\."):
        data.region(district='Pune', state='Beta')
    with pytest.raises(imd.RegionNotFoundError,
                       match=r"No state named 'Alpah'\. Did you mean: Alpha"):
        data.region(state='Alpah')
    with pytest.raises(imd.RegionNotFoundError, match=r"^No subbasin named 'Nile'\.$"):
        data.region(subbasin='Nile')
    # Error classes
    assert issubclass(imd.RegionNotFoundError, imd.RegionError)
    assert issubclass(imd.AmbiguousRegionError, imd.RegionError)
    assert issubclass(imd.RegionError, ValueError)


@pytest.mark.parametrize('kwargs', [
    {}, {'basin': 'Godavari', 'subbasin': 'Godavari Upper'},
    {'state': 'Alpha', 'basin': 'Godavari'}, {'city': 'Pune', 'subbasin': 'Godavari Upper'},
    {'shapefile': 'x.shp', 'state': 'Alpha'},
])
def test_exactly_one_type(synthetic, kwargs):
    with pytest.raises(TypeError, match=r"^Give exactly one of state=, district=, city=, basin=, "
                                        r"subbasin=, shapefile=\.$"):
        grid_obj().region(**kwargs)


@pytest.mark.parametrize('kwargs', [
    {'district': 'Pune', 'by': 'district'}, {'state': 'Alpha', 'by': 'subbasin'},
    {'basin': 'Godavari', 'by': 'district'}, {'city': 'Pune', 'by': 'city'},
    {'subbasin': 'Godavari Upper', 'by': 'subbasin'},
])
def test_invalid_by(synthetic, kwargs):
    with pytest.raises(ValueError, match=r"cannot be used with"):
        grid_obj().region(**kwargs)


def test_bad_name_type(synthetic):
    with pytest.raises(TypeError, match="must be a name or a list of names"):
        grid_obj().region(district=5)
    with pytest.raises(TypeError, match="must be a name or a list of names"):
        grid_obj().region(district=[])


def imdlib_frames(error):
    """(file name, function) of the traceback frames inside the imdlib package."""
    import traceback
    package = os.path.dirname(os.path.abspath(imd.__file__))
    return [(os.path.basename(f.filename), f.name) for f in traceback.extract_tb(error.__traceback__)
            if os.path.abspath(f.filename).startswith(package + os.sep)]


@pytest.mark.parametrize('call, public', [
    (lambda: grid_obj().region(district='Pume'), ('core.py', 'region')),
    (lambda: grid_obj().region(district='Bilaspur'), ('core.py', 'region')),
    (lambda: grid_obj().region(city='Rampur', state='Alpha'), ('core.py', 'region')),
    (lambda: grid_obj().region(city='Pume'), ('core.py', 'region')),
    (lambda: grid_obj().region(district=5), ('core.py', 'region')),
    (lambda: grid_obj().region(), ('core.py', 'region')),
    (lambda: grid_obj().region(state='Alpha', by='city'), ('core.py', 'region')),
    (lambda: imd.regions.search(''), ('regions.py', 'search')),
    (lambda: imd.regions.search('Pune', state='Alpah'), ('regions.py', 'search')),
    (lambda: imd.regions.list('district', state='Alpah'), ('regions.py', 'list')),
    (lambda: imd.regions.list('city'), ('regions.py', 'list')),
])
def test_errors_come_from_the_public_call(synthetic, call, public):
    with pytest.raises((imd.RegionError, TypeError, ValueError)) as e:
        call()
    # Only the frame of the public function, none of the internal helpers
    assert imdlib_frames(e.value) == [public]
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_other_errors_keep_their_traceback(synthetic, monkeypatch):
    def broken(*args):
        raise KeyError('internal')
    monkeypatch.setattr(regions, '_weighted_means', broken)
    with pytest.raises(KeyError) as e:
        grid_obj().region(state='Alpha')
    assert imdlib_frames(e.value) == [('core.py', 'region'), ('regions.py', '_region')]


###############################################################################
# Cities
###############################################################################

def test_city_clear_winner(synthetic):
    data = grid_obj()
    # One of three Rampurs is a district HQ
    out = data.region(city='Rampur')
    assert list(out.columns) == ['Rampur (Beta)']
    assert np.allclose(out.iloc[:, 0], data.data[:, 61, 60])
    # Old name of an HQ beats a village of that name
    assert list(data.region(city='Gurgaon').columns) == ['Gurugram (Beta)']
    assert list(data.region(city='Gurgaon', state='Alpha').columns) == ['Gurgaon (Alpha)']
    assert list(data.region(city='poona').columns) == ['Pune (Alpha)']
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Rampur' \(Rampur \(Bilaspur, Alpha\), "
            r"Rampur \(Pune, Alpha\)\)\. Add district=, or see "
            r"imd\.regions\.search\('Rampur'\)\.$")):
        data.region(city='Rampur', state='Alpha')
    # Places in one district cannot be told apart
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Rampur' in Bilaspur \(Beta\) cannot be told apart\. Use "
            r"region\(district='Bilaspur'\) for the district, or see "
            r"imd\.regions\.search\('Rampur'\)\.$")):
        data.region(city=['Rampur'], state='Beta', district='Bilaspur')
    out = data.region(city='Rampur', district='Pune')
    assert np.allclose(out.iloc[:, 0], data.data[:, 41, 50])
    with pytest.raises(imd.RegionNotFoundError, match=(r"^No city named 'Shoreville' in "
                                                       r"Gurugram \(Beta\)\. See imd\.regions")):
        data.region(city='Shoreville', district='Gurugram')
    with pytest.raises(imd.RegionNotFoundError, match=r"^No city named 'Pune' in Beta\."):
        data.region(city='Pune', state='Beta')
    # Did you mean: district HQs and state capitals first (difflib alone puts Pure first)
    with pytest.raises(imd.RegionNotFoundError, match=r"^No city named 'Pume'\. Did you mean: "
                                                      r"Pune \(Alpha\), Pure \(Alpha\)\?"):
        data.region(city='Pume')
    with pytest.raises(imd.RegionNotFoundError, match=r"^No city named 'Xyz'\. See imd\.regions"):
        data.region(city='Xyz')


def test_city_columns_with_same_name(synthetic):
    data = grid_obj()
    out = data.region(city=['Shoreville', 'Rampur'])
    assert list(out.columns) == ['Shoreville (Beta)', 'Rampur (Beta)']
    # Two places with the same column name: the district is added
    out = data.region(city=['Rampur', 'Rampur Kalan'])
    assert list(out.columns) == ['Rampur (Gurugram, Beta)', 'Rampur (Bilaspur, Beta)']
    a = data.region(city='Rampur', district='Pune', state='Alpha').iloc[:, 0]
    b = data.region(city='Rampur', district='Bilaspur', state='Alpha').iloc[:, 0]
    assert not np.allclose(a, b)


def test_city_on_other_grids(synthetic):
    t = grid_obj('t100', cat='tmax', mask=False)
    t.data[:, 0, 0] = 99.9
    assert np.allclose(t.region(city='Pune').iloc[:, 0], t.data[:, 5, 5])
    g = grid_obj('gpm', cat='rain_gpm', mask=False)
    assert np.allclose(g.region(city='Pune').iloc[:, 0], g.data[:, 40 + 66, 50 + 146])


def test_official_name_beats_alias(synthetic):
    data = grid_obj()
    # 'Rayagada' is the official name of a district in Beta and an alias of Raigad (Alpha)
    assert list(data.region(district='Rayagada').columns) == ['Rayagada (Beta)']
    assert list(data.region(district='Rayagada', state='Alpha').columns) == ['Raigad (Alpha)']
    assert list(data.region(district='Raigad').columns) == ['Raigad (Alpha)']


def test_names_of_split_districts(synthetic):
    data = grid_obj()
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^'Gamma' matches several districts: South Gamma, North Gamma \(Beta\)\. "
            r"Use one of these names\.$")):
        data.region(district='Gamma')
    with pytest.raises(imd.AmbiguousRegionError, match="matches several districts"):
        data.region(district='gamma', state='Beta')
    with pytest.raises(imd.RegionNotFoundError, match="No district named 'Gamma' in Alpha"):
        data.region(district='Gamma', state='Alpha')
    assert list(data.region(district='North Gamma').columns) == ['North Gamma (Beta)']


def test_not_a_district(synthetic):
    data = grid_obj()
    message = (r"^Mahe is not a district in the official boundaries; it is part of Beta\. "
               r"For Mahe itself use city='Mahe'\.$")
    with pytest.raises(imd.RegionNotFoundError, match=message):
        data.region(district='Mahe')
    with pytest.raises(imd.RegionNotFoundError, match=message):
        data.region(district='mahe', state='Beta')
    assert list(data.region(city='Mahe').columns) == ['Mahe (Beta)']


def test_city_names_and_other_names(synthetic):
    data = grid_obj()
    # Two capitals: the one with this name, not the one with it as an alternate name
    assert list(data.region(city='Old Town').columns) == ['Old Town (Beta)']
    assert list(data.region(city='Capitol').columns) == ['Capitol (Beta)']
    # A town with this name before a village with it as an alternate name
    assert list(data.region(city='Shoreville').columns) == ['Shoreville (Beta)']
    assert list(data.region(city='Shoreville', state='Alpha').columns) == ['Seaview (Alpha)']
    # Another name of an HQ (e.g. its former name) counts as a name: two HQs fit
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^'Oldtown' fits 2 district HQs or state capitals: Oldtown \(Alpha\) and "
            r"Newtown \(Beta\)\. Add state=, or see imd\.regions\.search\('Oldtown'\)\.$")):
        data.region(city='Oldtown')
    assert list(data.region(city='Oldtown', state='Beta').columns) == ['Newtown (Beta)']


def test_city_order_of_preference(synthetic):
    """
    HQ/capital by name, then a town by name, then an HQ/capital by an old
    name, then a town by an old name, then any other place.
    """
    data = grid_obj()
    # A town's name beats a capital's old name (Jeypore, not Jaipur) and a village
    assert list(data.region(city='Jeypore').columns) == ['Jeypore (Beta)']
    # A capital's old name beats a village's name
    assert list(data.region(city='Jeypore', state='Alpha').columns) == ['Pinkcity (Alpha)']
    assert list(data.region(city='Pinkcity').columns) == ['Pinkcity (Alpha)']
    # Two towns of one name: ambiguous (the village is not listed)
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Twinton' \(Twinton \(Pune, Alpha\), Twinton \(Bilaspur, Beta\)\)\. "
            r"Add state=, or see imd\.regions\.search\('Twinton'\)\.$")):
        data.region(city='Twinton')
    assert list(data.region(city='Twinton', state='Beta').columns) == ['Twinton (Beta)']
    out = data.region(city='Twinton', state='Beta')
    assert np.allclose(out.iloc[:, 0], data.data[:, 60, 60])
    # A town's old name beats a village's name
    assert list(data.region(city='Oldbig').columns) == ['Bigtown (Beta)']
    assert list(data.region(city='Oldbig', state='Alpha').columns) == ['Oldbig (Alpha)']
    # Villages: their names and alternate names are one level
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Hamlet' \(Hamlet \(Pune, Alpha\), Smallvil \(Gurugram, Beta\)\)\. "
            r"Add state=, or see imd\.regions\.search\('Hamlet'\)\.$")):
        data.region(city='Hamlet')
    # Places of one state: district= tells them apart
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Samename' \(.*\)\. "
            r"Add district=, or see imd\.regions\.search\('Samename'\)\.$")):
        data.region(city='Samename')
    level = regions._city_level
    assert [level(1, 0, regions._NAME), level(0, 1, regions._NAME),
            level(2, 0, regions._ALTERNATE), level(0, 1, regions._ALTERNATE),
            level(0, 0, regions._NAME), level(0, 0, regions._ALTERNATE)] == [
        regions._HQ_NAME, regions._TOWN_NAME, regions._HQ_OLD_NAME, regions._TOWN_OLD_NAME,
        regions._OTHER_PLACE, regions._OTHER_PLACE]
    assert regions._HQ_NAME < regions._TOWN_NAME < regions._HQ_OLD_NAME \
        < regions._TOWN_OLD_NAME < regions._OTHER_PLACE


def test_city_without_a_cell(synthetic):
    t = grid_obj('t100', days=2, cat='tmax', mask=False)
    out = t.region(city=['Pune', 'Island'])
    assert list(out.columns) == ['Pune (Alpha)', 'Island (Beta)']
    assert np.allclose(out.iloc[:, 0], t.data[:, 5, 5]) and out.iloc[:, 1].isna().all()
    out = t.region(city='Island')
    assert out.shape == (2, 1) and out.isna().all().all()
    # On the 0.25 degree grid the place has a cell
    assert grid_obj().region(city='Island').notna().all().all()


def test_missing_values(synthetic):
    """Rain -999, temperature 99.9 (as typed or as float32); not the corner value."""
    t = grid_obj('t050', days=4, cat='tmax', mask=False)
    t.data[:, 0, 0] = 99.9
    t.data[1, 10, 10] = 99.9
    t.data[2, 10, 10] = np.float32(99.9)
    pune = t.region(district='Pune').iloc[:, 0]
    assert pune.isna().tolist() == [False, True, True, False]
    assert np.allclose(pune.iloc[[0, 3]], t.data[[0, 3], 10, 10], rtol=1e-14)
    # Clipped: the corner is a value, not the missing value
    clip = t.copy()
    clip.data = clip.data[:, 5:30, 5:30].copy()
    clip.lon_array, clip.lat_array = clip.lon_array[5:30], clip.lat_array[5:30]
    clip.data[:, 0, 0] = clip.data[3, 5, 5]                # equal to Pune on day 4
    assert clip.region(district='Pune').equals(t.region(district='Pune'))
    # Rain: -999 only
    r = grid_obj(days=2)
    r.data[0, 42, 50] = -999.0
    r.data[1, 42, 50] = 99.9
    out = r.region(district='Bilaspur', state='Alpha').iloc[:, 0]
    assert np.isnan(out.iloc[0]) and out.iloc[1] == 99.9


###############################################################################
# Grids: GPM offsets, clipped windows, remapped data
###############################################################################

def test_grid_table():
    # Coordinates are bit-identical to the linspace literals the readers used before
    for key, grid in util.GRIDS.items():
        assert np.array_equal(grid.lat, LAT[key]) and grid.lat.dtype == LAT[key].dtype, key
        assert np.array_equal(grid.lon, LON[key]) and grid.lon.dtype == LON[key].dtype, key
    assert util.ARCHIVE_GRIDS == {'rain': (129, 135), 'tmin': (31, 31), 'tmax': (31, 31)}
    assert util.REALTIME_GRIDS == {'rain': (129, 135), 'rain_gpm': (281, 241),
                                   'tmin': (61, 61), 'tmax': (61, 61)}
    assert util.GRIDS['r025'].file_size(365) == 365 * 129 * 135 * 4
    assert util.GRIDS['gpm'].file_size() == 281 * 241 * 4
    assert regions._GPM_OFFSET == (66, 146)
    gpm, r025 = util.GRIDS['gpm'], util.GRIDS['r025']
    assert np.array_equal(gpm.lon[66:66 + 135], r025.lon)
    assert np.array_equal(gpm.lat[146:146 + 129], r025.lat)


@pytest.mark.parametrize('src, var, key', [('archive', 'rain', 'r025'), ('archive', 'tmax', 't100'),
                                           ('realtime', 'rain', 'r025'),
                                           ('realtime', 'tmin', 't050'),
                                           ('realtime', 'rain_gpm', 'gpm')])
def test_identify_grid(src, var, key):
    grid = util.GRIDS[util.ARCHIVE_GRID[var] if src == 'archive' else util.REALTIME_GRID[var]]
    assert grid is util.GRIDS[key]
    obj = IMD(None, var, '2020-01-01', '2020-01-01', 1, grid.lat, grid.lon)
    assert util.identify_grid(obj) == (key, 0, 0)
    box = IMD(None, var, '2020-01-01', '2020-01-01', 1, grid.lat[3:7], grid.lon[2:5])
    assert util.identify_grid(box) == (key, 2, 3)
    with pytest.raises(ValueError, match=r'^clip\(\) needs data on an IMD grid'):
        util.identify_grid(IMD(None, var, '2020-01-01', '2020-01-01', 1, grid.lat + 0.1,
                               grid.lon), 'clip')


def test_gpm_offsets(synthetic):
    g = grid_obj('gpm', cat='rain_gpm', mask=False)
    out = g.region(state=['Alpha', 'Beta'])
    for name in ('Alpha', 'Beta'):
        assert np.allclose(out[name], expected(g, cells_of(name)['r025'], off=(66, 146)),
                           rtol=1e-14)


def test_real_time_temperature_grid(synthetic):
    t = grid_obj('t050', cat='tmax', mask=False)
    t.data[:, 0, 0] = 99.9
    out = t.region(district='Pune')
    want = expected(t, cells_of('Pune')['t050'], 't050')
    assert np.allclose(out.iloc[:, 0], want, rtol=1e-14)


def test_clipped_window(synthetic):
    full = grid_obj(days=2)
    clip = full.copy()
    i0, i1, j0, j1 = 35, 50, 40, 60
    clip.data = clip.data[:, i0:i1, j0:j1]
    clip.land_mask = clip.land_mask[i0:i1, j0:j1]
    clip.lon_array = clip.lon_array[i0:i1]
    clip.lat_array = clip.lat_array[j0:j1]
    a = clip.region(district=['Pune', 'Bilaspur'], state='Alpha')
    b = full.region(district=['Pune', 'Bilaspur'], state='Alpha')
    assert np.allclose(a.values, b.values, rtol=1e-14)
    assert np.allclose(clip.region(city='Pune').values, full.data[:, 40, 50][:, None])
    with pytest.raises(ValueError, match=r"^Alpha extends beyond this data's extent "
                                         r"\(was it clipped\?\)\.$"):
        clip.region(state='Alpha')
    with pytest.raises(ValueError, match=r"^Gurugram \(Beta\) is outside this data's extent"):
        clip.region(city='Gurgaon')


def test_remapped_grid_error(synthetic):
    t = grid_obj('t100', days=1, cat='tmax', mask=False)
    t.remap(0.3)
    with pytest.raises(ValueError, match=(r"^region\(\) needs data on an IMD grid "
                                          r"\(0\.25°, 0\.5°, 1\.0°, GPM 0\.25°\)\. "
                                          r"For other grids use region\(shapefile=\.\.\.\)\.$")):
        t.region(district='Pune')


###############################################################################
# Lazy, eager and computed objects
###############################################################################

def test_lazy_equals_eager_and_stays_unread(synthetic, isolated, reads):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    lazy = imd.load('rain', 2019, 2020, offline=True)
    full = eager(isolated, 'rain', 2019, 2020)
    kwargs = [dict(state=['Alpha', 'Beta']), dict(district='Pune'), dict(city='Rampur'),
              dict(basin='Godavari', by='subbasin')]
    for kw in kwargs:
        cells = reads['cells']
        a = lazy.region(**kw)
        # One read of the files: the land mask is built from the values read
        assert reads['cells'] == cells + 1
        b = full.region(**kw)
        assert a.equals(b)
    assert reads['full'] == 0
    assert lazy._data_pending
    # A period that is not whole years
    lazy = imd.load('rain', '2019-12-20', '2020-01-10', offline=True)
    full = eager(isolated, 'rain', '2019-12-20', '2020-01-10')
    assert lazy.region(state='Alpha').equals(full.region(state='Alpha'))
    assert len(full.region(state='Alpha')) == 22


def test_lazy_temperature_and_realtime(synthetic, isolated, tmp_path, reads):
    put_archive_eager_names(isolated, 'tmax', [2020])
    a = imd.load('tmax', 2020, offline=True).region(district=['Pune', 'Gurugram'])
    b = eager(isolated, 'tmax', 2020, 2020).region(district=['Pune', 'Gurugram'])
    assert a.equals(b) and a.notna().all().all()
    days = list(pd.date_range('2026-10-03', '2026-10-05'))
    for var in ('rain', 'rain_gpm', 'tmax'):
        rt = put_realtime(isolated, var, days, tmp_path)
        a = imd.load(var, '2026-10-03', '2026-10-05', source='realtime', offline=True)
        b = imd.open_real_data(var, '2026-10-03', '2026-10-05', rt)
        assert a.region(state='Alpha').equals(b.region(state='Alpha'))
        assert a.region(city='Pune').equals(b.region(city='Pune'))
    assert reads['full'] == 0


def test_computed_object(synthetic, isolated):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    data = eager(isolated, 'rain', 2019, 2020)
    annual = data.copy().compute('rxa', 'A')
    out = annual.region(district='Pune')
    assert out.index.equals(pd.date_range('2019-01-01', periods=2, freq='YE'))
    assert np.allclose(out.iloc[:, 0], expected(annual, cells_of('Pune')['r025']), rtol=1e-14)
    # Lazy object computed the same way
    lazy = imd.load('rain', 2019, 2020, offline=True).compute('rxa', 'A')
    assert lazy.region(district='Pune').equals(out)
    clim = data.copy().climatology().region(state='Beta')
    assert len(clim) == 12 and clim.index[0] == pd.Timestamp('2000-01-31')


def test_spatial_mean_unchanged():
    data = grid_obj(days=3)
    ts = data.spatial_mean()
    w = np.cos(np.deg2rad(data.lat_array))
    exp = (data.data * w).sum(axis=(1, 2)) / (w.sum() * data.data.shape[1])
    assert np.allclose(ts.iloc[:, 0], exp)
    assert ts.index.equals(pd.date_range('2020-01-01', periods=3))


###############################################################################
# shapefile=
###############################################################################

def write_shapefile(path, polygons, prj=True, field='name'):
    shapefile = pytest.importorskip('shapefile')
    with shapefile.Writer(str(path), shapeType=shapefile.POLYGON) as w:
        w.field(field, 'C')
        for name, ring in polygons:
            w.poly([ring])
            w.record(name)
    if prj:
        path.with_suffix('.prj').write_text(
            'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
            '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')


def box_ring(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon0, lat1), (lon1, lat1), (lon1, lat0), (lon0, lat0)]


def test_shapefile_region(tmp_path):
    pytest.importorskip('shapely')
    data = grid_obj(days=3)
    lon, lat = data.lon_array, data.lat_array
    # Cell (40, 50) fully, the left half of cell (41, 50) in lon
    a = box_ring(lon[40] - 0.125, lat[50] - 0.125, lon[41], lat[50] + 0.125)
    b = box_ring(lon[60] - 0.125, lat[60] - 0.125, lon[60] + 0.125, lat[60] + 0.125)
    c = box_ring(lon[61] - 0.125, lat[60] - 0.125, lon[61] + 0.125, lat[60] + 0.125)
    shp = tmp_path / 'catchment.shp'
    write_shapefile(shp, [('A', a), ('B', b), ('B', c)])
    out = data.region(shapefile=str(shp))
    assert list(out.columns) == ['catchment']
    cells = [(40, 50, 1.0), (41, 50, 0.5), (60, 60, 1.0), (61, 60, 1.0)]
    assert np.allclose(out['catchment'], expected(data, cells), rtol=1e-12)
    out = data.region(shapefile=shp, by='name')
    assert list(out.columns) == ['A', 'B']
    assert np.allclose(out['A'], expected(data, cells[:2]), rtol=1e-12)
    assert np.allclose(out['B'], expected(data, cells[2:]), rtol=1e-12)
    # Works on any regular grid, e.g. after remap()
    t = grid_obj('t100', days=1, cat='tmax', mask=False)
    t.data[:, 0, 0] = 99.9
    t.remap(0.5)
    assert t.region(shapefile=shp).notna().all().all()
    with pytest.raises(ValueError, match=r"has no field 'basin'\. Fields: name\."):
        data.region(shapefile=shp, by='basin')


def test_shapefile_errors(tmp_path):
    pytest.importorskip('shapely')
    data = grid_obj(days=1)
    shp = tmp_path / 'projected.shp'
    write_shapefile(shp, [('A', box_ring(4000000, 4000000, 4100000, 4100000))], prj=False)
    shp.with_suffix('.prj').write_text('PROJCS["LCC",GEOGCS["GCS_WGS_1984"]]')
    with pytest.raises(ValueError, match=r"not in longitude/latitude.*Reproject it to EPSG:4326"):
        data.region(shapefile=shp)
    shp.with_suffix('.prj').unlink()
    with pytest.raises(ValueError, match=r"not in longitude/latitude"):
        data.region(shapefile=shp)
    far = tmp_path / 'far.shp'
    write_shapefile(far, [('A', box_ring(10, 10, 11, 11))])
    with pytest.raises(ValueError, match=r"^far does not overlap this data's grid\.$"):
        data.region(shapefile=far)
    shapefile = pytest.importorskip('shapefile')
    pts = tmp_path / 'points.shp'
    with shapefile.Writer(str(pts), shapeType=shapefile.POINT) as w:
        w.field('name', 'C')
        w.point(77, 20)
        w.record('p')
    with pytest.raises(ValueError, match="needs polygons"):
        data.region(shapefile=pts)


def test_shapefile_without_shapely(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'shapely', None)
    with pytest.raises(ImportError, match=(r"^region\(shapefile=\.\.\.\) needs pyshp and "
                                           r"shapely: pip install pyshp shapely$")):
        grid_obj(days=1).region(shapefile=tmp_path / 'x.shp')


###############################################################################
# search, list, info
###############################################################################

def test_search(synthetic):
    s = imd.regions.search('pun')
    assert list(s.columns) == ['name', 'type', 'state', 'district', 'matched_alias']
    assert s.values.tolist()[:2] == [['Pune', 'district', 'Alpha', '', ''],
                                     ['Pune', 'city', 'Alpha', 'Pune', '']]
    s = imd.regions.search('gurgaon')
    assert s.values.tolist() == [
        ['Gurugram', 'district', 'Beta', '', 'Gurgaon'],
        ['Gurugram', 'city', 'Beta', 'Gurugram', 'Gurgaon'],     # district HQ first
        ['Gurgaon', 'city', 'Alpha', 'Bilaspur', '']]
    s = imd.regions.search('Rampur', type='city', state='Alpha')
    assert s.district.tolist() == ['Bilaspur', 'Pune']
    # Places that would give the same row are listed once
    s = imd.regions.search('Rampur', type='city', state='Beta')
    assert s.values.tolist() == [['Rampur', 'city', 'Beta', 'Gurugram', ''],     # the HQ first
                                 ['Rampur', 'city', 'Beta', 'Bilaspur', '']]
    assert imd.regions.search('godavari', type='subbasin').name.tolist() == \
        ['Godavari Lower', 'Godavari Upper']
    assert len(imd.regions.search('a', limit=3)) == 3
    s = imd.regions.search('ville')
    assert s.name.tolist() == ['Shoreville', 'Seaview']
    assert s.matched_alias.tolist() == ['', 'Shoreville']
    # Places in the order of preference of region(city=...)
    s = imd.regions.search('Oldbig', type='city')
    assert s.values.tolist() == [['Bigtown', 'city', 'Beta', 'Bilaspur', 'Oldbig'],
                                 ['Oldbig', 'city', 'Alpha', 'Pune', '']]
    with pytest.raises(ValueError, match="type must be one of"):
        imd.regions.search('x', type='town')


def test_list_and_info(synthetic):
    assert imd.regions.list('state') == ['Alpha', 'Beta']
    assert imd.regions.list('district') == ['Bilaspur', 'Bilaspur', 'Gurugram', 'North Gamma',
                                            'Pune', 'Raigad', 'Rayagada', 'South Gamma']
    assert imd.regions.list('district', state='Beta') == ['Bilaspur', 'Gurugram', 'North Gamma',
                                                          'Rayagada', 'South Gamma']
    assert imd.regions.list('subbasin', basin='godavari basin') == ['Godavari Lower',
                                                                    'Godavari Upper']
    with pytest.raises(ValueError, match="search"):
        imd.regions.list('city')
    with pytest.raises(ValueError, match="state= can only be used"):
        imd.regions.list('basin', state='Alpha')
    info = imd.regions.info()
    assert info == {'sources': {'names': {'description': 'test', 'date': '2026-01-01'}},
                    'counts': {'states': 2}}


###############################################################################
# Names
###############################################################################

@pytest.mark.parametrize('text, key', [
    ('Kāngra', 'kangra'), ('  JAMMU & KASHMIR ', 'jammu and kashmir'),
    ('Dr. B.R. Ambedkar Konaseema', 'dr b r ambedkar konaseema'),
    ("Manendragarh-Chirmiri-Bharatpur(M C B)", 'manendragarh chirmiri bharatpur m c b'),
    ("Bhalswa Jahangir Pur's", 'bhalswa jahangir pur s'), ('SOUTH SALMARA\r\n', 'south salmara'),
    ('Pūnch', 'punch'), ('Nicobars,', 'nicobars'),
])
def test_normalise(text, key):
    assert regions._normalise(text) == key


def test_build_decodes_legacy_soi_symbols():
    pytest.importorskip('geopandas')
    pytest.importorskip('pyogrio')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    'scripts'))
    try:
        import build_regions
    finally:
        sys.path.pop(0)
    assert build_regions.decode_soi('K>NGRA') == 'KāNGRA'
    assert regions._normalise(build_regions.decode_soi('K>NGRA')) == 'kangra'
    assert regions._normalise(build_regions.decode_soi('B\\DAR')) == 'bidar'
    assert regions._normalise(build_regions.decode_soi('Bengal#ru')) == 'bengaluru'
    assert regions._normalise(build_regions.decode_soi('āLīPUR DUāR')) == 'alipur duar'
    assert build_regions.decode_soi('SOUTH SALMARA\r\n') == 'SOUTH SALMARA'
    assert build_regions.display('Andaman And Nicobar Islands') == 'Andaman and Nicobar Islands'
    assert build_regions.display('The Dadra And Nagar Haveli And Daman And Diu') == \
        'The Dadra and Nagar Haveli and Daman and Diu'
    assert build_regions.display('MAUGANJ') == 'Mauganj'
    # Plain names: no accents, names in capitals in title case
    assert build_regions.display('JOR BAGH') == 'Jor Bagh'
    assert build_regions.display('JP NAGAR 1ST PHASE') == 'Jp Nagar 1st Phase'
    assert build_regions.display('Rāmpur') == 'Rampur'
    assert build_regions.display(build_regions.decode_soi('PUNCH')) == 'Punch'
    assert build_regions.display(build_regions.decode_soi('āLīPUR DUāR')) == 'Alipur Duar'
    assert build_regions.display('CBI') == 'CBI' and build_regions.display('alampur') == 'Alampur'
    assert build_regions.without_brackets('Khandwa (East Nimar)') == 'Khandwa'
    assert build_regions.without_brackets('Manendragarh-Chirmiri-Bharatpur(M C B)') == \
        'Manendragarh-Chirmiri-Bharatpur'
    assert build_regions.bracketed('Khandwa (East Nimar)') == ['East Nimar']


def test_build_uses_only_cells_next_to_a_place():
    """A place without data in its own cells uses a cell next to them, never one further away."""
    pytest.importorskip('geopandas')
    shapely = pytest.importorskip('shapely')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    'scripts'))
    try:
        import build_regions as b
    finally:
        sys.path.pop(0)
    grid = util.Grid(0.0, 0.0, 1.0, 10, 10)
    mask = np.zeros((10, 10), bool)
    mask[5, 5] = True
    data = b.DataCells(grid, mask)
    # own cell with data; next to it; two cells away; next to it; outside the grid
    lon = np.array([5.1, 4.2, 3.0, 4.4, -3.0])
    lat = np.array([5.2, 4.3, 3.0, 6.4, -3.0])
    cells, status = data.city_cells(lon, lat)
    assert cells.tolist() == [55, 55, 33, 55, NO_CELL]
    assert status.tolist() == [0, 1, 2, 1, 2]
    # Regions: own cells (3, 3), (3, 4); (4, 4) has no data either -> none next to them
    assert data.region_neighbour(np.array([3, 3]), np.array([3, 4]),
                                 shapely.box(2.6, 2.6, 3.4, 4.4)) is None
    cell, km = data.region_neighbour(np.array([4]), np.array([4]), shapely.box(3.6, 3.6, 4.4, 4.4))
    assert cell == 55 and 0 < km < 100


def test_build_city_rows_of_region_aliases():
    """A city row of region_aliases.csv adds an other name to exactly one place."""
    pytest.importorskip('geopandas')
    pytest.importorskip('pyogrio')
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    'scripts'))
    try:
        import build_regions as b
    finally:
        sys.path.pop(0)
    region_name = ['Alpha', 'Beta', 'Pune', 'Bilaspur', 'Gurugram']
    # Places sorted by key: two Rampurs in Beta (districts Bilaspur and Gurugram), one in Alpha
    city = {'keys': ['pune', 'rampur', 'rampur', 'rampur'],
            'names': ['Pune', 'Rampur', 'Rampur', 'Rampur'],
            'state': [0, 0, 1, 1], 'district': [2, 2, 3, 4],
            'aliases': [('poona', 0, 'Poona', 0)]}

    def row(name, state, alias, district=''):
        return {'type': 'city', 'name': name, 'state': state, 'district': district,
                'alias': alias, 'note': ''}
    rows = [row('Pune', 'Alpha', 'Punya Nagari'), row('Rampur', 'alpha', 'Old Rampur'),
            row('Rampur', 'Beta', 'Rampur Khas', district='Gurugram'),
            row('Pune', 'Alpha', 'Poona')]                  # already a name: left out
    assert b.hand_city_aliases(rows, city, region_name) == [
        ('punya nagari', 0, 'Punya Nagari', 0), ('old rampur', 1, 'Old Rampur', 0),
        ('rampur khas', 3, 'Rampur Khas', 0)]
    # The alias ranks as an other name: below the town's own name, above villages
    assert regions._city_level(0, 1, 0) == regions._TOWN_OLD_NAME
    with pytest.raises(SystemExit, match=r"city 'Rampur' in Beta fits 2 places: "
                                         r"Rampur \(Bilaspur, Beta\); Rampur \(Gurugram, Beta\)\. "
                                         r"Add or correct the district"):
        b.hand_city_aliases([row('Rampur', 'Beta', 'X')], city, region_name)
    with pytest.raises(SystemExit, match=r"city 'Pune' in Beta fits no place"):
        b.hand_city_aliases([row('Pune', 'Beta', 'X')], city, region_name)
    with pytest.raises(SystemExit, match=r"city 'Rampur' in Pune, Beta fits no place"):
        b.hand_city_aliases([row('Rampur', 'Beta', 'X', district='Pune')], city, region_name)
    # Rows are checked before the build
    b.check_hand_aliases([row('Pune', 'Alpha', 'X'), {'type': 'state', 'name': 'Alpha',
                                                      'state': '', 'district': '', 'alias': 'A'}])
    for bad in [row('Pune', '', 'X'), {**row('Pune', 'Alpha', 'X'), 'type': 'town'},
                {'type': 'state', 'name': 'Alpha', 'state': '', 'district': 'Pune', 'alias': 'A'}]:
        with pytest.raises(SystemExit, match="region_aliases.csv row"):
            b.check_hand_aliases([bad])


###############################################################################
# Import and shipped data
###############################################################################

def test_import_and_first_use_of_city_data():
    """Importing imdlib loads no region data; the first use of the city data is fast.
    One new Python process for both checks."""
    code = ("import numpy as np\n"
            "loaded = []\n"
            "load = np.load\n"
            "np.load = lambda *a, **k: loaded.append(a) or load(*a, **k)\n"
            "import imdlib, imdlib.regions as r\n"
            "assert r._cache == {} and loaded == [], loaded\n"
            "print('ok')\n"
            "import time\n"
            "t = time.perf_counter(); r._cities(); print(time.perf_counter() - t)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, '-c', code], cwd=root, capture_output=True, text=True)
    lines = out.stdout.split()
    assert lines[:1] == ['ok'], out.stderr
    seconds = float(lines[1])
    print('first use of city data: {:.3f} s'.format(seconds))
    assert seconds < 0.5


@pytest.fixture(scope='module')
def shipped():
    return regions._Regions(os.path.join(regions._DATA_DIR, 'regions.npz'))


@pytest.fixture(scope='module')
def shipped_raw():
    with np.load(os.path.join(regions._DATA_DIR, 'regions.npz'), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def per_cell(raw, g, k):
    ptr, cell, frac = raw[g + '_ptr'], raw[g + '_cell'], raw[g + '_frac']
    return dict(zip(cell[ptr[k]:ptr[k + 1]].tolist(), frac[ptr[k]:ptr[k + 1]].tolist()))


def cell_area_km2(g, flat):
    """Area of cells on the sphere (authalic radius of WGS84)."""
    grid = regions._GRIDS[g]
    south = grid.lat0 + grid.step * (np.asarray(flat) % grid.nlat) - grid.step / 2
    return (np.deg2rad(grid.step) * 6371.0072 ** 2 *
            (np.sin(np.deg2rad(south + grid.step)) - np.sin(np.deg2rad(south))))


def test_shipped_every_region_has_cells(shipped_raw):
    n = len(shipped_raw['reg_name'])
    assert n > 900
    for g in regions._GRIDS:
        ptr = shipped_raw[g + '_ptr']
        assert len(ptr) == n + 1 and np.all(np.diff(ptr) >= 1)
        grid = regions._GRIDS[g]
        assert shipped_raw[g + '_cell'].max() < grid.nlon * grid.nlat
        f = shipped_raw[g + '_frac']
        assert np.all((f > 1e-4) & (f <= 1.0))


def test_shipped_islands_have_no_data_cells(shipped_raw):
    """Regions without IMD data keep their own cells (NaN), never a far-away cell."""
    names = [str(n) for n in shipped_raw['reg_name']]
    for g in ('r025', 't100'):
        status = shipped_raw[g + '_status']
        for name in ('Andaman and Nicobar Islands', 'Nicobars'):
            assert status[names.index(name)] == 2, (g, name)
        assert status[names.index('Kerala')] == 0
    assert shipped_raw['r025_status'][names.index('Lakshadweep')] == 2
    # A region that uses a cell next to it uses exactly one cell
    for g in regions._GRIDS:
        ptr = shipped_raw[g + '_ptr']
        for k in np.flatnonzero(shipped_raw[g + '_status'] == 1):
            assert ptr[k + 1] - ptr[k] == 1


def test_shipped_areas_match_polygons(shipped_raw):
    """Sum of fraction x cell area equals the polygon area (0.25 degree grid)."""
    area = shipped_raw['reg_area_km2']
    for k in range(len(area)):
        if shipped_raw['r025_status'][k] == 1:      # a cell next to the region
            continue
        cells = per_cell(shipped_raw, 'r025', k)
        covered = np.sum(np.array(list(cells.values())) * cell_area_km2('r025', list(cells)))
        assert covered == pytest.approx(area[k], rel=0.01), shipped_raw['reg_name'][k]


def test_shipped_districts_sum_to_state(shipped_raw):
    """Per cell within 1e-3, except a few cells where the SoI state and district
    polygons differ by a few km2 (e.g. on the Jharkhand-West Bengal border)."""
    types, parent = shipped_raw['reg_type'], shipped_raw['reg_parent']
    off = []
    for s in np.flatnonzero(types == STATE):
        if shipped_raw['r025_status'][s] == 1:
            continue
        state = per_cell(shipped_raw, 'r025', s)
        total = {}
        for d in np.flatnonzero((types == DISTRICT) & (parent == s)):
            assert shipped_raw['r025_status'][d] != 1
            for c, f in per_cell(shipped_raw, 'r025', d).items():
                total[c] = total.get(c, 0) + f
        for c in set(state) | set(total):
            diff = abs(state.get(c, 0) - total.get(c, 0))
            assert diff < 0.03, (shipped_raw['reg_name'][s], c)
            if diff >= 1e-3:
                off.append((str(shipped_raw['reg_name'][s]), c, diff))
    assert len(off) <= 5, off


def test_shipped_subbasins_tile_basins(shipped_raw):
    """
    Sub-basins cover their basin: same area within 1%, and per cell within
    0.01 except in a few cells where the CWC sub-basin layer leaves gaps
    (up to about 300 km2 per basin). The island basins are left out: their
    CWC sub-basins are larger than the basins.
    """
    types, parent = shipped_raw['reg_type'], shipped_raw['reg_parent']
    area = shipped_raw['reg_area_km2']
    for b in np.flatnonzero(types == BASIN):
        subs = np.flatnonzero((types == SUBBASIN) & (parent == b))
        assert len(subs) >= 1
        name = str(shipped_raw['reg_name'][b])
        if 'Islands' in name:
            continue
        assert area[subs].sum() == pytest.approx(area[b], rel=0.01), name
        basin = per_cell(shipped_raw, 'r025', b)
        total = {}
        for s in subs:
            for c, f in per_cell(shipped_raw, 'r025', s).items():
                total[c] = total.get(c, 0) + f
        diff = np.array([abs(basin.get(c, 0) - total.get(c, 0))
                         for c in set(basin) | set(total)])
        assert diff.max() < 0.25 and (diff > 0.01).sum() <= 5, name


def test_shipped_names_resolve(shipped, monkeypatch):
    """Every name and alias resolves: official names first, aliases within their state."""
    monkeypatch.setattr(regions, '_cache', {'regions': shipped})
    for t in range(4):
        for key, hits in shipped.index[t].items():
            official = sorted({r for r, alias in hits if not alias})
            for r, alias in hits:
                states = {int(shipped.parent[r])} if t == DISTRICT else None
                assert regions._resolve_region(t, alias or key, states) == r, (key, r)
            ids = official or sorted({r for r, _ in hits})
            if len(ids) == 1:
                assert regions._resolve_region(t, key) == ids[0]
            else:
                with pytest.raises(imd.AmbiguousRegionError):
                    regions._resolve_region(t, key)


def test_shipped_official_names_resolve_without_state(shipped, monkeypatch):
    """Only official names that two regions have (e.g. Bilaspur) need state=."""
    monkeypatch.setattr(regions, '_cache', {'regions': shipped})
    duplicates = set()
    for t in range(4):
        keys = [regions._normalise(shipped.name[k]) for k in shipped.of_type(t)]
        for k in shipped.of_type(t):
            key = regions._normalise(shipped.name[k])
            if keys.count(key) > 1:
                duplicates.add(shipped.name[k])
                with pytest.raises(imd.AmbiguousRegionError, match="Add state="):
                    regions._resolve_region(t, shipped.name[k])
            else:
                assert regions._resolve_region(t, shipped.name[k]) == k, shipped.name[k]
    assert duplicates == {'Bilaspur', 'Hamirpur', 'Pratapgarh'}


def test_shipped_no_alias_collides(shipped):
    for t in range(4):
        for key, hits in shipped.index[t].items():
            official = {r for r, a in hits if not a}
            for r, a in hits:
                if a:
                    scope = shipped.parent[r] if t == DISTRICT else -1
                    clash = [o for o in official if o != r and
                             (shipped.parent[o] if t == DISTRICT else -1) == scope]
                    assert not clash, (key, r)
    # official names: unique per type (districts: per state)
    for t in range(4):
        labels = [shipped.label(k) for k in shipped.of_type(t)]
        assert len(labels) == len(set(labels))


def test_shipped_counts_and_known_names(shipped, monkeypatch):
    monkeypatch.setattr(regions, '_cache', {'regions': shipped})
    assert len(shipped.of_type(STATE)) == 36
    assert len(shipped.of_type(BASIN)) == 25 and len(shipped.of_type(SUBBASIN)) == 99
    assert 'Andaman and Nicobar Islands' in shipped.name
    for t, name, label in [(DISTRICT, 'Gurgaon', 'Gurugram (Haryana)'),
                           (DISTRICT, 'Cuttack', 'Kataka (Odisha)'),
                           (DISTRICT, 'kangra', 'Kangra (Himachal Pradesh)'),
                           (STATE, 'Orissa', 'Odisha'), (BASIN, 'Godavari Basin', 'Godavari'),
                           (SUBBASIN, 'Chhimtuipui & Others', 'Chhimtuipui & Others'),
                           # official names beat other regions' aliases
                           (DISTRICT, 'Rayagada', 'Rayagada (Odisha)'),
                           (DISTRICT, 'Raigad', 'Raigad (Maharashtra)'),
                           (DISTRICT, 'Raigarh', 'Raigarh (Chhattisgarh)'),
                           (DISTRICT, 'Bijapur', 'Bijapur (Chhattisgarh)'),
                           (DISTRICT, 'Aurangabad', 'Aurangabad (Bihar)'),
                           # no part in brackets in the column names
                           (DISTRICT, 'Khandwa (East Nimar)', 'Khandwa (Madhya Pradesh)'),
                           (DISTRICT, 'East Nimar', 'Khandwa (Madhya Pradesh)'),
                           (DISTRICT, 'Kaimur', 'Kaimur (Bihar)')]:
        assert shipped.label(regions._resolve_region(t, name)) == label
    assert regions._resolve_region(DISTRICT, 'Bijapur', {regions._resolve_region(
        STATE, 'Karnataka')}) == regions._resolve_region(DISTRICT, 'Vijayapura')
    # No label with two parts in brackets, plain names
    for k in range(len(shipped.name)):
        assert shipped.label(k).count('(') <= 1 and shipped.name[k].isascii()


@pytest.mark.parametrize('name, districts', [
    ('Bengaluru', 'Bengaluru Urban, Bengaluru Rural, Bengaluru South (Karnataka)'),
    ('Godavari', 'East Godavari, West Godavari (Andhra Pradesh)'),
    ('Bardhaman', None), ('Jaintia Hills', None), ('Garhwal', None), ('Nimar', None),
])
def test_shipped_names_of_split_districts(shipped, monkeypatch, name, districts):
    monkeypatch.setattr(regions, '_cache', {'regions': shipped})
    message = "^'{}' matches several districts: ".format(name)
    if districts:
        message += re.escape(districts) + r"\. Use one of these names\.$"
    with pytest.raises(imd.AmbiguousRegionError, match=message):
        regions._resolve_region(DISTRICT, name)


def test_shipped_not_districts(shipped, monkeypatch):
    monkeypatch.setattr(regions, '_cache', {'regions': shipped})
    with pytest.raises(imd.RegionNotFoundError, match=(
            r"^Mahe is not a district in the official boundaries; it is part of Puducherry\. "
            r"For Mahe itself use city='Mahe'\.$")):
        regions._select(district='Mahe')


@pytest.fixture(scope='module')
def shipped_cities():
    saved = dict(regions._cache)
    regions._cache.clear()
    yield regions._cities()
    regions._cache.clear()
    regions._cache.update(saved)


def city(name, **kwargs):
    """Column label of region(city=name, **kwargs)."""
    return [c.label for c in regions._select(city=name, **kwargs)]


def test_shipped_cities(shipped_cities):
    C = shipped_cities
    n = len(C.name)
    assert n > 500000 and len(C.key) == n == len(C.state) == len(C.hq)
    keys = C.key.range(0, n)
    assert keys == sorted(keys)
    assert keys[::5000] == [C.key[i] for i in range(0, n, 5000)]
    assert keys == [regions._normalise(name) for name in C.name.range(0, n)]
    alt = C.alt_key.range(0, len(C.alt_key))
    assert alt == sorted(alt)
    for g, grid in regions._GRIDS.items():
        cells = C.cells[g]
        assert cells[cells != NO_CELL].max() < grid.nlon * grid.nlat
    assert C.cells['gpm'].max() < regions._GRIDS['r025'].nlon * regions._GRIDS['r025'].nlat
    # Plain names: ASCII, no names in capitals
    names = C.name.range(0, n)
    assert all(s.isascii() for s in names)
    assert not [s for s in names if s.isupper() and sum(c.isalpha() for c in s) > 3]
    assert 'Jor Bagh' in names and 'Uttarahalli' in names


@pytest.mark.parametrize('name, kwargs, label', [
    ('Bombay', {}, 'Mumbai (Maharashtra)'), ('Mumbai', {}, 'Mumbai (Maharashtra)'),
    ('Gurgaon', {}, 'Gurugram (Haryana)'), ('Calcutta', {}, 'Kolkata (West Bengal)'),
    ('Pune', {}, 'Pune (Maharashtra)'), ('Madras', {}, 'Chennai (Tamil Nadu)'),
    ('Bangalore', {}, 'Bengaluru (Karnataka)'), ('Bengaluru', {}, 'Bengaluru (Karnataka)'),
    ('Trivandrum', {}, 'Thiruvananthapuram (Kerala)'),
    ('Allahabad', {}, 'Prayagraj (Uttar Pradesh)'), ('Faizabad', {}, 'Ayodhya (Uttar Pradesh)'),
    # A village of that name (in Haryana a village Nagli also has the name)
    ('Allahabad', {'district': 'Nuh'}, 'Allahabad (Haryana)'),
    ('Chandigarh', {}, 'Chandigarh (Chandigarh)'), ('Agartala', {}, 'Agartala (Tripura)'),
    ('Aurangabad', {'state': 'Maharashtra'}, 'Chhatrapati Sambhajinagar (Maharashtra)'),
    ('Delhi', {}, 'Delhi (Delhi)'), ('New Delhi', {}, 'New Delhi (Delhi)'),
    ('Hyderabad', {}, 'Hyderabad (Telangana)'), ('Secunderabad', {}, 'Secunderabad (Telangana)'),
    ('Mahe', {}, 'Mahe (Puducherry)'), ('Rampur', {}, 'Rampur (Uttar Pradesh)'),
    # SoI spellings: the name is corrected, the spelling still works
    ('Thiruvananthpuram', {}, 'Thiruvananthapuram (Kerala)'), ('Panji', {}, 'Panaji (Goa)'),
    ('Vishakhapatnam', {}, 'Visakhapatnam (Andhra Pradesh)'), ('Bid', {}, 'Beed (Maharashtra)'),
    ('Rampur', {'district': 'Bareilly'}, 'Rampur (Uttar Pradesh)'),
    # A town's name beats an old name of an HQ or capital elsewhere (and villages)
    ('Jeypore', {}, 'Jeypore (Odisha)'), ('Dadri', {}, 'Dadri (Uttar Pradesh)'),
    ('Nowgong', {}, 'Nowgong (Madhya Pradesh)'), ('Bopal', {}, 'Bopal (Gujarat)'),
    ('Sihora', {}, 'Sihora (Madhya Pradesh)'), ('Jainagar', {}, 'Jainagar (Bihar)'),
    ('Chittur', {}, 'Chittur (Kerala)'), ('Bareli', {}, 'Bareli (Madhya Pradesh)'),
    ('Velur', {}, 'Velur (Tamil Nadu)'), ('Junagarh', {}, 'Junagarh (Odisha)'),
    ('Baroda', {}, 'Baroda (Madhya Pradesh)'), ('Udaipura', {}, 'Udaipura (Madhya Pradesh)'),
    ('Jeypore', {'state': 'Rajasthan'}, 'Jaipur (Rajasthan)'),
    ('Baroda', {'state': 'Gujarat'}, 'Vadodara (Gujarat)'),
    # A town's name (Brahmapur in Ganjam) beats villages of that name
    ('Brahmapur', {}, 'Brahmapur (Odisha)'),
    # An HQ's current name beats another HQ's old name (Bijapur, now Vijayapura)
    ('Bijapur', {}, 'Bijapur (Chhattisgarh)'), ('Vijayapura', {}, 'Vijayapura (Karnataka)'),
    ('Bijapur', {'state': 'Karnataka'}, 'Vijayapura (Karnataka)'),
    # Towns shown by their own name, not an alternate name (the districts are
    # Anugola and Subarnapur)
    ('Angul', {}, 'Angul (Odisha)'), ('Sonepur', {}, 'Sonepur (Odisha)'),
    ('Anugul', {}, 'Angul (Odisha)'), ('Nabarangpur', {}, 'Nabarangpur (Odisha)'),
    # A town's old or other name beats villages of that name
    ('Rajahmundry', {}, 'Rajamahendravaram (Andhra Pradesh)'),
    ('Berhampur', {}, 'Brahmapur (Odisha)'), ('Hubli', {}, 'Hubballi (Karnataka)'),
    ('Karaikudi', {}, 'Karaikkudi (Tamil Nadu)'), ('Kukatpalli', {}, 'Kukatpally (Telangana)'),
    ('Madanapalli', {}, 'Madanapalle (Andhra Pradesh)'),
    ('Prodduturu', {}, 'Proddatur (Andhra Pradesh)'), ('Kurichi', {}, 'Kurichchi (Tamil Nadu)'),
    # The village is still found with district=
    ('Rajahmundry', {'district': 'Eluru'}, 'Rajahmundry (Andhra Pradesh)'),
])
def test_shipped_city_names(shipped_cities, shipped, name, kwargs, label):
    assert city(name, **kwargs) == [label]


def test_shipped_search_order(shipped_cities, shipped):
    """Areas before cities (official names first); cities as region(city=...) ranks them."""
    def first(text, n=1):
        rows = imd.regions.search(text, limit=n)
        return list(zip(rows['name'], rows['type']))
    assert first('godav') == [('Godavari', 'basin')]
    assert first('Godavari', 2) == [('Godavari', 'basin'), ('Kakinada', 'city')]
    # The district (old name Allahabad) first, then the city before villages
    assert first('Allahabad', 2) == [('Prayagraj', 'district'), ('Prayagraj', 'city')]
    assert first('Rajahmundry') == [('Rajamahendravaram', 'city')]


def test_shipped_city_errors(shipped_cities, shipped):
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^'Aurangabad' fits 2 district HQs or state capitals: Aurangabad \(Bihar\) and "
            r"Chhatrapati Sambhajinagar \(Maharashtra\)\. Add state=")):
        city('Aurangabad')
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^\d+ places named 'Rampur' in Araria \(Bihar\) cannot be told apart\. Use "
            r"region\(district='Araria'\) for the district, or see "
            r"imd\.regions\.search\('Rampur'\)\.$")):
        city('Rampur', district='Araria')
    with pytest.raises(imd.RegionNotFoundError, match=r"^No city named 'Pume'\. Did you mean: "
                                                      r"Pune \(Maharashtra\), "):
        city('Pume')
    # Several towns of one name
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^4 places named 'Shahabad' \(Shahabad \(Kurukshetra, Haryana\), Shahabad "
            r"\(Kalaburagi, Karnataka\), Shahabad \(Hardoi, Uttar Pradesh\), Shahabad "
            r"\(Rampur, Uttar Pradesh\)\)\. Add state=")):
        city('Shahabad')
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Gudalur' \(Gudalur \(The Nilgiris, Tamil Nadu\), Gudalur "
            r"\(Theni, Tamil Nadu\)\)\. Add district=")):
        city('Gudalur', state='Tamil Nadu')
    # All in one state: district= is suggested
    with pytest.raises(imd.AmbiguousRegionError, match=(
            r"^2 places named 'Gudalur' \(.*\)\. Add district=, or see "
            r"imd\.regions\.search\('Gudalur'\)\.$")):
        city('Gudalur')


def test_shipped_one_entry_per_town(shipped_cities, shipped):
    C = shipped_cities
    for name in ('Mumbai', 'Chandigarh', 'Agartala', 'Beed', 'Thiruvananthapuram', 'Panaji'):
        flagged = [c for c, _, _ in C.lookup(regions._normalise(name)) if C.hq[c]]
        assert len(flagged) == 1, name
    for typo in ('Thiruvananthpuram', 'Panji', 'Bengluru', 'Vishakhapatnam', 'Bid'):
        assert not [c for c in range(len(C.name)) if C.hq[c] and C.name[c] == typo]


def test_shipped_search_lists_a_place_once(shipped_cities, shipped):
    for name, state, district in [('Chandigarh', 'Chandigarh', 'Chandigarh'),
                                  ('Kalimpong', 'West Bengal', 'Kalimpong')]:
        s = imd.regions.search(name, type='city', limit=100)
        rows = s[(s.name == name) & (s.state == state) & (s.district == district)]
        assert len(rows) == 1, name
    s = imd.regions.search('Rajahmundry')
    assert s.values.tolist()[0] == ['Rajamahendravaram', 'city', 'Andhra Pradesh',
                                    'East Godavari', 'Rajahmundry']
    s = imd.regions.search('Angul', type='city', state='Odisha')
    assert 'Anugola' not in s.name.tolist() and 'Angul' in s.name.tolist()


def test_shipped_capitals_resolve(shipped_cities, shipped):
    """Every state capital resolves by its name, with and without state=."""
    C, R = shipped_cities, shipped
    capitals = np.flatnonzero(C.hq == 2)
    assert len(capitals) >= 36
    # Chandigarh, the capital of Punjab and Haryana, lies in the union territory Chandigarh
    states = {R.name[C.state[c]] for c in capitals} | {'Punjab', 'Haryana'}
    assert states == {R.name[k] for k in R.of_type(STATE)}
    for c in capitals:
        label = regions._city_label(int(c))
        assert city(C.name[c]) == [label]
        assert city(C.name[c], state=R.name[C.state[c]]) == [label]


def test_shipped_search_shows_plain_names(shipped_cities, shipped):
    s = imd.regions.search('Punch', type='district')
    assert s.values.tolist()[0] == ['Poonch', 'district', 'Jammu and Kashmir', '', 'Punch']
    s = imd.regions.search('Rāmpur', type='city', limit=3)
    assert s.name.tolist() == ['Rampur'] * 3 and list(s.columns) == [
        'name', 'type', 'state', 'district', 'matched_alias']

