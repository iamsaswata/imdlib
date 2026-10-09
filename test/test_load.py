"""
Tests for imdlib.load(), imdlib.cache and the download/reader fixes in
get_data / get_real_data / open_data / open_real_data.

No network access: requests.post is replaced by a fake server.
"""
import array
import functools
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
import requests

import imdlib as imd
from imdlib import cache, lazy, loader
from imdlib.util import DataNotAvailableError, DownloadError

ARCHIVE_URL = {'rain': 'https://imdpune.gov.in/cmpg/Griddata/rainfall.php',
               'tmax': 'https://imdpune.gov.in/cmpg/Griddata/maxtemp.php',
               'tmin': 'https://imdpune.gov.in/cmpg/Griddata/mintemp.php'}
GRID = {('archive', 'rain'): (129, 135), ('archive', 'tmax'): (31, 31),
        ('archive', 'tmin'): (31, 31), ('realtime', 'rain'): (129, 135),
        ('realtime', 'tmax'): (61, 61), ('realtime', 'tmin'): (61, 61),
        ('realtime', 'rain_gpm'): (281, 241)}


###############################################################################
# Helpers
###############################################################################

def days_in(year):
    return 366 if imd.LeapYear(year) else 365


@functools.lru_cache(maxsize=16)
def grd_bytes(var, days, source='archive', seed=0):
    """Synthetic IMD file content (float32, days x lat x lon, C order).

    Cached: the content is deterministic and returned as immutable bytes."""
    nlat, nlon = GRID[(source, var)]
    rng = np.random.default_rng(seed)
    if var == 'rain_gpm':
        arr = rng.gamma(0.5, 5.0, (days, nlat, nlon)).astype('<f4')   # no sentinel
    elif var == 'rain':
        arr = rng.gamma(0.5, 5.0, (days, nlat, nlon)).astype('<f4')
        arr[:, :5, :5] = -999.0       # ocean
        arr[:, 10, 10] = 0.0          # boundary cell without rain
        arr[0, 20, 20] = 0.0          # dry on the first day only: still a land cell
    else:
        arr = (15.0 + 20.0 * rng.random((days, nlat, nlon))).astype('<f4')
        arr[:, :3, :3] = 99.9         # sentinel
    return arr.tobytes()


def year_bytes(var, year):
    return grd_bytes(var, days_in(year), seed=year)


class FakeResponse:
    def __init__(self, content=b'', status=200, chunk=1 << 20):
        self.content = content
        self.status_code = status
        self._chunk = chunk
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                "{} Error".format(self.status_code), response=self)

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), self._chunk):
            yield self.content[i:i + self._chunk]

    def close(self):
        self.closed = True


