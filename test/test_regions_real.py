"""
IMD.region() on real IMD files, compared with an independent overlay of the
source polygons (geopandas), and performance checks.

Marked slow and skipped unless these environment variables are set:

    IMDLIB_TEST_CACHE   imdlib cache with archive/rain/2025.grd,
                        archive/tmax/2025.grd, realtime/rain/<days>.grd and
                        realtime/tmax/<days>.grd (the files are copied; the
                        cache is not changed)
    IMDLIB_TEST_GIS     folder with the region sources used by
                        scripts/build_regions.py (DISTRICTS/, CWC_Basin/, ...)
                        (only for the overlay comparisons)

    pytest -m slow test/test_regions_real.py
"""
import glob
import os
import shutil
import subprocess
import sys
import time
import zipfile

import numpy as np
import pandas as pd
import pytest

import imdlib as imd
from imdlib import regions

pytestmark = pytest.mark.slow

CACHE = os.environ.get('IMDLIB_TEST_CACHE')
GIS = os.environ.get('IMDLIB_TEST_GIS')
CEA = '+proj=cea +lon_0=0 +lat_ts=0 +R=6371007.2 +units=m +no_defs'


@pytest.fixture(scope='module')
def cache(tmp_path_factory):
    if not CACHE or not os.path.isfile(os.path.join(CACHE, 'archive', 'rain', '2025.grd')):
        pytest.skip('IMDLIB_TEST_CACHE is not set')
    root = tmp_path_factory.mktemp('cache')
    for path in glob.glob(os.path.join(CACHE, '*', '*', '*.grd')):
        rel = os.path.relpath(path, CACHE)
        os.makedirs(os.path.dirname(root / rel), exist_ok=True)
        shutil.copyfile(path, root / rel)
    return root


@pytest.fixture(scope='module')
def rain(cache):
    data = imd.load('rain', 2025, cache_dir=cache, offline=True)
    data.data
    return data


def gis():
    if not GIS or not os.path.isdir(os.path.join(GIS, 'DISTRICTS')):
        pytest.skip('IMDLIB_TEST_GIS is not set')
    return pytest.importorskip('geopandas')


def soi_polygon(layer, name, state_zip):
    """A state ('STATE_BDY') or district ('DISTRICT_BDY') polygon from the SoI zip."""
    gpd = gis()
    import shapely
    zp = os.path.join(GIS, 'DISTRICTS', state_zip)
    member = [m for m in zipfile.ZipFile(zp).namelist() if m.endswith(layer + '.shp')][0]
    df = gpd.read_file('/vsizip/' + os.path.abspath(zp) + '/' + member).to_crs(4326)
    col = 'STATE' if layer == 'STATE_BDY' else 'DISTRICT'
    geoms = df.geometry[df[col].str.strip().str.upper() == name.upper()]
    assert len(geoms) == 1
    return shapely.make_valid(shapely.force_2d(geoms.iloc[0]))


def cwc_basin(name):
    gpd = gis()
    import shapely
    df = gpd.read_file(os.path.join(GIS, 'CWC_Basin', 'BASIN_CWC.shp')).to_crs(4326)
    return shapely.make_valid(df.geometry[df.Basin_Name == name].iloc[0])


def overlay_mean(data, geom):
    """Area-weighted mean from a geopandas overlay in an equal-area projection."""
    gpd = gis()
    import shapely
    lon, lat = data.lon_array, data.lat_array
    d = lon[1] - lon[0]
    x0, y0, x1, y1 = geom.bounds
    ii = np.flatnonzero((lon + d / 2 > x0) & (lon - d / 2 < x1))
    jj = np.flatnonzero((lat + d / 2 > y0) & (lat - d / 2 < y1))
    I, J = (a.ravel() for a in np.meshgrid(ii, jj, indexing='ij'))
    cells = gpd.GeoDataFrame({'i': I, 'j': J}, crs=4326,
                             geometry=shapely.box(lon[I] - d / 2, lat[J] - d / 2,
                                                  lon[I] + d / 2, lat[J] + d / 2)).to_crs(CEA)
    cell_area = dict(zip(zip(cells.i, cells.j), cells.area))
    region = gpd.GeoDataFrame(geometry=[geom], crs=4326).to_crs(CEA)
    inter = gpd.overlay(cells, region, how='intersection', keep_geom_type=True)
    area = inter.area.values
    frac = area / np.array([cell_area[c] for c in zip(inter.i, inter.j)])
    keep = frac > 1e-4
    i, j, area = inter.i.values[keep], inter.j.values[keep], area[keep]
    v = np.array(data.data[:, i, j], dtype=float)
    ok = np.isfinite(v) & (v != -999.0)
    if data.cat in ('tmin', 'tmax'):
        ok &= v != data.data[0, 0, 0]
    if data.land_mask is not None:
        ok &= data.land_mask[i, j]
    num = np.where(ok, v, 0) @ area
    den = ok @ area
    return np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)


