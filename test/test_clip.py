"""
Tests for IMD.clip(): one area (region name or shapefile), cell fractions,
spatial_mean() on clipped data, lazy reading and saved output.

Uses the synthetic region data and IMD files of test_regions.py and
test_lazy.py (no network, no shipped data). Real IMD files are used in
test_regions_real.py (marked slow).
"""
import sys

import warnings

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import imdlib as imd
from imdlib import regions
from test_load import isolated  # noqa: F401  (autouse: isolated cache, no network)
from test_lazy import reads, put_archive_eager_names, eager, put_realtime  # noqa: F401
from test_regions import synthetic, grid_obj, cells_of, write_shapefile, box_ring  # noqa: F401
from test_regions import imdlib_frames, LAT, LON

AREAS = [dict(state='Alpha'), dict(state='Beta'), dict(district='Pune'),
         dict(district='Bilaspur', state='Beta'), dict(basin='Godavari'),
         dict(subbasin='Godavari Lower')]


def same_values(a, b, tol=1e-12):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    assert np.all(np.abs(a[ok] - b[ok]) <= tol * np.maximum(1.0, np.abs(b[ok])))


def clipped_equals_region(data, tol=1e-12, **area):
    clipped = data.clip(**area)
    same_values(clipped.spatial_mean().iloc[:, 0], data.region(**area).iloc[:, 0], tol)
    return clipped


###############################################################################
# The invariant: clip(...).spatial_mean() == region(...)
###############################################################################

@pytest.mark.parametrize('area', AREAS)
@pytest.mark.parametrize('grid, cat', [('r025', 'rain'), ('t100', 'tmax'), ('t050', 'tmax'),
                                       ('gpm', 'rain_gpm')])
def test_spatial_mean_equals_region(synthetic, area, grid, cat):
    data = grid_obj(grid, days=4, cat=cat, mask=grid != 'gpm')
    if cat == 'tmax':
        data.data += 10
        data.data[:, 0, 0] = 99.9
        data.data[2, ::2, :] = 99.9                     # missing values
    data.data[1, ::3, ::2] = np.nan
    if data.land_mask is not None:
        data.land_mask[::4, ::3] = False
    clipped_equals_region(data, **area)


def test_cells_box_and_fractions(synthetic):
    data = grid_obj(days=2)
    pune = data.clip(district='Pune')
    # Cells (40, 50) and (41, 50), the second half inside
    assert np.array_equal(pune.lon_array, data.lon_array[40:42])
    assert np.array_equal(pune.lat_array, data.lat_array[50:51])
    assert pune.cell_fraction.tolist() == [[1.0], [0.5]]
    assert np.array_equal(pune.data, data.data[:, 40:42, 50:51])
    alpha = data.clip(state='Alpha')
    # Box of the cells (40, 10), (40, 100), (41, 50): cells outside are NaN and masked
    assert alpha.data.shape == (2, 2, 91)
    inside = alpha.cell_fraction > 0
    assert sorted(zip(*np.nonzero(inside))) == [(0, 0), (0, 90), (1, 40)]
    assert np.isnan(alpha.data[:, ~inside]).all() and not alpha.land_mask[~inside].any()
    assert alpha.land_mask[inside].all()
    assert np.array_equal(alpha.data[:, inside], data.data[:, [40, 40, 41], [10, 100, 50]])
    assert data.cell_fraction is None


def test_weights_cos_lat_times_fraction(synthetic):
    data = grid_obj(days=2)
    data.data[:, 40, 50] = 1.0
    data.data[:, 41, 50] = 3.0
    pune = data.clip(district='Pune')
    assert np.allclose(pune.spatial_mean(), (1.0 + 0.5 * 3.0) / 1.5, rtol=1e-14)
    # weighted=False drops cos(lat) only; the fractions still apply
    alpha = data.clip(state='Alpha')
    v = data.data[0, [40, 40, 41], [10, 100, 50]]
    assert alpha.spatial_mean(weighted=False).iloc[0, 0] == pytest.approx(
        (v[0] + v[1] + 0.5 * v[2]) / 2.5, rel=1e-14)


def test_places_without_data(synthetic):
    data = grid_obj(days=2)
    data.land_mask[40, 50] = data.land_mask[41, 50] = False
    pune = clipped_equals_region(data, district='Pune')
    assert pune.spatial_mean().isna().all().all()
    assert (pune.cell_fraction > 0).sum() == 2           # its own cells, without data