class FakeServer:
    """Replacement for requests.post; ``handler(url, data)`` returns a
    FakeResponse or raises."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, url, data=None, proxies=None, timeout=None, stream=False, **kw):
        self.calls.append({'url': url, 'data': dict(data), 'timeout': timeout,
                           'stream': stream, 'proxies': proxies})
        return self.handler(url, data)


def archive_server(published=None, empty=(), sizes=None):
    """Serve synthetic archive years; years in ``empty`` give 0 bytes."""
    def handler(url, data):
        var = {v: k for k, v in ARCHIVE_URL.items()}[url]
        year = int(list(data.values())[0])
        if year in empty or (published is not None and year > published):
            return FakeResponse(b'')
        content = year_bytes(var, year)
        if sizes and year in sizes:
            content = content[:sizes[year]]
        return FakeResponse(content)
    return handler


class FakeDate(date):
    @classmethod
    def today(cls):
        return date(2026, 10, 6)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Isolated cache dir, fixed 'today', no network, fast lock polling."""
    monkeypatch.setenv('IMDLIB_CACHE', str(tmp_path / 'cache'))
    monkeypatch.setattr(cache, '_session_dir', None)
    monkeypatch.setattr(cache, 'LOCK_POLL', 0.02)
    monkeypatch.setattr(loader, 'date', FakeDate)

    def no_network(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(requests, 'post', no_network)
    return tmp_path / 'cache'


@pytest.fixture
def server(monkeypatch):
    def install(handler):
        srv = FakeServer(handler)
        monkeypatch.setattr(requests, 'post', srv)
        return srv
    return install


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []

    def wait(cancel, seconds):
        waits.append(seconds)
        return cancel.is_set()
    monkeypatch.setattr(loader, '_wait', wait)
    return waits


def put_archive(root, var, year, content=None):
    path = Path(root) / 'archive' / var / '{}.grd'.format(year)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(year_bytes(var, year) if content is None else content)
    return path


def leftovers(root):
    return [p for p in Path(root).rglob('*') if p.name.endswith('.part')]


###############################################################################
# load(): downloading and caching
###############################################################################

def test_first_load_downloads_only_missing(isolated, server):
    put_archive(isolated, 'tmax', 2018)
    srv = server(archive_server())
    data = imd.load('tmax', 2018, 2020, progress=False)
    assert sorted(c['data']['maxtemp'] for c in srv.calls) == ['2019', '2020']
    assert all(c['url'] == ARCHIVE_URL['tmax'] for c in srv.calls)
    assert all(c['timeout'] >= 300 and c['stream'] for c in srv.calls)
    assert data.data.shape == (365 + 365 + 366, 31, 31)
    for year in (2019, 2020):
        assert (isolated / 'archive' / 'tmax' / '{}.grd'.format(year)).stat().st_size \
            == days_in(year) * 31 * 31 * 4


def test_second_identical_load_makes_no_requests(isolated, server):
    srv = server(archive_server())
    first = imd.load('tmin', 2019, 2020, progress=False)
    assert len(srv.calls) == 2
    srv = server(lambda url, data: pytest.fail("unexpected request"))
    second = imd.load('tmin', 2019, 2020)
    assert srv.calls == []
    assert np.array_equal(first.data, second.data)


def test_offline_missing_files_raise(isolated, server):
    put_archive(isolated, 'tmax', 2018)
    srv = server(archive_server())
    with pytest.raises(FileNotFoundError, match=r"tmax 2019-2020") as e:
        imd.load('tmax', 2018, 2020, offline=True)
    assert '2 files' in str(e.value)
    assert srv.calls == []
    # Cached data works offline
    assert imd.load('tmax', 2018, offline=True).data.shape == (365, 31, 31)


def test_zero_byte_reply_is_not_published(isolated, server, capsys):
    srv = server(archive_server(published=2024))
    with pytest.raises(DataNotAvailableError, match=r"rain 2025 is not published yet") as e:
        imd.load('rain', 2024, 2025)
    # 2025 and 2024 are requested at the same time
    assert {'rain': '2025'} in [c['data'] for c in srv.calls]
    assert "end=2024" in str(e.value)
    assert not (isolated / 'archive' / 'rain' / '2025.grd').exists()
    assert leftovers(isolated) == []
    assert 'archive/rain/2025.grd' not in cache.read_manifest(isolated)['files']
    assert 'rain 2025  not published yet (empty reply)' in capsys.readouterr().out


def test_unpublished_year_reports_latest_available(isolated, server):
    put_archive(isolated, 'tmax', 2024)
    server(archive_server(published=2024))
    with pytest.raises(DataNotAvailableError) as e:
        imd.load('tmax', 2024, 2025, progress=False)
    msg = str(e.value)
    assert "Latest available: 2024. Use end=2024." in msg
    assert "Archive ends 2024-12-31" in msg and "source='realtime'" in msg


def test_current_year_is_refused_without_request(isolated, server):
    srv = server(archive_server())
    with pytest.raises(DataNotAvailableError, match="tmax 2026 is not published yet"):
        imd.load('tmax', 2026)
    with pytest.raises(DataNotAvailableError, match="rain 2026"):
        imd.load('rain', 2024, '2026-03-31')
    assert srv.calls == []


def test_archive_start_years():
    with pytest.raises(ValueError, match="starts in 1901"):
        imd.load('rain', 1900)
    with pytest.raises(ValueError, match="starts in 1951"):
        imd.load('tmin', 1950, 1951)
    with pytest.raises(ValueError, match="var must be"):
        imd.load('tavg', 2020)
    with pytest.raises(ValueError, match="source must be"):
        imd.load('rain', 2020, source='gpm')


def test_wrong_size_reply_leaves_nothing(isolated, server):
    srv = server(archive_server(sizes={2020: 1000}))
    with pytest.raises(DownloadError, match=r"received 1,000 bytes, expected exactly 1,406,904"):
        imd.load('tmax', 2020, progress=False)
    assert len(srv.calls) == 1
    assert list((isolated / 'archive' / 'tmax').iterdir()) == []
    assert 'archive/tmax/2020.grd' not in cache.read_manifest(isolated)['files']


def test_oversized_reply_leaves_nothing(isolated, server):
    big = year_bytes('tmax', 2020) + b'\0' * 8
    srv = server(lambda url, data: FakeResponse(big, chunk=4096))
    with pytest.raises(DownloadError):
        imd.load('tmax', 2020, progress=False)
    assert list((isolated / 'archive' / 'tmax').iterdir()) == []


def test_corrupt_cached_file_is_downloaded_again(isolated, server):
    put_archive(isolated, 'tmax', 2020, content=b'x' * 100)
    srv = server(archive_server())
    imd.load('tmax', 2020, progress=False)
    assert len(srv.calls) == 1
    assert (isolated / 'archive' / 'tmax' / '2020.grd').read_bytes() == year_bytes('tmax', 2020)


def test_retry_after_connection_error(isolated, server, no_sleep, capsys):
    replies = [requests.exceptions.ConnectionError("Connection reset by peer"),
               requests.exceptions.ReadTimeout("read timed out"),
               FakeResponse(year_bytes('tmax', 2020))]

    def handler(url, data):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    srv = server(handler)
    data = imd.load('tmax', 2020)
    assert len(srv.calls) == 3
    assert no_sleep == [5, 15]
    assert data.data.shape == (366, 31, 31)
    out = capsys.readouterr().out
    assert "retrying in 5 s (attempt 2 of 4)" in out


def test_retry_gives_up(isolated, server, no_sleep):
    def handler(url, data):
        raise requests.exceptions.ConnectionError("Connection reset by peer")
    srv = server(handler)
    with pytest.raises(DownloadError, match="after 4 attempts"):
        imd.load('tmax', 2020, progress=False)
    assert len(srv.calls) == 4
    assert no_sleep == [5, 15, 45]
    assert leftovers(isolated) == []


def test_server_error_is_retried_client_error_is_not(isolated, server, no_sleep):
    replies = [FakeResponse(status=503), FakeResponse(year_bytes('tmax', 2020))]
    srv = server(lambda url, data: replies.pop(0))
    imd.load('tmax', 2020, progress=False)
    assert len(srv.calls) == 2

    srv = server(lambda url, data: FakeResponse(status=404))
    with pytest.raises(DownloadError, match="404"):
        imd.load('tmax', 2019, progress=False)
    assert len(srv.calls) == 1


def test_empty_reply_for_published_year_is_a_server_problem(isolated, server, no_sleep):
    # 2018 was published years ago: an empty reply is retried, not "not published"
    replies = [FakeResponse(b''), FakeResponse(year_bytes('rain', 2018))]
    srv = server(lambda url, data: replies.pop(0))
    imd.load('rain', 2018, progress=False)
    assert len(srv.calls) == 2 and no_sleep == [5]

    server(lambda url, data: FakeResponse(b''))
    with pytest.raises(DownloadError) as e:
        imd.load('rain', 2017, progress=False)
    assert "after 4 attempts" in str(e.value)
    assert "IMD returned an empty file, although rain 2017 is published" in str(e.value)
    assert leftovers(isolated) == []


def test_blocked_reply_in_browser_is_not_empty_data(isolated, server, no_sleep):
    """Firefox gives a reply blocked by CORS as HTTP status 0 without content."""
    srv = server(lambda url, data: FakeResponse(b'', status=0))
    with pytest.raises(DownloadError) as e:
        imd.load('rain', 2017, 2018, progress=False)
    msg = str(e.value)
    assert re.match("Could not download rain 201[78]: ", msg)
    assert "CORS" in msg and "JupyterLite" in msg
    assert "not published" not in msg
    assert {'rain': '2018'} in [c['data'] for c in srv.calls]
    assert no_sleep == []
    assert leftovers(isolated) == []
    assert not list(isolated.rglob('*.grd'))


def test_connection_error_in_browser_is_not_retried(isolated, server, no_sleep, monkeypatch):
    """Chromium gives a reply blocked by CORS as a connection error. In
    the browser there are no threads: files are downloaded one at a time."""
    monkeypatch.setattr(sys, 'platform', 'emscripten')
    monkeypatch.setattr(loader, '_start_thread', lambda target: None)

    def handler(url, data):
        raise requests.exceptions.ConnectionError("Failed to execute 'send' on 'XMLHttpRequest'")
    srv = server(handler)
    with pytest.raises(DownloadError, match="does not allow downloads from web pages"):
        imd.load('rain', 2017, 2018)
    assert len(srv.calls) == 1 and no_sleep == []


def test_without_threads_files_are_downloaded_one_at_a_time(isolated, server, monkeypatch,
                                                            capsys):
    monkeypatch.setattr(loader, '_start_thread', lambda target: None)
    srv = server(archive_server(published=2024))
    data = imd.load('tmax', 2022, 2024)
    assert [c['data']['maxtemp'] for c in srv.calls] == ['2024', '2023', '2022']
    assert data.data.shape == (365 + 365 + 366, 31, 31)
    out = capsys.readouterr().out
    assert "tmax 2022-2024  waiting for IMD server..." in out
    assert "tmax 2022  downloaded 1.4 MB in" in out
    assert "tmax 2022-2024  downloaded 4.2 MB in" in out

    with pytest.raises(DataNotAvailableError, match="tmax 2025 is not published yet"):
        imd.load('tmax', 2019, 2025, progress=False)
    # Stops at the first year that is not published
    assert srv.calls[-1]['data'] == {'maxtemp': '2025'}


def test_downloads_run_in_parallel(isolated, server):
    """IMD sends each file slowly: several files are downloaded at a time."""
    lock = threading.Lock()
    running, most = [0], [0]
    handler = archive_server()

    def slow(url, data):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)   # the server takes time to answer
        with lock:
            running[0] -= 1
        return handler(url, data)
    srv = server(slow)
    t0 = time.monotonic()
    data = imd.load('tmax', 2011, 2016, progress=False, parallel=3)
    assert most[0] == 3
    assert time.monotonic() - t0 < 6 * 0.2
    assert len(srv.calls) == 6
    assert data.data.shape[0] == sum(days_in(y) for y in range(2011, 2017))
    assert sorted(cache.read_manifest(isolated)['files']) == \
        ['archive/tmax/{}.grd'.format(y) for y in range(2011, 2017)]

    # One at a time
    most[0] = 0
    imd.load('tmax', 2001, 2003, progress=False, parallel=1)
    assert most[0] == 1
    for bad in (0, 2.5, '4', None):
        with pytest.raises(ValueError, match="parallel must be"):
            imd.load('tmax', 2001, parallel=bad)