def assert_same(a, b, scale):
    """Agreement to 1e-6 of the typical magnitude (weights are stored as float32)."""
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    err = np.max(np.abs(a[ok] - b[ok]))
    assert err <= 1e-6 * scale, err
    return err


###############################################################################
# Comparison with an independent overlay
###############################################################################

@pytest.mark.parametrize('kwargs, layer, name, zip_name', [
    (dict(state='Kerala'), 'STATE_BDY', 'KERALA', 'KERALA.zip'),
    (dict(district='Pune'), 'DISTRICT_BDY', 'PUNE', 'MAHARASHTRA.zip'),
    (dict(district='Nashik'), 'DISTRICT_BDY', 'NASHIK', 'MAHARASHTRA.zip'),
    (dict(district='Ratnagiri'), 'DISTRICT_BDY', 'RATNAGIRI', 'MAHARASHTRA.zip'),   # coastal
    (dict(basin='Godavari'), None, 'Godavari', None),
])
def test_archive_rain_equals_overlay(rain, kwargs, layer, name, zip_name):
    geom = cwc_basin(name) if layer is None else soi_polygon(layer, name, zip_name)
    got = rain.region(**kwargs).iloc[:, 0].values
    want = overlay_mean(rain, geom)
    err = assert_same(got, want, scale=max(1.0, np.nanmax(np.abs(want))))
    print('{}: max |region - overlay| = {:.2e} mm'.format(name, err))