def test_shapefile(synthetic, tmp_path):
    pytest.importorskip('shapely')
    data = grid_obj(days=3)
    lon, lat = data.lon_array, data.lat_array
    a = box_ring(lon[40] - 0.125, lat[50] - 0.125, lon[41], lat[50] + 0.125)
    b = box_ring(lon[60] - 0.125, lat[60] - 0.125, lon[60] + 0.125, lat[60] + 0.125)
    shp = tmp_path / 'catchment.shp'
    write_shapefile(shp, [('A', a), ('B', b)])
    # Positional (as before 0.3.0) and keyword; all features form one area
    for clipped in (data.clip(str(shp)), data.clip(shapefile=shp)):
        assert clipped.data.shape == (3, 21, 11)
        assert clipped.cell_fraction[0, 0] == 1.0
        assert clipped.cell_fraction[1, 0] == pytest.approx(0.5, abs=1e-12)
        assert clipped.cell_fraction[20, 10] == 1.0 and (clipped.cell_fraction > 0).sum() == 3
        same_values(clipped.spatial_mean().iloc[:, 0],
                    data.region(shapefile=shp).iloc[:, 0])
    # Any regular grid, e.g. after remap()
    t = grid_obj('t100', days=2, cat='tmax', mask=False)
    t.remap(0.5)
    clipped_equals_region(t, shapefile=shp)


def test_named_regions_need_no_shapely(synthetic, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'shapely', None)
    monkeypatch.setitem(sys.modules, 'shapefile', None)
    clipped_equals_region(grid_obj(), state='Alpha')
    (tmp_path / 'x.shp').write_bytes(b'')
    with pytest.raises(ImportError, match=r"^clip\(shapefile=\.\.\.\) needs pyshp and shapely"):
        grid_obj().clip(tmp_path / 'x.shp')


###############################################################################
# New object, original unchanged, clip of clipped data, region() on it
###############################################################################

def test_original_unchanged(synthetic):
    data = grid_obj(days=2)
    before = [a.copy() for a in (data.data, data.land_mask, data.lat_array, data.lon_array)]
    kerala = data.clip(state='Alpha')
    assert kerala is not data
    after = (data.data, data.land_mask, data.lat_array, data.lon_array)
    assert all(np.array_equal(a, b) for a, b in zip(before, after))
    assert data.cell_fraction is None
    kerala.data[:] = 0
    assert np.array_equal(data.data, before[0])


def test_clip_of_clipped_and_region_on_clipped(synthetic):
    data = grid_obj(days=3)
    godavari = data.clip(basin='Godavari')
    for area in (dict(subbasin='Godavari Lower'), dict(basin='Godavari')):
        assert godavari.region(**area).equals(data.region(**area))
        clipped_equals_region(godavari, **area)
    lower = godavari.clip(subbasin='Godavari Lower')
    want = data.clip(subbasin='Godavari Lower')
    assert np.array_equal(lower.data, want.data, equal_nan=True)
    assert np.array_equal(lower.cell_fraction, want.cell_fraction)
    assert np.array_equal(lower.lon_array, want.lon_array)
    # Cells outside the first region stay NaN: cell (40, 50) of Pune is not in Alpha
    pune = data.clip(state='Alpha').clip(district='Pune')
    assert np.isnan(pune.data[:, 0, 0]).all() and not pune.land_mask[0, 0]
    # ... and their fraction is 0
    assert pune.cell_fraction.tolist() == [[0.0], [0.5]]
    assert np.array_equal(pune.spatial_mean().iloc[:, 0], data.data[:, 41, 50])
    with pytest.raises(ValueError, match=r"Pune \(Alpha\) extends beyond this data's extent"):
        data.clip(state='Beta').clip(district='Pune')


@pytest.mark.parametrize('grid', ['t100', 't050'])
def test_one_cell_box(synthetic, grid):
    """A box one cell wide fits several grids: the grid it was cut from is used."""
    t = grid_obj(grid, days=2, cat='tmax', mask=False)
    pune = t.clip(district='Pune')
    assert pune.data.shape == (2, 1, 1)
    for obj in (pune, pune.copy()):
        assert obj.region(district='Pune').equals(t.region(district='Pune'))
        assert obj.clip(district='Pune').cell_fraction.shape == (1, 1)