class SlowResponse(FakeResponse):
    """Sends its content in small chunks with pauses."""

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), 4096):
            time.sleep(0.01)
            yield self.content[i:i + 4096]


def test_unpublished_year_stops_the_other_downloads(isolated, server):
    def handler(url, data):
        year = int(data['rain'])
        if year == 2025:
            time.sleep(0.05)
            return FakeResponse(b'')
        return SlowResponse(year_bytes('rain', year))
    srv = server(handler)
    t0 = time.monotonic()
    with pytest.raises(DataNotAvailableError, match="rain 2025 is not published yet"):
        imd.load('rain', 2010, 2025, progress=False)
    # Stopped right away: no new downloads, the running ones were aborted
    assert time.monotonic() - t0 < 2
    assert len(srv.calls) == 4   # the default parallel=4
    time.sleep(0.1)
    assert leftovers(isolated) == []
    assert not list(isolated.rglob('*.grd'))
    assert cache.read_manifest(isolated)['files'] == {}


def test_download_error_stops_the_other_downloads(isolated, server, no_sleep):
    serve = archive_server()

    def handler(url, data):
        if data['maxtemp'] == '2018':
            time.sleep(0.3)
            return FakeResponse(status=404)
        time.sleep(0.1)
        return serve(url, data)
    srv = server(handler)
    with pytest.raises(DownloadError, match="tmax 2018.*404"):
        imd.load('tmax', 2001, 2020, progress=False)
    assert len(srv.calls) < 20
    # Files completed before the error stay cached, with their manifest entry
    cached = sorted(p.name for p in (isolated / 'archive' / 'tmax').glob('*.grd'))
    assert cached and '2018.grd' not in cached
    assert sorted(cache.read_manifest(isolated)['files']) == \
        ['archive/tmax/' + name for name in cached]
    assert leftovers(isolated) == []


def test_manifest_entries(isolated, server):
    server(archive_server())
    imd.load('tmax', 2019, 2020, progress=False)
    files = cache.read_manifest(isolated)['files']
    assert sorted(files) == ['archive/tmax/2019.grd', 'archive/tmax/2020.grd']
    entry = files['archive/tmax/2020.grd']
    content = year_bytes('tmax', 2020)
    assert entry['url'] == ARCHIVE_URL['tmax']
    assert entry['post_data'] == {'maxtemp': '2020'}
    assert entry['size'] == len(content)
    assert entry['sha256'] == hashlib.sha256(content).hexdigest()
    assert entry['imdlib_version'] == imd.__version__
    datetime.strptime(entry['downloaded'], '%Y-%m-%dT%H:%M:%SZ')
    # Written atomically: no temporary files left
    assert [p.name for p in isolated.iterdir() if p.is_file()] == ['manifest.json']


