"""
Tests for the lazy reading of IMD objects returned by imdlib.load().

No network access: files are put into the cache directly.
"""
import os
import warnings

import numpy as np
import pandas as pd
import pytest

import imdlib as imd
from imdlib import lazy
from test_load import grd_bytes, put_archive
from test_load import isolated  # noqa: F401  (autouse: isolated cache, no network)


###############################################################################
# Helpers
###############################################################################

@pytest.fixture
def reads(monkeypatch):
    """Count full reads and cell-by-cell reads of the files."""
    calls = {'full': 0, 'cells': 0}
    read, chunks = lazy.GrdFiles.read, lazy.GrdFiles._chunks

    def spy_read(self):
        calls['full'] += 1
        return read(self)

    def spy_chunks(self, *a, **k):
        calls['cells'] += 1
        return chunks(self, *a, **k)
    monkeypatch.setattr(lazy.GrdFiles, 'read', spy_read)
    monkeypatch.setattr(lazy.GrdFiles, '_chunks', spy_chunks)
    return calls


def archive(root, var, years):
    for year in years:
        put_archive(root, var, year)


def eager(root, var, start, end):
    """open_data() on the files of the cache."""
    return imd.open_data(var, start, end, 'yearwise', str(root / 'archive'))


def put_archive_eager_names(root, var, years):
    """Cache files, plus copies named as open_data(..., 'yearwise') expects."""
    archive(root, var, years)
    if var != 'rain':
        for year in years:
            src = root / 'archive' / var / '{}.grd'.format(year)
            src.with_suffix('.GRD').write_bytes(src.read_bytes())