def max_difference(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    assert np.array_equal(np.isnan(a), np.isnan(b))
    ok = ~np.isnan(a)
    return float(np.max(np.abs(a[ok] - b[ok]))) if ok.any() else 0.0


@pytest.mark.parametrize('var', ['rain', 'tmax'])
@pytest.mark.parametrize('kwargs', [
    dict(state='Kerala'), dict(state='Maharashtra'), dict(district='Pune'),
    dict(district='Karaikal'), dict(district='Ratnagiri'), dict(basin='Godavari'),
    dict(subbasin='Wainganga'), dict(state='Lakshadweep')])
def test_clip_spatial_mean_equals_region(cache, var, kwargs):
    data = imd.load(var, 2025, cache_dir=cache, offline=True)
    clipped = data.clip(**kwargs)
    assert data._data_pending and clipped._data_pending
    err = max_difference(clipped.spatial_mean().iloc[:, 0], data.region(**kwargs).iloc[:, 0])
    print('{} {}: max |clip().spatial_mean() - region()| = {:.1e}'.format(var, kwargs, err))
    assert err <= 1e-12
    assert clipped.region(**kwargs).equals(data.region(**kwargs))


@pytest.mark.parametrize('kwargs, layer, name, zip_name', [
    (dict(state='Kerala'), 'STATE_BDY', 'KERALA', 'KERALA.zip'),
    (dict(district='Pune'), 'DISTRICT_BDY', 'PUNE', 'MAHARASHTRA.zip'),
    (dict(basin='Godavari'), None, 'Godavari', None),
])
def test_clip_shapefile_equals_region(rain, tmp_path, kwargs, layer, name, zip_name):
    """clip(shapefile) equals region(shapefile), and is close to the shipped weights."""
    gpd = gis()
    geom = cwc_basin(name) if layer is None else soi_polygon(layer, name, zip_name)
    shp = tmp_path / 'area.shp'
    gpd.GeoDataFrame(geometry=[geom], crs=4326).to_file(shp)
    clipped = rain.clip(str(shp))
    got = clipped.spatial_mean().iloc[:, 0]
    err = max_difference(got, rain.region(shapefile=shp).iloc[:, 0])
    print('{}: max |clip(shapefile).spatial_mean() - region(shapefile)| = {:.1e}'.format(
        name, err))
    assert err <= 1e-12
    # Named region: same cells, fractions stored as float32
    named = rain.clip(**kwargs)
    assert np.array_equal(named.cell_fraction > 0, clipped.cell_fraction > 0)
    assert np.allclose(named.cell_fraction, clipped.cell_fraction, rtol=0, atol=1e-6)
    assert_same(got.values, named.spatial_mean().iloc[:, 0].values, scale=np.nanmax(got))


def test_clip_reads_only_the_box(cache, monkeypatch):
    from imdlib import lazy
    boxes = []
    chunks = lazy.GrdFiles._chunks

    def spy(self, lon_idx, lat_idx, max_days=None):
        boxes.append((lon_idx, lat_idx))
        return chunks(self, lon_idx, lat_idx, max_days)
    monkeypatch.setattr(lazy.GrdFiles, '_chunks', spy)
    data = imd.load('rain', 2025, cache_dir=cache, offline=True)
    pune = data.clip(district='Pune')
    assert boxes == []
    pune.data
    assert len(boxes) == 1 and pune.data.shape == (365,) + pune.cell_fraction.shape
    lon, lat = boxes[0]
    assert (lon.stop - lon.start, lat.stop - lat.start) == pune.cell_fraction.shape


def test_tiny_district_on_1_degree_temperature(cache):
    """Karaikal (about 160 km2) covers parts of one or two 1-degree cells."""
    data = imd.load('tmax', 2025, cache_dir=cache, offline=True)
    got = data.region(district='Karaikal').iloc[:, 0].values
    assert np.isfinite(got).all() and 20 < np.mean(got) < 40
    geom = soi_polygon('DISTRICT_BDY', 'KARAIKAL', 'PUDUCHERRY.zip')
    assert_same(got, overlay_mean(data, geom), scale=40)


def test_city_cells_have_data(rain):
    out = rain.region(city=['Mumbai', 'Chennai', 'Bombay', 'Port Blair'])
    assert list(out.columns) == ['Mumbai (Maharashtra)', 'Chennai (Tamil Nadu)',
                                 'Port Blair (Andaman and Nicobar Islands)']
    assert out.iloc[:, :2].notna().all().all()
    C = regions._cities()
    for city in ('Mumbai', 'Chennai'):
        c = regions._resolve_city(city)
        cell = int(C.cells['r025'][c])
        assert rain.land_mask[cell // 129, cell % 129]
    # Chennai's own cell (80.28 E, 13.09 N) has no data: a cell next to it with data is used
    own = (int(round((80.2785 - 66.5) / 0.25)), int(round((13.0878 - 6.5) / 0.25)))
    assert not rain.land_mask[own]
    cell = int(C.cells['r025'][regions._resolve_city('Chennai')])
    i, j = cell // 129, cell % 129
    assert (i, j) != own and max(abs(i - own[0]), abs(j - own[1])) == 1 and rain.land_mask[i, j]
    # No cell with data on or next to the islands: NaN
    assert out['Port Blair (Andaman and Nicobar Islands)'].isna().all()


def test_islands_without_data_are_nan(rain, cache):
    out = rain.region(state=['Andaman and Nicobar Islands', 'Lakshadweep', 'Kerala'])
    assert out.iloc[:, :2].isna().all().all() and out['Kerala'].notna().all()
    out = rain.region(district=['Nicobars', 'Lakshadweep District'])
    assert out.isna().all().all()
    tmax = imd.load('tmax', 2025, cache_dir=cache, offline=True)
    assert tmax.region(state='Andaman and Nicobar Islands').isna().all().all()
    assert tmax.region(city='Port Blair').isna().all().all()


def realtime_files(cache, var):
    days = sorted(glob.glob(os.path.join(str(cache), 'realtime', var, '*.grd')))
    if not days:
        pytest.skip('no real-time {} files'.format(var))
    return [os.path.basename(p)[:10] for p in (days[0], days[-1])]


def test_realtime_temperature(cache):
    """Missing value 99.9; a clipped object (corner no longer 99.9) gives the same result."""
    start, end = realtime_files(cache, 'tmax')
    data = imd.load('tmax', start, end, source='realtime', cache_dir=cache, offline=True)
    kw = dict(district=['Pune', 'Karaikal', 'Jaipur'])
    full = data.region(**kw)
    assert full.notna().all().all() and ((full > 0) & (full < 50)).all().all()
    clipped = data.copy()
    clipped.data
    i0, i1, j0, j1 = 10, 40, 5, 50
    clipped.data = clipped.data[:, i0:i1, j0:j1].copy()
    clipped.lon_array, clipped.lat_array = clipped.lon_array[i0:i1], clipped.lat_array[j0:j1]
    assert clipped.data[0, 0, 0] != np.float32(99.9)
    assert clipped.region(**kw).equals(full)
    # clip() and spatial_mean() use the missing value too
    for district in kw['district']:
        err = max_difference(data.clip(district=district).spatial_mean().iloc[:, 0],
                             data.region(district=district).iloc[:, 0])
        assert err <= 1e-12
    if GIS and os.path.isdir(os.path.join(GIS, 'DISTRICTS')):
        geom = soi_polygon('DISTRICT_BDY', 'PUNE', 'MAHARASHTRA.zip')
        eager = data.copy()
        eager.data
        assert_same(full.iloc[:, 0].values, overlay_mean(eager, geom), scale=50)


###############################################################################
# Lazy and eager, real-time
###############################################################################

def test_lazy_equals_eager_real_files(cache, rain):
    lazy = imd.load('rain', 2025, cache_dir=cache, offline=True)
    for kw in (dict(state='Maharashtra', by='district'), dict(basin='Godavari', by='subbasin'),
               dict(city=['Pune', 'Gurgaon'])):
        assert lazy.region(**kw).equals(rain.region(**kw))
    assert lazy._data_pending


def test_realtime_rain(cache):
    start, end = realtime_files(cache, 'rain')
    lazy = imd.load('rain', start, end, source='realtime', cache_dir=cache, offline=True)
    full = lazy.copy()
    full.data
    a = lazy.region(state=['Kerala', 'Maharashtra'])
    assert a.equals(full.region(state=['Kerala', 'Maharashtra']))
    assert a.notna().all().all()
    assert lazy.region(city='Pune').equals(full.region(city='Pune'))
    assert lazy._data_pending


###############################################################################
# Performance (warm file cache)
###############################################################################

@pytest.fixture(scope='module')
def long_cache(cache, tmp_path_factory):
    """Rain 1901-2025: every year is the 2025 file (one extra day for leap years)."""
    root = tmp_path_factory.mktemp('long')
    src = os.path.join(str(cache), 'archive', 'rain', '2025.grd')
    folder = root / 'archive' / 'rain'
    folder.mkdir(parents=True)
    leap = root / 'leap.grd'
    with open(src, 'rb') as f:
        content = f.read()
    leap.write_bytes(content + content[-129 * 135 * 4:])
    for year in range(1901, 2026):
        os.link(leap if imd.LeapYear(year) else src, folder / '{}.grd'.format(year))
    return root


def best_of(fn, n=3):
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return min(times)


def test_performance(long_cache):
    data = imd.load('rain', 1901, 2025, cache_dir=long_cache, offline=True)
    data.region(district='Pune')                   # warm up (file cache, region data)
    t1 = best_of(lambda: imd.load('rain', 1901, 2025, cache_dir=long_cache,
                                  offline=True).region(district='Pune'))
    t25 = best_of(lambda: imd.load('rain', 2001, 2025, cache_dir=long_cache,
                                   offline=True).region(state='Maharashtra', by='district'))
    regions._resolve_region(regions._DISTRICT, 'Pune')
    lookup = best_of(lambda: regions._select(None, 'Nashik', None, None, None, None), n=20)
    city = best_of(lambda: regions._select(None, None, 'Rampur', None, None, None), n=20)
    print('one district 1901-2025: {:.3f} s; Maharashtra by district 2001-2025: {:.3f} s; '
          'name lookup {:.2f} ms; city lookup {:.2f} ms'.format(t1, t25, lookup * 1e3, city * 1e3))
    assert t1 < 1.0 and t25 < 1.0 and lookup < 0.005 and city < 0.005


def test_first_use_of_city_data():
    code = ("import time, imdlib.regions as r\n"
            "t = time.perf_counter(); r._cities(); print(time.perf_counter() - t)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    seconds = float(out.stdout)
    print('first use of city data: {:.3f} s'.format(seconds))
    assert seconds < 0.5