def test_proxies_are_passed(isolated, server):
    srv = server(archive_server())
    proxies = {'https': 'http://proxy:8080'}
    imd.load('tmax', 2020, proxies=proxies, progress=False)
    assert srv.calls[0]['proxies'] == proxies


###############################################################################
# Returned object
###############################################################################

def test_load_offline_reuse_and_coordinates(server):
    # The values are compared with open_data() in test_lazy.py
    # (test_archive_rain_same_as_open_data, test_archive_temp_same_as_open_data)
    server(archive_server())
    imd.load('rain', 2019, 2020, progress=False)
    rain = imd.load('rain', 2019, 2020, offline=True)
    assert rain.data.dtype == np.float64
    assert (rain.cat, rain.start_day, rain.end_day, rain.no_days) == \
        ('rain', '2019-01-01', '2020-12-31', 731)
    assert rain.data.shape == (731, 135, 129)
    assert rain.lat_array[0] == 6.5 and rain.lat_array[-1] == 38.5
    assert rain.lon_array[0] == 66.5 and rain.lon_array[-1] == 100.0
    ds = rain.get_xarray()
    assert str(ds.time.values[0])[:10] == '2019-01-01' and ds.sizes['time'] == 731


def test_load_sub_year_range(tmp_path, server):
    yw = tmp_path / 'yearwise' / 'rain'
    yw.mkdir(parents=True)
    (yw / '2020.grd').write_bytes(year_bytes('rain', 2020))
    srv = server(archive_server())
    a = imd.load('rain', '2020-06-01', '2020-09-30', progress=False)
    b = imd.open_data('rain', '2020-06-01', '2020-09-30', 'yearwise', str(tmp_path / 'yearwise'))
    assert len(srv.calls) == 1
    assert a.data.shape == (122, 135, 129)
    assert (a.start_day, a.end_day, a.no_days) == ('2020-06-01', '2020-09-30', 122)
    assert np.array_equal(a.data, b.data)
    assert np.array_equal(a.land_mask, b.land_mask)
    # Same file serves a different sub-range without a request
    c = imd.load('rain', '2020-01-01', '2020-01-31', offline=True)
    assert c.data.shape == (31, 135, 129)


###############################################################################
# Real-time source
###############################################################################

def realtime_server(empty=()):
    names = {'https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/rain.php': 'rain',
             'https://imdpune.gov.in/cmpg/Realtimedata/max/max.php': 'tmax',
             'https://imdpune.gov.in/cmpg/Realtimedata/min/min.php': 'tmin',
             'https://www.imdpune.gov.in/cmpg/Realtimedata/gpm/rain.php': 'rain_gpm'}

    def handler(url, data):
        var = names[url]
        value = list(data.values())[0]
        if value in empty:
            return FakeResponse(b'')
        return FakeResponse(grd_bytes(var, 1, 'realtime', seed=int(value)))
    return handler


def test_realtime_native_grids(isolated, server, tmp_path):
    srv = server(realtime_server())
    t = imd.load('tmax', '2026-10-03', '2026-10-04', source='realtime', progress=False)
    assert sorted(c['data']['max'] for c in srv.calls) == ['03102026', '04102026']
    assert t.data.shape == (2, 61, 61)
    assert len(t.lat_array) == 61 and t.lat_array[0] == 7.5 and t.lat_array[-1] == 37.5
    assert (isolated / 'realtime' / 'tmax' / '2026-10-03.grd').stat().st_size == 61 * 61 * 4

    r = imd.load('rain', '2026-10-05', source='realtime', progress=False)
    assert srv.calls[-1]['data'] == {'rain': '05102026'}
    assert r.data.shape == (1, 135, 129)

    # Same result as open_real_data on the same file
    (tmp_path / 'rt').mkdir()
    (tmp_path / 'rt' / 'max03102026.grd').write_bytes(
        (isolated / 'realtime' / 'tmax' / '2026-10-03.grd').read_bytes())
    o = imd.open_real_data('tmax', '2026-10-03', None, str(tmp_path / 'rt'))
    a = imd.load('tmax', '2026-10-03', source='realtime', offline=True)
    assert np.array_equal(a.data, o.data) and a.data.dtype == np.float64
    assert np.array_equal(a.lat_array, o.lat_array) and np.array_equal(a.lon_array, o.lon_array)
    assert a.land_mask is None and o.land_mask is None


def test_realtime_gpm(isolated, server, tmp_path):
    srv = server(realtime_server())
    g = imd.load('rain_gpm', '2026-10-04', '2026-10-05', source='realtime', progress=False)
    assert srv.calls[0]['url'] == 'https://www.imdpune.gov.in/cmpg/Realtimedata/gpm/rain.php'
    assert sorted(c['data']['rain'] for c in srv.calls) == ['04102026', '05102026']
    assert g.data.shape == (2, 241, 281) and g.cat == 'rain_gpm'
    assert (isolated / 'realtime' / 'rain_gpm' / '2026-10-04.grd').stat().st_size == 281 * 241 * 4

    # Same result as open_real_data on the same file
    (tmp_path / 'rt').mkdir()
    (tmp_path / 'rt' / '04102026.grd').write_bytes(
        (isolated / 'realtime' / 'rain_gpm' / '2026-10-04.grd').read_bytes())
    o = imd.open_real_data('rain_gpm', '2026-10-04', None, str(tmp_path / 'rt'))
    a = imd.load('rain_gpm', '2026-10-04', source='realtime', offline=True)
    assert np.array_equal(a.data, o.data)
    assert np.array_equal(a.lat_array, o.lat_array) and np.array_equal(a.lon_array, o.lon_array)


def test_realtime_gpm_not_available(isolated, server):
    server(realtime_server(empty={'06102026', '01012015'}))
    with pytest.raises(DataNotAvailableError, match="about 1 day late"):
        imd.load('rain_gpm', '2026-10-06', source='realtime', progress=False)
    with pytest.raises(DataNotAvailableError) as e:
        imd.load('rain_gpm', '2015-01-01', source='realtime', progress=False)
    assert "source='archive'" not in str(e.value)