def put_realtime(root, var, days, tmp_path):
    """Real-time files in the cache and with open_real_data() names."""
    names = {'rain': 'rain_ind0.25_{:%y_%m_%d}.grd', 'rain_gpm': '{:%d%m%Y}.grd',
             'tmax': 'max{:%d%m%Y}.grd', 'tmin': 'min{:%d%m%Y}.grd'}
    rt = tmp_path / 'rt'
    rt.mkdir(exist_ok=True)
    for i, day in enumerate(days):
        content = grd_bytes(var, 1, 'realtime', seed=i)
        path = root / 'realtime' / var / '{:%Y-%m-%d}.grd'.format(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        (rt / names[var].format(day)).write_bytes(content)
    return str(rt)


def same(a, b):
    """IMD objects a (lazy) and b (eager) hold identical data."""
    assert a.data.dtype == b.data.dtype == np.float64
    assert a.data.shape == b.data.shape
    assert a.data.tobytes() == b.data.tobytes()
    if b.land_mask is None:
        assert a.land_mask is None
    else:
        assert a.land_mask.dtype == b.land_mask.dtype
        assert np.array_equal(a.land_mask, b.land_mask)
    assert np.array_equal(a.lat_array, b.lat_array)
    assert np.array_equal(a.lon_array, b.lon_array)
    assert (a.cat, a.start_day, a.end_day, a.no_days) == \
        (b.cat, b.start_day, b.end_day, b.no_days)


###############################################################################
# Reading on first use
###############################################################################

def test_load_does_not_read(isolated, reads, capsys):
    archive(isolated, 'rain', [2019, 2020])
    data = imd.load('rain', 2019, 2020, offline=True)
    assert reads == {'full': 0, 'cells': 0}
    # Metadata does not read
    data.shape
    assert capsys.readouterr().out == '(731, 135, 129)\n'
    assert (data.cat, data.start_day, data.end_day, data.no_days) == \
        ('rain', '2019-01-01', '2020-12-31', 731)
    assert len(data.lat_array) == 129 and len(data.lon_array) == 135
    assert data.var_name == 'rain' and data.computed is False
    assert reads == {'full': 0, 'cells': 0}
    # First use reads once, with the land mask from the same data
    assert data.data.shape == (731, 135, 129)
    assert reads == {'full': 1, 'cells': 0}
    data.data
    data.land_mask
    data.shape
    assert capsys.readouterr().out == '(731, 135, 129)\n'
    assert reads == {'full': 1, 'cells': 0}


@pytest.mark.parametrize('start, end', [
    (2019, 2020),                        # leap year included
    ('2020-02-15', '2020-03-10'),        # sub-year range with 29 February
    ('2019-12-20', '2020-01-10'),        # across two files
    ('2019-03-01', '2020-02-29'),        # 366 days, not whole years
    (2020, 2020),
])
@pytest.mark.parametrize('mask_first', [False, True])
def test_archive_rain_same_as_open_data(isolated, start, end, mask_first):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    a = imd.load('rain', start, end, offline=True)
    if mask_first:
        a.land_mask
    same(a, eager(isolated, 'rain', start, end))


@pytest.mark.parametrize('var', ['tmax', 'tmin'])
@pytest.mark.parametrize('start, end', [(2019, 2020), ('2020-02-28', '2020-03-01')])
@pytest.mark.parametrize('mask_first', [False, True])
def test_archive_temp_same_as_open_data(isolated, var, start, end, mask_first):
    put_archive_eager_names(isolated, var, [2019, 2020])
    a = imd.load(var, start, end, offline=True)
    if mask_first:
        a.land_mask
    same(a, eager(isolated, var, start, end))


@pytest.mark.parametrize('var', ['rain', 'rain_gpm', 'tmax', 'tmin'])
def test_realtime_same_as_open_real_data(isolated, tmp_path, reads, var):
    days = list(pd.date_range('2026-10-03', '2026-10-05'))
    rt = put_realtime(isolated, var, days, tmp_path)
    a = imd.load(var, '2026-10-03', '2026-10-05', source='realtime', offline=True)
    assert a.land_mask is None
    assert reads == {'full': 0, 'cells': 0}
    same(a, imd.open_real_data(var, '2026-10-03', '2026-10-05', rt))


###############################################################################
# Reading selected cells
###############################################################################

@pytest.mark.parametrize('var, start, end', [
    ('rain', 2019, 2020), ('rain', '2019-12-20', '2020-01-10'),
    ('tmax', 2019, 2020), ('tmin', '2020-02-28', '2020-03-01')])
def test_read_cells_without_full_read(isolated, reads, monkeypatch, var, start, end):
    put_archive_eager_names(isolated, var, [2019, 2020])
    full = eager(isolated, var, start, end)
    data = imd.load(var, start, end, offline=True)
    # A partial read never warns about memory
    monkeypatch.setattr(lazy, 'MEMORY_WARNING', 0)
    selections = [(slice(2, 12), slice(5, 15)),            # box
                  (np.array([0, 3, 7]), np.array([1, 2, 9])),  # list of cells
                  (4, 6), (slice(None), 8),
                  # other index forms, as numpy takes them
                  (-1, -2), ([0, 2], [3, 30]), (slice(1, 9, 3), slice(25, 2, -7)),
                  (np.array([5, 5]), slice(None)), (slice(None), np.arange(len(full.lat_array)) % 10 == 4),
                  (3, slice(7, 7)), (slice(None), slice(None))]
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        for lon, lat in selections:
            part = data._read_cells(lon, lat)
            assert part.dtype == np.float64
            assert part.tobytes() == full.data[:, lon, lat].tobytes()
            assert part.shape == full.data[:, lon, lat].shape
            mask = data._land_mask_cells(lon, lat)
            assert np.array_equal(mask, full.land_mask[lon, lat])
            # Built from the values already read: rain needs no second read
            cells = reads['cells']
            mask = data._land_mask_cells(lon, lat, part)
            assert np.array_equal(mask, full.land_mask[lon, lat])
            assert reads['cells'] == cells or var != 'rain'
    assert reads['full'] == 0
    # Mask of the whole grid, without reading all data either
    assert np.array_equal(data.land_mask, full.land_mask)
    assert reads['full'] == 0
    # The same calls on data in memory slice it
    with pytest.warns(UserWarning, match="shorter period"):
        data.data
    assert reads['full'] == 1
    for lon, lat in selections:
        assert np.array_equal(data._read_cells(lon, lat), full.data[:, lon, lat])
        assert np.array_equal(data._land_mask_cells(lon, lat), full.land_mask[lon, lat])
    assert full._read_cells(4, 6).tobytes() == full.data[:, 4, 6].tobytes()


def test_read_cells_realtime(isolated, tmp_path, reads):
    rt = put_realtime(isolated, 'rain', list(pd.date_range('2026-10-03', '2026-10-04')),
                      tmp_path)
    full = imd.open_real_data('rain', '2026-10-03', '2026-10-04', rt)
    data = imd.load('rain', '2026-10-03', '2026-10-04', source='realtime', offline=True)
    assert np.array_equal(data._read_cells(slice(0, 20), slice(30, 40)),
                          full.data[:, 0:20, 30:40])
    assert data._land_mask_cells(slice(0, 20), slice(30, 40)) is None
    assert reads['full'] == 0


def test_read_cells_without_preadv(isolated, monkeypatch):
    """Systems without os.preadv (Windows) seek and read."""
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    full = eager(isolated, 'rain', '2019-12-20', '2020-01-10')
    monkeypatch.setattr(lazy, '_preadv', None)
    data = imd.load('rain', '2019-12-20', '2020-01-10', offline=True)
    for lon, lat in [(np.array([0, 3, 7]), np.array([1, 2, 9])), (slice(None), slice(None))]:
        assert data._read_cells(lon, lat).tobytes() == full.data[:, lon, lat].tobytes()


def test_read_cells_partial_preadv(isolated, monkeypatch):
    """A read that returns fewer bytes than asked is completed by seek and read."""
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    full = eager(isolated, 'rain', '2019-12-20', '2020-01-10')
    calls = []

    def partial(fd, buffers, offset):
        buf = buffers[0]
        os.lseek(fd, offset, os.SEEK_SET)
        got = os.read(fd, max(1, len(buf) // 3))
        buf[:len(got)] = got
        calls.append((len(got), len(buf)))
        return len(got)
    monkeypatch.setattr(lazy, '_preadv', partial)
    data = imd.load('rain', '2019-12-20', '2020-01-10', offline=True)
    for lon, lat in [(np.array([0, 3, 7]), np.array([1, 2, 9])), (slice(None), slice(None))]:
        assert data._read_cells(lon, lat).tobytes() == full.data[:, lon, lat].tobytes()
    assert calls and all(got < size for got, size in calls)


###############################################################################
# Memory warning
###############################################################################

def test_memory_warning_only_at_full_read(isolated, monkeypatch):
    archive(isolated, 'rain', [2020])
    monkeypatch.setattr(lazy, 'MEMORY_WARNING', 1e6)
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        data = imd.load('rain', 2020, offline=True)
        data.land_mask
        data._read_cells(slice(0, 10), slice(0, 10))
        data.copy()
    with pytest.warns(UserWarning, match=r"Loading rain for 366 days needs about 0\.1 GB") as w:
        data.data
    assert w[0].filename == __file__
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        data.data


###############################################################################
# copy(), assignment and methods
###############################################################################

def test_copy_stays_lazy(isolated, reads):
    put_archive_eager_names(isolated, 'rain', [2020])
    data = imd.load('rain', 2020, offline=True)
    dup = data.copy()
    assert reads == {'full': 0, 'cells': 0}
    full = eager(isolated, 'rain', 2020, 2020)
    same(dup, full)
    assert reads['full'] == 1
    # The copy is independent of the original
    dup.data[:] = 0
    same(data, full)
    assert reads['full'] == 2
    # Copy after reading copies the data in memory
    again = data.copy()
    same(again, full)
    assert reads['full'] == 2


def test_assigning_data(isolated, reads):
    put_archive_eager_names(isolated, 'rain', [2020])
    full = eager(isolated, 'rain', 2020, 2020)
    data = imd.load('rain', 2020, offline=True)
    data.data = np.zeros((3, 4, 5))
    assert data.data.shape == (3, 4, 5)
    assert reads['full'] == 0
    # The land mask is still the one of the loaded files
    assert np.array_equal(data.land_mask, full.land_mask)
    data.land_mask = None
    assert data.land_mask is None


def test_methods_on_lazy_objects(isolated):
    put_archive_eager_names(isolated, 'rain', [2019, 2020])
    for method in ('cdd', 'rxa'):
        a = imd.load('rain', 2019, 2020, offline=True).compute(method, 'A')
        b = eager(isolated, 'rain', 2019, 2020).compute(method, 'A')
        assert np.array_equal(a.data, b.data, equal_nan=True)
        assert a.var_name == b.var_name
    a = imd.load('rain', 2019, 2020, offline=True)
    b = eager(isolated, 'rain', 2019, 2020)
    assert a.spatial_mean().equals(b.spatial_mean())
    assert a.get_xarray().equals(b.get_xarray())
    a = imd.load('rain', 2019, 2020, offline=True)
    assert a.climatology().get_xarray().equals(b.copy().climatology().get_xarray())

    put_archive_eager_names(isolated, 'tmax', [2019, 2020])
    a = imd.load('tmax', 2019, 2020, offline=True)
    b = eager(isolated, 'tmax', 2019, 2020)
    a.fill_na()
    b.fill_na()
    same(a, b)


def test_heatwave_on_lazy_object(isolated, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    put_archive_eager_names(isolated, 'tmax', list(range(2001, 2011)) + [2020])
    a = imd.load('tmax', 2020, offline=True).heatwave(norm_start=2001, norm_end=2010)
    b = eager(isolated, 'tmax', 2020, 2020).heatwave(norm_start=2001, norm_end=2010)
    assert np.array_equal(a.data, b.data, equal_nan=True)


###############################################################################
# Files removed before first use
###############################################################################

def test_removed_file_before_first_use(isolated):
    archive(isolated, 'tmax', [2019, 2020])
    data = imd.load('tmax', 2019, 2020, offline=True)
    (isolated / 'archive' / 'tmax' / '2019.grd').unlink()
    with pytest.raises(FileNotFoundError, match=r"2019\.grd has been removed since load\(\)"
                                                r".*Call imdlib\.load\(\) again"):
        data.data
    with pytest.raises(FileNotFoundError, match="load"):
        data._read_cells(0, 0)
    with pytest.raises(FileNotFoundError, match="load"):
        data.copy().land_mask
    # Nothing was kept from the failed read
    put_archive(isolated, 'tmax', 2019)
    assert data.data.shape == (731, 31, 31)


def test_changed_file_before_first_use(isolated):
    archive(isolated, 'tmax', [2020])
    data = imd.load('tmax', 2020, offline=True)
    (isolated / 'archive' / 'tmax' / '2020.grd').write_bytes(b'\0' * 400)
    with pytest.raises(OSError, match="has changed since load"):
        data.data