###############################################################################
# Errors
###############################################################################

@pytest.mark.parametrize('kwargs, error, message', [
    (dict(city='Pune'), TypeError, r"^clip\(\) takes an area.*region\(city=\.\.\.\)"),
    (dict(state='Alpha', by='district'), TypeError, r"has no by=.*region\(\.\.\., by=\.\.\.\)"),
    (dict(district=['Pune', 'Raigad']), TypeError, r"^clip\(\) takes one area: district= must be "
                                                   r"a name"),
    (dict(state=('Alpha',)), TypeError, r"must be a name"),
    (dict(), TypeError, r"^Give exactly one area"),
    (dict(state='Alpha', basin='Godavari'), TypeError, r"^Give exactly one area"),
    (dict(district='Pune', basin='Godavari'), TypeError, r"^Give exactly one area"),
    (dict(shapefile='x.shp', state='Alpha'), TypeError, r"^Give exactly one area"),
    (dict(shapefile=['a.shp', 'b.shp']), TypeError, r"takes one shapefile"),
    (dict(stat='Alpha'), TypeError, r"unexpected keyword argument 'stat'"),
    (dict(district='Pume'), imd.RegionNotFoundError, r"Did you mean: Pune \(Alpha\)\?"),
    (dict(district='Bilaspur'), imd.AmbiguousRegionError, r"Add state="),
])
def test_errors(synthetic, kwargs, error, message):
    with pytest.raises(error, match=message) as e:
        grid_obj().clip(**kwargs)
    # Short traceback: only the public call
    assert imdlib_frames(e.value) == [('core.py', 'clip')]


def test_not_an_imd_grid(synthetic):
    t = grid_obj('t100', days=1, cat='tmax', mask=False)
    t.remap(0.3)
    with pytest.raises(ValueError, match=(r"^clip\(\) needs data on an IMD grid .*For other "
                                          r"grids use clip\(shapefile=\.\.\.\)\.$")):
        t.clip(district='Pune')


def test_signature():
    import inspect
    assert str(inspect.signature(imd.IMD.clip)) == (
        "(self, shapefile=None, *, state=None, district=None, basin=None, subbasin=None, "
        "**unexpected)")


###############################################################################
# Lazy objects
###############################################################################

def test_lazy_reads_only_the_box(synthetic, isolated, reads, monkeypatch):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    lazy = imd.load('rain', 2019, 2020, offline=True)
    boxes = []
    chunks = imd.lazy.GrdFiles._chunks

    def spy(self, lon_idx, lat_idx, max_days=None):
        boxes.append((lon_idx, lat_idx))
        return chunks(self, lon_idx, lat_idx, max_days)
    monkeypatch.setattr(imd.lazy.GrdFiles, '_chunks', spy)
    alpha = lazy.clip(state='Alpha')
    assert boxes == [] and reads['full'] == 0                 # clip() reads nothing
    assert alpha._data_pending and lazy._data_pending
    ts = alpha.spatial_mean()
    # One read of the box (the land mask is built from it), never the whole grid
    assert boxes == [(slice(40, 42, 1), slice(10, 101, 1))] and reads['full'] == 0
    full = eager(isolated, 'rain', 2019, 2020)
    want = full.clip(state='Alpha')
    assert np.array_equal(alpha.data, want.data, equal_nan=True)
    assert np.array_equal(alpha.land_mask, want.land_mask)
    assert ts.equals(want.spatial_mean())
    assert lazy._data_pending
    # region() on a lazy clipped object reads only the region's cells
    boxes.clear()
    pune = lazy.clip(district='Pune')
    assert pune.region(district='Pune').equals(full.region(district='Pune'))
    assert len(boxes) == 1 and pune._data_pending
    # A lazy clip of a lazy clip
    twice = lazy.clip(state='Alpha').clip(district='Pune')
    want = full.clip(state='Alpha').clip(district='Pune')
    assert np.array_equal(twice.data, want.data, equal_nan=True)
    assert np.array_equal(twice.land_mask, want.land_mask)
    assert np.isnan(twice.data[:, 0, 0]).all() and not twice.land_mask[0, 0]
    assert np.array_equal(twice.cell_fraction, want.cell_fraction)
    assert twice.cell_fraction.tolist() == [[0.0], [0.5]]