def test_gpm_is_realtime_only(isolated, server):
    srv = server(realtime_server())
    with pytest.raises(ValueError, match="source='realtime'"):
        imd.load('rain_gpm', 2020)
    assert srv.calls == []


def test_cache_info_and_clear_gpm(isolated, server, capsys):
    server(realtime_server())
    imd.load('rain_gpm', '2026-10-04', source='realtime', progress=False)
    cache.info()
    assert "realtime rain_gpm  2026-10-04  (1 file" in capsys.readouterr().out
    cache.clear('rain_gpm')
    assert "Removing realtime/rain_gpm: 2026-10-04" in capsys.readouterr().out
    assert not list((isolated / 'realtime' / 'rain_gpm').iterdir())


def test_realtime_recent_days_not_published(isolated, server):
    srv = server(realtime_server(empty={'05102026', '06102026'}))
    with pytest.raises(DataNotAvailableError) as e:
        imd.load('tmax', '2026-10-03', '2026-10-06', source='realtime', progress=False)
    msg = str(e.value)
    assert "2026-10-05 to 2026-10-06" in msg
    assert "may not be published yet" in msg and "2 days late" in msg
    assert "end='2026-10-04'" in msg
    # Recent days: the earlier days are still downloaded (and cached)
    assert sorted(c['data']['max'] for c in srv.calls) == ['03102026', '04102026',
                                                           '05102026', '06102026']
    assert sorted(p.name for p in (isolated / 'realtime' / 'tmax').iterdir()) == \
        ['2026-10-03.grd', '2026-10-04.grd']
    assert sorted(cache.read_manifest(isolated)['files']) == \
        ['realtime/tmax/2026-10-03.grd', 'realtime/tmax/2026-10-04.grd']
    srv = server(lambda url, data: pytest.fail("unexpected request"))
    imd.load('tmax', '2026-10-03', '2026-10-04', source='realtime', progress=False)


def test_realtime_old_day_not_available(isolated, server):
    srv = server(realtime_server(empty={'06102021'}))
    with pytest.raises(DataNotAvailableError, match="source='archive'"):
        imd.load('rain', '2021-10-05', '2021-10-06', source='realtime', progress=False)
    assert {'rain': '06102021'} in [c['data'] for c in srv.calls]


def test_realtime_gap_is_nan_with_warning(isolated, server):
    """A day missing at IMD between available days is NaN, with a warning."""
    srv = server(realtime_server(empty={'22092026'}))
    with pytest.warns(UserWarning, match=r"not available at IMD for 2026-09-22; this day is NaN"):
        data = imd.load('rain', '2026-09-20', '2026-09-25', source='realtime', progress=False)
    assert len(srv.calls) == 6
    # Cell reads (region, clip) give NaN for the day as well
    assert np.isnan(data._source.read_cells(slice(0, 5), slice(0, 5))[2]).all()
    values = data.data
    assert values.shape == (6, 135, 129)
    assert np.isnan(values[2]).all()
    assert not np.isnan(values[[0, 1, 3, 4, 5]]).any()
    # Same values for the other days as when loaded without the gap
    other = imd.load('rain', '2026-09-23', '2026-09-25', source='realtime', offline=True)
    assert np.array_equal(values[3:], other.data)
    assert not (isolated / 'realtime' / 'rain' / '2026-09-22.grd').exists()


def test_realtime_recent_gap_is_nan(isolated, server):
    server(realtime_server(empty={'04102026'}))
    with pytest.warns(UserWarning, match="2026-10-04"):
        data = imd.load('tmax', '2026-10-03', '2026-10-05', source='realtime', progress=False)
    assert np.isnan(data.data[1]).all() and not np.isnan(data.data[2]).any()


def test_realtime_missing_period_stops(isolated, server):
    """Days that are all empty (not on the server) stop the downloads."""
    srv = server(realtime_server(empty={'{:02d}012015'.format(d) for d in range(1, 32)}))
    with pytest.raises(DataNotAvailableError, match="limited period"):
        imd.load('rain', '2015-01-01', '2015-01-31', source='realtime', progress=False)
    assert len(srv.calls) < 31


def test_realtime_future_days_refused(isolated, server):
    srv = server(realtime_server())
    with pytest.raises(DataNotAvailableError, match="2026-10-07 to 2026-10-08.*future"):
        imd.load('rain', '2026-10-05', '2026-10-08', source='realtime')
    assert srv.calls == []


###############################################################################
# Progress and memory warning
###############################################################################

def test_progress_plain_output(isolated, server, capsys):
    server(archive_server())
    imd.load('tmax', 2020)
    out = capsys.readouterr().out
    assert "Downloading 1 file from IMD into {}".format(isolated) in out
    assert "tmax 2020  waiting for IMD server..." in out
    assert "tmax 2020  downloaded 1.4 MB in" in out
    assert '\r' not in out


def test_progress_false_is_silent(isolated, server, capsys):
    server(archive_server())
    imd.load('tmax', 2020, progress=False)
    assert capsys.readouterr().out == ''


def rain_files(*years):
    root = cache.get_dir()
    return [loader._File(root, 'archive', 'rain', y, str(y), 25_425_900, '', '')
            for y in years]


def test_progress_tty_bar(capsys, monkeypatch):
    monkeypatch.setattr(loader, '_is_tty', lambda: True)
    p = loader._Progress()
    assert p.mode == 'tty'
    p._full, p._empty, p._dots = '#', '-', '...'
    f, = rain_files(2023)
    p.start([f])
    p.waiting(f)
    p.update(f, 12_712_950)
    p.done(f, "downloaded 25.4 MB")
    p.finish()
    out = capsys.readouterr().out
    assert "\rrain 2023  waiting for IMD server... 0s" in out
    assert "\rrain 2023  ############------------  12.7 / 25.4 MB" in out
    assert "files" not in out
    assert re.search("\rrain 2023  downloaded 12.7 MB in 0 s *\n$", out)


def test_progress_tty_several_files(capsys, monkeypatch):
    monkeypatch.setattr(loader, '_is_tty', lambda: True)
    p = loader._Progress()
    p._full, p._empty, p._dots = '#', '-', '...'
    a, b = files = rain_files(2018, 2017)
    p.start(files)
    for f in files:
        p.waiting(f)
    p.update(a, 25_425_900)
    p.done(a, "downloaded 25.4 MB")
    p.update(b, 100)
    # The server sends nothing for a while
    p._data_time -= 7
    p._render()
    p.message("rain 2017  retrying")
    p.done(b, "failed")
    p.finish()
    out = capsys.readouterr().out
    assert "rain 2017-2018  ############------------  25.4 / 50.9 MB  1 of 2 files" in out
    assert "waiting for IMD server... 7s" in out
    # Messages come on a line of their own, then the bar is drawn again
    assert re.search("\rrain 2017  retrying *\n\rrain 2017-2018  ####", out)
    assert "rain 2017  failed in" in out
    assert re.search("\rrain 2017-2018  stopped: 1 of 2 files downloaded *\n$", out)


def test_progress_group_label():
    assert loader._group_label(rain_files(2017, 2018)) == 'rain 2017-2018'
    assert loader._group_label(rain_files(2010, 2012, 2014)) == 'rain 2010, 2012, 2014'
    assert loader._group_label(rain_files(*range(1901, 2020, 2))) == 'rain 60 files'


@pytest.mark.parametrize('shell, notebook', [
    ('ZMQInteractiveShell', True),        # Jupyter (ipykernel)
    ('Shell', True),                      # Google Colab
    ('Interpreter', True),                # JupyterLite, Pyodide kernel
    ('XPythonShell', True),               # JupyterLite, Xeus kernel
    ('TerminalInteractiveShell', False),  # IPython in a terminal
    (None, False),                        # plain Python
])
def test_in_notebook(monkeypatch, shell, notebook):
    import IPython
    instance = None if shell is None else type(shell, (), {})()
    monkeypatch.setattr(IPython, 'get_ipython', lambda: instance)
    assert loader._in_notebook() is notebook


def test_memory_warning():
    # When the warning comes (at the full read) is tested in test_lazy.py
    with pytest.warns(UserWarning, match=r"about 7\.2 GB"):
        lazy.warn_memory('rain', 45656, 45656, 129 * 135)


###############################################################################
# Cache location
###############################################################################

def test_cache_dir_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv('IMDLIB_CACHE', str(tmp_path / 'env'))
    assert cache.get_dir() == tmp_path / 'env'
    cache.set_dir(tmp_path / 'session')
    assert cache.get_dir() == tmp_path / 'session'
    assert cache.get_dir(tmp_path / 'arg') == tmp_path / 'arg'
    cache.set_dir(None)
    monkeypatch.delenv('IMDLIB_CACHE')
    assert cache.get_dir() == cache.default_dir()


def test_load_uses_cache_dir_argument(tmp_path, server, isolated):
    server(archive_server())
    imd.load('tmax', 2020, cache_dir=tmp_path / 'mine', progress=False)
    assert (tmp_path / 'mine' / 'archive' / 'tmax' / '2020.grd').exists()
    assert not isolated.exists()


def test_default_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setenv('XDG_CACHE_HOME', '/xdg')
    assert cache.default_dir() == Path('/xdg/imdlib')
    monkeypatch.setenv('XDG_CACHE_HOME', 'relative')
    assert cache.default_dir() == tmp_path / 'home' / '.cache' / 'imdlib'
    monkeypatch.delenv('XDG_CACHE_HOME')
    assert cache.default_dir() == tmp_path / 'home' / '.cache' / 'imdlib'

    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert cache.default_dir() == tmp_path / 'home' / 'Library' / 'Caches' / 'imdlib'

    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setenv('LOCALAPPDATA', r'C:\Users\me\AppData\Local')
    assert cache.default_dir() == Path(r'C:\Users\me\AppData\Local') / 'imdlib' / 'Cache'
    monkeypatch.delenv('LOCALAPPDATA')
    assert cache.default_dir() == tmp_path / 'home' / 'AppData' / 'Local' / 'imdlib' / 'Cache'


###############################################################################
# info / clear
###############################################################################

def test_cache_info(isolated, server, capsys):
    cache.info()
    assert "(empty)" in capsys.readouterr().out
    server(archive_server())
    imd.load('tmax', 2018, 2020, progress=False)
    cache.info()
    out = capsys.readouterr().out
    assert "IMDLIB cache: {}".format(isolated) in out
    assert "archive  tmax  2018-2020  (3 files, 4.2 MB, downloaded 20" in out
    assert "Total: 3 files" in out and "manifest.json" in out


def test_cache_clear_selected(isolated, server, capsys):
    server(archive_server())
    imd.load('tmax', 2019, 2020, progress=False)
    imd.load('tmin', 2019, progress=False)
    part = isolated / 'archive' / 'tmax' / '2019.grd.part'
    part.write_bytes(b'junk')
    part2 = isolated / 'archive' / 'tmax' / '2019.grd.0123abcd.part'
    part2.write_bytes(b'junk')
    cache.clear('tmax', 2019)
    out = capsys.readouterr().out
    assert "Removing archive/tmax: 2019 (3 files, 1.4 MB)" in out
    assert "Freed 1.4 MB." in out
    assert not (isolated / 'archive' / 'tmax' / '2019.grd').exists()
    assert not part.exists() and not part2.exists()
    assert (isolated / 'archive' / 'tmax' / '2020.grd').exists()
    assert (isolated / 'archive' / 'tmin' / '2019.grd').exists()
    assert sorted(cache.read_manifest(isolated)['files']) == \
        ['archive/tmax/2020.grd', 'archive/tmin/2019.grd']

    cache.clear('rain')
    assert "Nothing to remove." in capsys.readouterr().out

    cache.clear()
    out = capsys.readouterr().out
    assert "Removing archive/tmax: 2020" in out and "Removing archive/tmin: 2019" in out
    assert cache.read_manifest(isolated)['files'] == {}
    assert list((isolated / 'archive').rglob('*.grd')) == []
    with pytest.raises(ValueError):
        cache.clear('rainfall')