def test_lazy_temperature_and_realtime(synthetic, isolated, tmp_path):
    put_archive_eager_names(isolated, 'tmax', [2020])
    a = imd.load('tmax', 2020, offline=True).clip(district='Pune')
    b = eager(isolated, 'tmax', 2020, 2020).clip(district='Pune')
    assert np.array_equal(a.data, b.data, equal_nan=True)
    assert np.array_equal(a.land_mask, b.land_mask)
    days = list(pd.date_range('2026-10-03', '2026-10-05'))
    for var in ('rain', 'rain_gpm', 'tmax'):
        rt = put_realtime(isolated, var, days, tmp_path)
        a = imd.load(var, '2026-10-03', '2026-10-05', source='realtime', offline=True)
        b = imd.open_real_data(var, '2026-10-03', '2026-10-05', rt)
        ca, cb = a.clip(state='Alpha'), b.clip(state='Alpha')
        assert ca.land_mask is None and cb.land_mask is None
        assert np.array_equal(ca.data, cb.data, equal_nan=True)
        assert ca.spatial_mean().equals(cb.spatial_mean())
        same_values(ca.spatial_mean().iloc[:, 0], a.region(state='Alpha').iloc[:, 0])


###############################################################################
# Fractions through computations
###############################################################################

def test_fractions_kept_by_computations(synthetic, isolated):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    data = eager(isolated, 'rain', 2019, 2020)
    alpha = data.clip(state='Alpha')
    fraction = alpha.cell_fraction.copy()
    for method in ('rx5d', 'rxa', 'cdd', 'pci', 'sdii'):
        out = alpha.copy().compute(method, 'A')
        assert np.array_equal(out.cell_fraction, fraction)
        same_values(out.spatial_mean().iloc[:, 0],
                    data.copy().compute(method, 'A').region(state='Alpha').iloc[:, 0])
    for out, ref in ((alpha.copy().climatology(), data.copy().climatology()),
                     (alpha.copy().anomaly(), data.copy().anomaly())):
        assert np.array_equal(out.cell_fraction, fraction)
        same_values(out.spatial_mean().iloc[:, 0], ref.region(state='Alpha').iloc[:, 0])
    copy = alpha.copy()
    assert np.array_equal(copy.cell_fraction, fraction) and copy.cell_fraction is not fraction
    filled = alpha.copy()
    filled.fill_na()
    assert np.array_equal(filled.cell_fraction, fraction)
    remapped = alpha.copy()
    remapped.remap(0.5)
    assert remapped.cell_fraction is None
    assert data.copy().compute('rxa', 'A').cell_fraction is None


def test_spi_on_clipped_data(synthetic, isolated):
    put_archive_eager_names(isolated, 'rain', list(range(2011, 2021)))
    data = imd.load('rain', 2011, 2020, offline=True)
    out = data.clip(district='Pune').compute('spi', 'M', timescale=3)
    assert out.cell_fraction is not None
    # Reference: SPI of a plain box of the data, then region()
    box = data.copy()
    box.data = data._read_cells(slice(38, 44), slice(48, 53))
    box.land_mask = data._land_mask_cells(slice(38, 44), slice(48, 53))
    box.lon_array, box.lat_array = data.lon_array[38:44], data.lat_array[48:53]
    ref = box.compute('spi', 'M', timescale=3).region(district='Pune')
    same_values(out.spatial_mean().iloc[:, 0], ref.iloc[:, 0])