def test_cache_clear_realtime_by_year(isolated, server, capsys):
    server(realtime_server())
    imd.load('tmax', '2026-10-03', '2026-10-04', source='realtime', progress=False)
    put_archive(isolated, 'tmax', 2025)
    cache.clear(years=2026, source='realtime')
    out = capsys.readouterr().out
    assert "Removing realtime/tmax: 2026-10-03 to 2026-10-04 (2 files, 29.8 kB)" in out
    assert (isolated / 'archive' / 'tmax' / '2025.grd').exists()


###############################################################################
# Lock
###############################################################################

def write_lock(root, **info):
    root.mkdir(parents=True, exist_ok=True)
    data = {'pid': os.getpid(), 'host': socket.gethostname(),
            'started': 'earlier', 'time': time.time()}
    data.update(info)
    (root / '.lock').write_text(json.dumps(data))


def test_lock_second_holder_waits(isolated):
    messages = []
    first = cache.CacheLock(isolated)
    first.acquire()
    acquired = threading.Event()

    def second():
        with cache.CacheLock(isolated, say=messages.append):
            acquired.set()
    t = threading.Thread(target=second)
    t.start()
    time.sleep(0.2)
    assert not acquired.is_set()
    assert messages and "Another imdlib download is running (pid {}".format(os.getpid()) \
        in messages[0]
    first.release()
    t.join(5)
    assert acquired.is_set()
    assert not (isolated / '.lock').exists()


def test_lock_stale_dead_pid_removed(isolated):
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    write_lock(isolated, pid=proc.pid)
    messages = []
    with cache.CacheLock(isolated, say=messages.append):
        info = json.loads((isolated / '.lock').read_text())
        assert info['pid'] == os.getpid()
    assert "no longer running" in messages[0]


def test_lock_stale_old_removed(isolated):
    write_lock(isolated, time=time.time() - 25 * 3600)   # own pid: alive
    messages = []
    with cache.CacheLock(isolated, say=messages.append):
        pass
    assert "older than 24 h" in messages[0]


def test_lock_other_host_is_respected(isolated):
    write_lock(isolated, host='another-computer', pid=1)
    info = cache._read_lock(isolated / '.lock')
    assert cache._stale_reason(isolated / '.lock', info) is None
    write_lock(isolated, host='another-computer', pid=1, time=time.time() - 25 * 3600)
    info = cache._read_lock(isolated / '.lock')
    assert cache._stale_reason(isolated / '.lock', info) is not None


def test_unlock(isolated, capsys):
    write_lock(isolated, pid=12345, host='box', started='2026-10-06 10:00:00 UTC')
    cache.unlock()
    assert "pid 12345, host box, started 2026-10-06 10:00:00 UTC" in capsys.readouterr().out
    assert not (isolated / '.lock').exists()
    cache.unlock()
    assert "No lock file" in capsys.readouterr().out


def test_load_waits_for_lock_then_skips_downloaded(isolated, server, capsys):
    """A second load waits for the lock; files downloaded meanwhile are not fetched again."""
    srv = server(archive_server())
    holder = cache.CacheLock(isolated)
    holder.acquire()
    done = threading.Event()

    def run():
        imd.load('tmax', 2020)
        done.set()
    t = threading.Thread(target=run)
    t.start()
    time.sleep(0.2)
    assert not done.is_set()
    put_archive(isolated, 'tmax', 2020)   # the "other process" finished it
    holder.release()
    t.join(5)
    assert done.is_set()
    assert srv.calls == []
    assert "Another imdlib download is running" in capsys.readouterr().out


def test_pid_alive_posix():
    assert cache.pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    assert cache.pid_alive(proc.pid) is False
    assert cache.pid_alive(None) is None


class FakeKernel32:
    def __init__(self, handle, exit_code=259):
        self.handle = handle
        self.exit_code = exit_code
        self.closed = False

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed = True