def test_heatwave_on_clipped_data(synthetic, isolated, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    put_archive_eager_names(isolated, 'tmax', list(range(2001, 2011)) + [2020])
    data = imd.load('tmax', 2020, offline=True)
    # The normal period is read separately and cut to the same box
    out = data.clip(state='Beta').heatwave(output='annual', norm_start=2001, norm_end=2010)
    ref = data.copy().heatwave(output='annual', norm_start=2001, norm_end=2010)
    assert out.cell_fraction is not None
    same_values(out.spatial_mean().iloc[:, 0], ref.region(state='Beta').iloc[:, 0])
    box = (slice(8, 9), slice(8, 9))                         # Beta: cell (8, 8) of t100
    assert np.array_equal(out.data, ref.data[:, box[0], box[1]], equal_nan=True)


###############################################################################
# Missing values in spatial_mean()
###############################################################################

def test_spatial_mean_missing_values(synthetic):
    t = grid_obj('t050', days=4, cat='tmax', mask=False)
    t.data += 10
    t.data[:, 0, 0] = 99.9
    t.data[1, 10, 10] = 99.9
    t.data[2, 10, 10] = np.float32(99.9)
    pune = t.clip(district='Pune')
    assert pune.data[0, 0, 0] != np.float32(99.9)         # the corner is a temperature
    ts = pune.spatial_mean().iloc[:, 0]
    assert ts.isna().tolist() == [False, True, True, False]
    same_values(ts, t.region(district='Pune').iloc[:, 0])
    # Rain: -999 only; GPM rain has no missing value
    r = grid_obj(days=2)
    r.data[0, 42, 50] = -999.0
    r.data[1, 42, 50] = 99.9
    ts = r.clip(district='Bilaspur', state='Alpha').spatial_mean().iloc[:, 0]
    assert np.isnan(ts.iloc[0]) and ts.iloc[1] == pytest.approx(99.9)
    g = grid_obj('gpm', days=1, cat='rain_gpm', mask=False)
    g.data[:] = -999.0
    assert g.spatial_mean().iloc[0, 0] == pytest.approx(-999.0, rel=1e-12)
    assert (g.get_xarray()[g.var_name].values == -999.0).all()


###############################################################################
# Saved output
###############################################################################

def test_xarray_and_netcdf(synthetic, tmp_path):
    data = grid_obj(days=2)
    plain = data.get_xarray()
    assert 'cell_fraction' not in plain.coords and set(plain.coords) == {'lat', 'lon', 'time'}
    assert list(plain.data_vars) == ['rain']
    pune = data.clip(district='Pune')
    x = pune.get_xarray()
    assert isinstance(x, xr.Dataset) and list(x.data_vars) == ['rain']
    assert x.cell_fraction.dims == ('lat', 'lon')
    assert x.cell_fraction.attrs == {'long_name': 'fraction of grid cell inside the region',
                                     'units': '1'}
    assert np.array_equal(x.cell_fraction.values, pune.cell_fraction.T)
    pune.to_netcdf('pune', out_dir=str(tmp_path))
    with xr.open_dataset(tmp_path / 'pune.nc') as f:
        assert np.array_equal(f.cell_fraction.values, pune.cell_fraction.T)
        assert f.cell_fraction.attrs['units'] == '1'
    data.to_netcdf('all', out_dir=str(tmp_path))
    with xr.open_dataset(tmp_path / 'all.nc') as f:
        assert 'cell_fraction' not in f.variables
    # CSV is unchanged: no cell_fraction column
    pune.to_csv(str(tmp_path / 'pune.csv'))
    assert list(pd.read_csv(tmp_path / 'pune.csv').columns) == ['time', 'lat', 'lon', 'rain']
    # Computed data keeps the coordinate
    assert 'cell_fraction' in pune.copy().compute('rxa', 'A').get_xarray().coords


def test_xarray_temperature_missing_value(synthetic):
    """The missing value 99.9 becomes NaN, not the corner value (a temperature after clip)."""
    t = grid_obj('t100', days=2, cat='tmax', mask=False)
    t.data += 10
    t.data[:, 0, 0] = 99.9
    t.data[1, 5, 5] = 99.9
    x = t.get_xarray()
    assert np.isnan(x.tmax.values[:, 0, 0]).all() and np.isnan(x.tmax.values[1, 5, 5])
    assert np.isfinite(x.tmax.values).sum() == 2 * 31 * 31 - 3
    pune = t.clip(district='Pune')
    xp = pune.get_xarray()
    assert np.isfinite(xp.tmax.values[0]).all() and np.isnan(xp.tmax.values[1]).all()


def test_geotiff_keeps_zero_values(synthetic, tmp_path):
    """Missing values are NaN in GeoTIFF; a dry day at a corner of clipped data is data."""
    rioxarray = pytest.importorskip('rioxarray')
    data = grid_obj(days=3)
    data.data[:, 41, 50] = 0.0                    # the top-right corner of Pune's box
    data.data[:, 0, 0] = -999.0
    pune = data.clip(district='Pune')
    assert 1 in pune.data.shape[1:]                 # a box one cell wide
    from rasterio.errors import NotGeoreferencedWarning
    with warnings.catch_warnings():
        warnings.simplefilter('error', NotGeoreferencedWarning)
        pune.to_geotiff('pune', out_dir=str(tmp_path))
    xp = pune.get_xarray()
    with rioxarray.open_rasterio(tmp_path / 'pune.tif') as r:
        assert np.isnan(r.rio.nodata)
        assert (r.values == 0.0).sum() == 3 and np.isfinite(r.values).all()
        # Georeferenced at the grid cells, also with one cell along an axis
        assert np.allclose(np.sort(r.x.values), xp.lon.values)
        assert np.allclose(np.sort(r.y.values), xp.lat.values)
    # Unclipped: the same values as get_xarray(), missing values NaN
    data.to_geotiff('all', out_dir=str(tmp_path))
    want = data.get_xarray().rain.values
    with rioxarray.open_rasterio(tmp_path / 'all.tif') as r:
        assert np.isnan(r.rio.nodata)
        got = r.values[:, ::-1, :] if r.y.values[0] > r.y.values[-1] else r.values
        assert np.array_equal(got, want, equal_nan=True)


###############################################################################
# Functions that combine two datasets
###############################################################################

def temperatures(grid='t100', days=3):
    tmax = grid_obj(grid, days=days, cat='tmax', mask=False, seed=1)
    tmax.data += 30
    tmin = grid_obj(grid, days=days, cat='tmin', mask=False, seed=2)
    tmin.data += 15
    return tmax, tmin


def test_dtr_needs_the_same_cells(synthetic):
    tmax, tmin = temperatures()
    full = tmax.copy().compute('dtr', 'A', tmin=tmin.copy())
    out = tmax.clip(state='Beta').compute('dtr', 'A', tmin=tmin.clip(state='Beta'))
    assert np.array_equal(out.data, full.data[:, 8:9, 8:9])
    message = r"^compute\('dtr'\) needs tmax and tmin on the same grid cells"
    for a, b in ((tmax.clip(state='Beta'), tmin),
                 (tmax, tmin.clip(state='Beta')),
                 (tmax.clip(state='Beta'), tmin.clip(state='Alpha')),
                 (tmax.clip(district='Pune'), tmin.clip(state='Alpha')),   # same box (5, 5)
                 (tmax, temperatures('t050')[1])):
        with pytest.raises(ValueError, match=message):
            a.copy().compute('dtr', 'A', tmin=b)


def test_spei_needs_unclipped_temperature(synthetic):
    tmax, tmin = temperatures()
    rain = grid_obj(days=3)
    message = r"^SPEI needs unclipped tmax and tmin\. .*\.clip\(state='Kerala'\)"
    for a, b in ((tmax.clip(state='Beta'), tmin), (tmax, tmin.clip(state='Beta')),
                 (tmax.clip(state='Beta'), tmin.clip(state='Beta'))):
        with pytest.raises(ValueError, match=message):
            rain.clip(state='Beta').compute('spei', 'M', tmax=a, tmin=b)
    with pytest.raises(ValueError, match=r"^SPEI needs tmax and tmin on the same grid cells"):
        rain.copy().compute('spei', 'M', tmax=tmax, tmin=temperatures('t050')[1])
    # Clipped rain with full temperature passes these checks (10 years are needed)
    with pytest.raises(Exception, match=r"^SPEI requires at least 10 years"):
        rain.clip(state='Beta').compute('spei', 'M', tmax=tmax, tmin=tmin)


def test_anomaly_needs_the_same_cells(synthetic):
    t = grid_obj('t100', days=365, cat='tmax', mask=False, start='2019-01-01')
    t.data += 20
    clim = t.clip(district='Pune').climatology()
    out = t.clip(district='Pune').anomaly(clim)
    assert np.array_equal(out.data, t.copy().anomaly(t.copy().climatology()).data[:, 5:6, 5:6])
    # Same box (cell (5, 5)), other region
    with pytest.raises(ValueError, match=r"^anomaly\(\) needs data and climatology on the same "
                                         r"grid cells"):
        t.clip(state='Alpha').anomaly(clim)


###############################################################################
# fill_na(), clip('name'), clip(district='Mahe')
###############################################################################

def test_fill_na_with_a_day_without_values(synthetic):
    data = grid_obj(days=3)
    data.data[1, 40:42, 50] = -999.0
    pune = data.clip(district='Pune')
    pune.fill_na()                                   # finishes: the day stays missing
    assert np.isnan(pune.data[1]).all()
    assert np.array_equal(pune.data[[0, 2]], data.data[[0, 2], 40:42, 50:51])
    t = grid_obj('t050', days=3, cat='tmax', mask=False)
    t.data[2] = 99.9
    pune = t.clip(district='Pune')
    pune.fill_na()
    assert np.isnan(pune.data[2]).all() and np.isfinite(pune.data[:2]).all()


def filled(grid, cat, cells):
    """fill_na() on data missing on day 0 at ``cells`` (lon, lat indices)."""
    data = grid_obj(grid, days=2, cat=cat, mask=False)
    for i, j in cells:
        data.data[0, i, j] = np.nan
    data.fill_na()
    return np.isfinite(data.data[0])


def test_fill_na_skips_the_same_cells_on_the_1_degree_grid():
    from imdlib.core import _no_fill_cells
    i, j = np.meshgrid(np.arange(31), np.arange(31), indexing='ij')
    old = ((i >= 24) & (i <= 26) & (j >= 1) & (j <= 5)) | (j == 0)
    for cat in ('tmin', 'tmax'):
        assert np.array_equal(_no_fill_cells(cat, LON['t100'], LAT['t100']), old)
    ok = filled('t100', 'tmax', [(25, 3), (10, 0), (10, 10)])
    assert not ok[25, 3] and not ok[10, 0] and ok[10, 10]


def test_fill_na_skips_by_coordinates_on_the_half_degree_grid():
    # 92.5E 10N and 7.5N are skipped; 80E 9N (1.0 degree cell indices of
    # the Andaman area), the edge 91E and the row 8N are filled
    ok = filled('t050', 'tmin', [(50, 5), (20, 0), (25, 3), (47, 5), (20, 1)])
    assert not ok[50, 5] and not ok[20, 0]
    assert ok[25, 3] and ok[47, 5] and ok[20, 1]


@pytest.mark.parametrize('grid, cat', [('gpm', 'rain_gpm'), ('r025', 'rain')])
def test_fill_na_fills_rain_everywhere(grid, cat):
    i = int(np.argmin(np.abs(LON[grid] - 92.5)))
    j = int(np.argmin(np.abs(LAT[grid] - 10.0)))
    j0 = int(np.argmin(np.abs(LAT[grid] - 7.5)))
    ok = filled(grid, cat, [(i, j), (i, j0), (25, 3), (10, 0)])
    assert ok[i, j] and ok[i, j0] and ok[25, 3] and ok[10, 0]


@pytest.mark.parametrize('name, hint', [
    ('Alpha', r"For a region use clip\(state='Alpha'\)\.$"),
    ('poona', r"For a region use clip\(district='poona'\)\.$"),
    ('Bilaspur', r"For a region use clip\(district='Bilaspur'\)\.$"),
    ('Godavari Upper', r"For a region use clip\(subbasin='Godavari Upper'\)\.$"),
    ('Nowhere', r"For a region use clip\(state=\.\.\.\), clip\(district=\.\.\.\), "
                r"clip\(basin=\.\.\.\) or clip\(subbasin=\.\.\.\)\.$"),
])
def test_name_given_as_shapefile(synthetic, monkeypatch, name, hint):
    monkeypatch.setitem(sys.modules, 'shapefile', None)          # not needed for the message
    with pytest.raises(ValueError, match=r"^There is no shapefile {!r}\. ".format(name) + hint) \
            as e:
        grid_obj().clip(name)
    assert imdlib_frames(e.value) == [('core.py', 'clip')]


def test_not_a_district_in_clip(synthetic):
    with pytest.raises(imd.RegionNotFoundError, match=(
            r"^Mahe is not a district in the official boundaries; it is part of Beta\. "
            r"For Mahe itself use region\(city='Mahe'\)\.$")) as e:
        grid_obj().clip(district='Mahe')
    assert imdlib_frames(e.value) == [('core.py', 'clip')]