def test_pid_alive_windows(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    k = FakeKernel32(handle=7, exit_code=259)
    monkeypatch.setattr(cache, '_kernel32', lambda: (k, lambda: 0))
    assert cache.pid_alive(1234) is True and k.closed
    k = FakeKernel32(handle=7, exit_code=0)
    monkeypatch.setattr(cache, '_kernel32', lambda: (k, lambda: 0))
    assert cache.pid_alive(1234) is False
    monkeypatch.setattr(cache, '_kernel32', lambda: (FakeKernel32(handle=0), lambda: 87))
    assert cache.pid_alive(1234) is False
    monkeypatch.setattr(cache, '_kernel32', lambda: (FakeKernel32(handle=0), lambda: 5))
    assert cache.pid_alive(1234) is True

    def broken():
        raise OSError("no ctypes")
    monkeypatch.setattr(cache, '_kernel32', broken)
    assert cache.pid_alive(1234) is None
    # Unknown liveness: only the age rule applies
    assert cache._stale_reason(Path('x'), {'pid': 1234, 'host': socket.gethostname(),
                                           'time': time.time()}) is None
    assert cache._stale_reason(Path('x'), {'pid': 1234, 'host': socket.gethostname(),
                                           'time': time.time() - 25 * 3600}) is not None


###############################################################################
# Fixes in the existing functions
###############################################################################

def old_reader(fname, days, nlat, nlon):
    """The reader used by open_data before the np.fromfile change."""
    temp = array.array("f")
    with open(fname, 'rb') as f:
        temp.fromfile(f, os.stat(fname).st_size // temp.itemsize)
    data = np.array(list(map(lambda x: x, temp)))
    return np.transpose(np.reshape(data, (days, nlat, nlon), order='C'), (0, 2, 1))


def test_fast_reader_bit_identical(tmp_path):
    (tmp_path / 'tmax').mkdir()
    content = year_bytes('tmax', 2020)
    rng = np.random.default_rng(1)
    # include awkward float32 values
    values = np.frombuffer(content, '<f4').copy()
    values[:10] = [np.float32(0.1), -0.0, 1e-40, 3.4e38, -999.0, 99.9, 1 / 3, 7.7, 1e-7, 0]
    values[10:1000] = rng.standard_normal(990).astype('<f4')
    path = tmp_path / 'tmax' / '2020.GRD'
    path.write_bytes(values.tobytes())
    new = imd.open_data('tmax', 2020, 2020, 'yearwise', str(tmp_path))
    old = old_reader(path, 366, 31, 31)
    assert new.data.dtype == old.dtype == np.float64
    assert new.data.tobytes() == np.ascontiguousarray(old).tobytes()

    rt = tmp_path / 'rt'
    rt.mkdir()
    (rt / 'min05102026.grd').write_bytes(grd_bytes('tmin', 1, 'realtime', seed=3))
    new = imd.open_real_data('tmin', '2026-10-05', None, str(rt))
    old = old_reader(rt / 'min05102026.grd', 1, 61, 61)
    assert new.data.dtype == np.float64
    assert new.data.tobytes() == np.ascontiguousarray(old).tobytes()


def test_open_data_wrong_size_still_raises(tmp_path):
    (tmp_path / 'tmax').mkdir()
    (tmp_path / 'tmax' / '2020.GRD').write_bytes(b'\0' * 400)
    with pytest.raises(Exception, match="mismatch in size"):
        imd.open_data('tmax', 2020, 2020, 'yearwise', str(tmp_path))


def test_get_data_refuses_empty_file(tmp_path, server):
    srv = server(lambda url, data: FakeResponse(b''))
    with pytest.raises(DataNotAvailableError, match="tmax 2025 is not published yet"):
        imd.get_data('tmax', 2025, 2025, 'yearwise', str(tmp_path))
    assert len(srv.calls) == 1
    assert list((tmp_path / 'tmax').iterdir()) == []


def test_get_data_refuses_wrong_size(tmp_path, server):
    server(lambda url, data: FakeResponse(b'\0' * 1000))
    with pytest.raises(DownloadError, match="expected exactly"):
        imd.get_data('tmax', 2020, 2020, 'yearwise', str(tmp_path))
    assert list((tmp_path / 'tmax').iterdir()) == []


def test_get_data_blocked_by_browser(tmp_path, server):
    server(lambda url, data: FakeResponse(b'', status=0))
    with pytest.raises(DownloadError, match="CORS"):
        imd.get_data('tmax', 2020, 2020, 'yearwise', str(tmp_path))
    with pytest.raises(DownloadError, match="CORS"):
        imd.get_real_data('tmax', '2026-10-05', None, str(tmp_path))


def test_get_data_saves_valid_file(tmp_path, server):
    srv = server(archive_server())
    data = imd.get_data('tmax', 2020, 2020, 'yearwise', str(tmp_path))
    assert srv.calls[0]['data'] == {'maxtemp': 2020}
    assert (tmp_path / 'tmax' / '2020.GRD').read_bytes() == year_bytes('tmax', 2020)
    assert data.data.shape == (366, 31, 31)
    assert leftovers(tmp_path) == []


def test_get_real_data_validates_size(tmp_path, server):
    server(lambda url, data: FakeResponse(b''))
    with pytest.raises(DataNotAvailableError, match="not available"):
        imd.get_real_data('tmax', '2026-10-05', None, str(tmp_path))
    server(lambda url, data: FakeResponse(b'\0' * 2000))
    with pytest.raises(DownloadError):
        imd.get_real_data('tmax', '2026-10-05', None, str(tmp_path))
    assert list(tmp_path.iterdir()) == []
    server(realtime_server())
    data = imd.get_real_data('tmax', '2026-10-05', None, str(tmp_path))
    assert data.data.shape == (1, 61, 61)
    assert [p.name for p in tmp_path.iterdir()] == ['max05102026.grd']


###############################################################################
# heatwave()/coldwave(): normal period outside the loaded data
###############################################################################

def test_heatwave_normal_period_outside_data_uses_cache(isolated, server, tmp_path,
                                                        monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    for year in (2019, 2020):
        put_archive(isolated, 'tmax', year)
    srv = server(archive_server())
    data = imd.load('tmax', 2019, 2020, offline=True)
    hw = data.heatwave(output='daily', norm_start=2001, norm_end=2010)
    assert sorted(int(c['data']['maxtemp']) for c in srv.calls) == list(range(2001, 2011))
    for year in range(2001, 2011):
        assert (isolated / 'archive' / 'tmax' / '{}.grd'.format(year)).stat().st_size \
            == days_in(year) * 31 * 31 * 4
    assert list(work.iterdir()) == []
    assert hw.data.shape == (731, 31, 31)
    # Same result as with the normal period inside the loaded data
    srv = server(archive_server())
    full = imd.load('tmax', 2001, 2020, progress=False)
    ref = full.heatwave(output='daily', norm_start=2001, norm_end=2010)
    assert np.array_equal(ref.data[-731:], hw.data, equal_nan=True)
    assert list(work.iterdir()) == []


def test_heatwave_cached_normal_period_makes_no_requests(isolated, server, tmp_path,
                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    for year in list(range(2001, 2011)) + [2020]:
        put_archive(isolated, 'tmin', year)
    srv = server(lambda url, data: pytest.fail("unexpected request"))
    data = imd.load('tmin', 2020, offline=True)
    cw = data.coldwave(output='annual', norm_start=2001, norm_end=2010)
    assert srv.calls == []
    assert cw.data.shape == (1, 31, 31)


def test_heatwave_normal_period_errors_are_raised(isolated, server, tmp_path, monkeypatch,
                                                  no_sleep):
    monkeypatch.chdir(tmp_path)
    put_archive(isolated, 'tmax', 2020)
    server(archive_server(empty=(2010,)))
    data = imd.load('tmax', 2020, offline=True)
    with pytest.raises(DownloadError, match=r"empty file, although tmax 2010 is published"):
        data.heatwave(norm_start=2001, norm_end=2010)
    server(lambda url, data: FakeResponse(b'\0' * 1000))
    with pytest.raises(DownloadError, match="expected exactly"):
        imd.load('tmax', 2020, offline=True).heatwave(norm_start=1991, norm_end=2000)
