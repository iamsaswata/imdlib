"""
``imdlib.load()``: download, validate, cache and read IMD gridded data in one call.
"""

import hashlib
import numbers
import os
import queue
import sys
import threading
import time
import uuid
import warnings
from collections import deque
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from imdlib import cache
from imdlib.core import _open_archive
# Read-only alias: the threshold is set in imdlib.lazy (MEMORY_WARNING there)
from imdlib.lazy import MEMORY_WARNING  # noqa: F401
from imdlib.real import _open_realtime
from imdlib.util import (GRIDS, ARCHIVE_GRID, ARCHIVE_GRIDS, ARCHIVE_URLS, REALTIME_GRID,
                         REALTIME_GRIDS, REALTIME_URLS,
                         DataNotAvailableError, DownloadError, LeapYear,
                         check_download_size, parse_date_input, post)
from imdlib.version import __version__

# First year of the archive datasets
ARCHIVE_START = {'rain': 1901, 'tmin': 1951, 'tmax': 1951}
# Typical publication lag of real-time data in days (observed October 2026).
# Only used in error messages.
REALTIME_LAG = {'rain': 1, 'rain_gpm': 1, 'tmin': 1, 'tmax': 2}
# Real-time days at most this old are reported as "may not be published yet"
RECENT_DAYS = 7

# The IMD server takes 10-110 s to answer, even for empty replies
TIMEOUT = 300
# Seconds to wait for the other downloads to stop after one has failed
STOP_WAIT = 2
# Waits (s) before each retry after a connection error, timeout or server error
RETRY_WAITS = (5, 15, 45)
CHUNK_SIZE = 64 * 1024


def load(var, start, end=None, *, source='archive', cache_dir=None, offline=False,
         proxies=None, progress=True, parallel=4):
    """
    Load IMD gridded data, downloading and caching the files as needed.

    Files missing from the local cache are downloaded (in parallel, with
    progress), checked for their exact size, and stored in the cache. Later
    calls for the same period use the cached files and do not download again.

    ``load()`` checks the request and downloads the files, but reads them
    only when the data is first used (e.g. ``data.data``, ``get_xarray()``
    or ``compute()``).

    Parameters
    ----------
    var : str
        Four possible values.
        1. "rain" -> daily rainfall
        2. "tmin" -> daily minimum temperature
        3. "tmax" -> daily maximum temperature
        4. "rain_gpm" -> daily GPM rainfall (real-time only)

    start : int or str
        First year (e.g. 2020) or day ('YYYY-MM-DD') to load.

    end : int or str or None
        Last year or day to load (inclusive). If None, ``end = start``.

    source : str
        "archive" (default): quality-controlled yearly files
        (rain 0.25° from 1901, tmin/tmax 1.0° from 1951).
        "realtime": provisional daily files on their native grid
        (rain 0.25°, tmin/tmax 0.5°, rain_gpm 0.25° over 30°S-40°N,
        50°E-110°E).

    cache_dir : str or path-like or None
        Cache directory. If None, the directory set with
        ``imdlib.cache.set_dir()``, the ``IMDLIB_CACHE`` environment
        variable or the default user cache directory is used.

    offline : bool
        If True, never use the network; raise an error listing the files
        that are not in the cache.

    proxies : dict or None
        Proxies passed to ``requests``,
        e.g. proxies = { 'https' : 'http://uname:password@ip:port'}

    progress : bool
        Show download progress (default True).

    parallel : int
        Number of files downloaded at the same time (default 4). The IMD
        server sends each file slowly, but not slower for several files.

    Returns
    -------
    IMD object
        Its data is read from the cached files on first use.

    Raises
    ------
    ValueError
        Invalid variable, source or period (e.g. rain before 1901).
    DataNotAvailableError
        IMD has not published (part of) the requested period. Nothing is
        returned; files downloaded before the error stay cached. Real-time
        days missing at IMD between available days do not raise this
        error: they are NaN, with a warning.
    DownloadError
        A download failed after retries, or the file received has the
        wrong size.
    FileNotFoundError
        ``offline=True`` and files are missing from the cache. Also raised
        on first use of the data if its files were removed from the cache
        after ``load()``.

    Examples
    --------
    >>> import imdlib as imd
    >>> data = imd.load('rain', 2020, 2022)
    >>> data = imd.load('tmax', '2023-04-01', '2023-06-30')
    >>> data = imd.load('rain', '2026-10-01', '2026-10-05', source='realtime')
    >>> data = imd.load('rain_gpm', '2026-10-01', '2026-10-05', source='realtime')
    """
    if source not in ('archive', 'realtime'):
        raise ValueError("source must be 'archive' or 'realtime', got {!r}.".format(source))
    if var not in REALTIME_GRIDS:
        raise ValueError("var must be 'rain', 'tmin', 'tmax' or 'rain_gpm', got {!r}."
                         .format(var))
    if source == 'archive' and var not in ARCHIVE_GRIDS:
        raise ValueError("{} is available as real-time data only: use source='realtime'."
                         .format(var))
    if not isinstance(parallel, numbers.Integral) or parallel < 1:
        raise ValueError("parallel must be a whole number of at least 1, got {!r}."
                         .format(parallel))

    start_day, end_day, start_yr, end_yr = parse_date_input(start, end)
    root = cache.get_dir(cache_dir)
    today = date.today()

    if source == 'archive':
        _check_archive_period(var, start_yr, end_yr, root, today)
        grid = GRIDS[ARCHIVE_GRID[var]]
        url, field = ARCHIVE_URLS[var]
        files = [_File(root, 'archive', var, year, str(year),
                       grid.file_size(366 if LeapYear(year) else 365), url, field)
                 for year in range(start_yr, end_yr + 1)]
        # IMD publishes a year in the following year: an empty reply for an
        # older year is a server problem, not a missing year
        for f in files:
            f.may_be_unpublished = f.period >= today.year - 1
    else:
        grid = GRIDS[REALTIME_GRID[var]]
        url, field = REALTIME_URLS[var]
        dates = pd.date_range(start_day, end_day, freq='D')
        future = [d for d in dates if d.date() > today]
        if future:
            raise DataNotAvailableError(
                "Real-time {} is not available for {}: these dates are in the future."
                .format(var, _format_days(future)))
        files = [_File(root, 'realtime', var, d, d.strftime('%Y-%m-%d'),
                       grid.file_size(), url, field, d.strftime('%d%m%Y'))
                 for d in dates]

    missing = [f for f in files if not f.is_cached()]
    if missing:
        if offline:
            raise FileNotFoundError(
                "offline=True, but {} file{} {} not in the cache ({}): {} {}. "
                "Call load() with offline=False to download {}.".format(
                    len(missing), '' if len(missing) == 1 else 's',
                    'is' if len(missing) == 1 else 'are', root, var,
                    _format_periods(source, missing),
                    'it' if len(missing) == 1 else 'them'))
        unavailable = _download_missing(root, missing, proxies, _Progress(progress), today,
                                        parallel)
        if unavailable and source == 'archive':
            f = unavailable[0]
            raise DataNotAvailableError(
                "{} is not published yet (IMD returned an empty file). ".format(f.label)
                + _archive_hint(var, f.period, start_yr, root))
        if unavailable:
            # Days missing before an available day are gaps at IMD: NaN
            last = max((f.period for f in files if f.is_cached()), default=None)
            gaps = [f for f in unavailable if last is not None and f.period < last]
            at_end = [f for f in unavailable if f not in gaps]
            if at_end:
                raise DataNotAvailableError(_realtime_message(var, at_end, today))
            warnings.warn("Real-time {} is not available at IMD for {}; {} NaN.".format(
                var, _format_days([f.period for f in gaps]),
                'this day is' if len(gaps) == 1 else 'these days are'), stacklevel=2)
            for f in gaps:
                f.path = None

    # The files are read when the data is first used
    paths = {f.period: f.path for f in files}
    if source == 'archive':
        return _open_archive(var, start_day, end_day, start_yr, end_yr,
                             lambda year: paths[year], lazy=True)
    return _open_realtime(var, start_day, end_day, lambda day: paths[day], lazy=True)


class _File:
    """One file of the cache: where it is and where to download it from."""

    def __init__(self, root, source, var, period, name, expected, url, field, value=None):
        self.source = source
        self.var = var
        self.period = period
        self.name = name
        self.path = cache.file_path(root, source, var, period)
        self.key = cache.manifest_key(source, var, self.path.name)
        self.expected = expected
        self.url = url
        self.field = field
        self.value = name if value is None else value
        self.label = '{} {}'.format(var, name)
        # Whether an empty reply means "no data for this period"
        self.may_be_unpublished = True

    def is_cached(self):
        try:
            return self.path.stat().st_size == self.expected
        except OSError:
            return False


###############################################################################
# Checks
###############################################################################

def _check_archive_period(var, start_yr, end_yr, root, today):
    first = ARCHIVE_START[var]
    if start_yr < first:
        raise ValueError("The IMD {} archive starts in {}; requested start {}."
                         .format(var, first, start_yr))
    # The archive has complete years only, so the current year cannot be there
    if end_yr >= today.year:
        year = max(start_yr, today.year)
        raise DataNotAvailableError(
            "{} {} is not published yet: the IMD archive contains complete years only. "
            .format(var, year) + _archive_hint(var, today.year, start_yr, root))


def _archive_hint(var, year, start_yr, root):
    """Advice after `year` was found not published."""
    prev = year - 1
    known = _File(root, 'archive', var, prev, str(prev),
                  GRIDS[ARCHIVE_GRID[var]].file_size(366 if LeapYear(prev) else 365),
                  '', '').is_cached()
    if known:
        use = "Use end={}.".format(prev) if start_yr < year else "Use {}.".format(prev)
        return ("Latest available: {}. {} Archive ends {}-12-31. Later days are available "
                "as provisional real-time data: source='realtime'.".format(prev, use, prev))
    use = "e.g. end={}".format(prev) if start_yr < year else "{} or before".format(prev)
    return ("Use an earlier year ({}). Days after the last published year are available "
            "as provisional real-time data: source='realtime'.".format(use))


def _format_days(days):
    return cache._format_periods('realtime', [pd.Timestamp(d).strftime('%Y-%m-%d')
                                              for d in days])


def _format_periods(source, files):
    return cache._format_periods(source, [f.name for f in files])


###############################################################################
# Download
###############################################################################

def _download_missing(root, missing, proxies, show, today, parallel):
    """
    Download the missing files under the cache lock.

    ``parallel`` files are downloaded at the same time, newest first, so
    that a period that is not published yet is found early; the other
    downloads then stop.

    Returns the files IMD has no data for (empty reply).
    """
    with cache.CacheLock(root, say=show.message):
        # Another process may have downloaded some files while we waited
        missing = [f for f in missing if not f.is_cached()]
        if not missing:
            return []
        missing.sort(key=lambda f: f.period, reverse=True)
        show.message("Downloading {} file{} from IMD into {}".format(
            len(missing), '' if len(missing) == 1 else 's', root))

        def save(f):
            manifest = cache.read_manifest(root)
            manifest['files'][f.key] = f.record
            cache.write_manifest(root, manifest)

        empty = set()

        def stops(f):
            if f.source == 'archive':
                return True
            # Real-time: go on with the earlier days if this day is recent
            # (it may be in the publication lag) or may be a gap (a later
            # day is not empty). Stop when this and all later days are
            # empty: the period is not on the server.
            empty.add(f.period)
            if (today - f.period.date()).days <= RECENT_DAYS:
                return False
            return all(g.period in empty for g in missing if g.period > f.period)

        return _download_all(missing, proxies, show, save, stops, parallel)


def _realtime_message(var, unavailable, today):
    days = sorted(f.period for f in unavailable)
    msg = "Real-time {} is not available for {} (IMD returned an empty file).".format(
        var, _format_days(days))
    if (today - days[-1].date()).days <= RECENT_DAYS:
        msg += (" Recent days may not be published yet: IMD usually publishes real-time "
                "{} about {} day{} late. Try end='{:%Y-%m-%d}' or retry later.".format(
                    var, REALTIME_LAG[var], '' if REALTIME_LAG[var] == 1 else 's',
                    days[0] - timedelta(days=1)))
    else:
        msg += " Real-time files cover a limited period only"
        msg += ("; use source='archive' for older years." if var in ARCHIVE_GRIDS else ".")
    return msg


class _RetryableError(Exception):
    pass


class _Cancelled(Exception):
    """The download was stopped because another one failed."""


def _start_thread(target):
    """Start a daemon thread; None where threads are not available (Pyodide)."""
    thread = threading.Thread(target=target, daemon=True)
    try:
        thread.start()
    except RuntimeError:
        return None
    return thread


def _download_all(files, proxies, show, save, stops, parallel):
    """
    Download ``files`` in this order, ``parallel`` at a time.

    ``save(f)`` is called in this thread for each downloaded file. If IMD
    has no data for a file, the other downloads stop if ``stops(f)``. Any
    other error stops them and is raised.

    Returns the files IMD has no data for.

    The IMD server is slow for each file (10-30 s before the first byte,
    then often 0.5 MB/s), but not slower for several files at a time.
    """
    cancel = threading.Event()
    results = queue.Queue()
    todo = deque(files)
    take = threading.Lock()
    unavailable = []

    def get(f):
        try:
            _download(f, proxies, show, cancel)
        except _Cancelled as e:
            return e
        except BaseException as e:
            # Stop the others before taking a new file
            if not isinstance(e, DataNotAvailableError) or stops(f):
                cancel.set()
            return e

    def work():
        while not cancel.is_set():
            with take:
                if not todo:
                    return
                f = todo.popleft()
            results.put((f, get(f)))

    def handle(f, error):
        if error is None:
            save(f)
        elif isinstance(error, DataNotAvailableError):
            unavailable.append(f)
        elif not isinstance(error, _Cancelled):
            raise error

    show.start(files)
    errors = []
    try:
        workers = []
        for _ in range(min(parallel, len(files))):
            thread = _start_thread(work)
            if thread is None:
                break
            workers.append(thread)
        if not workers:
            # No threads: one file at a time in this thread
            while todo and not cancel.is_set():
                f = todo.popleft()
                handle(f, get(f))
        else:
            pending = len(files)
            while pending and not cancel.is_set():
                try:
                    f, error = results.get(timeout=0.2)
                except queue.Empty:
                    if not any(t.is_alive() for t in workers):
                        break
                    continue
                pending -= 1
                handle(f, error)
    except BaseException:
        cancel.set()
        show.finish()
        raise
    finally:
        cancel.set()
        if workers:
            # Downloads still running stop at their next chunk and remove
            # their partial file; waiting for the reply of the server could
            # take long, so wait only briefly. Their results are not needed.
            deadline = time.monotonic() + STOP_WAIT
            for t in workers:
                t.join(max(deadline - time.monotonic(), 0))
            # Results that came after the downloads were stopped
            while True:
                try:
                    f, error = results.get_nowait()
                except queue.Empty:
                    break
                try:
                    handle(f, error)
                except BaseException as e:
                    errors.append(e)
    if errors:
        show.finish()
        raise errors[0]
    show.finish()
    return unavailable


def _download(f, proxies, show, cancel):
    """Download one file with retries; sets ``f.record`` (manifest entry)."""
    attempts = len(RETRY_WAITS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(f, proxies, show, cancel)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError, _RetryableError) as e:
            if cancel.is_set():
                raise _Cancelled() from e
            if attempt == attempts:
                raise DownloadError("Could not download {} after {} attempts: {}"
                                    .format(f.label, attempts, e)) from e
            wait = RETRY_WAITS[attempt - 1]
            show.message("{}  {}; retrying in {} s (attempt {} of {})".format(
                f.label, _short_error(e), wait, attempt + 1, attempts))
            if _wait(cancel, wait):
                raise _Cancelled() from e


def _wait(cancel, seconds):
    """Sleep; returns True if the downloads were stopped meanwhile."""
    return cancel.wait(seconds)


def _short_error(e):
    text = str(e) or type(e).__name__
    return text if len(text) <= 100 else text[:97] + '...'


def _download_once(f, proxies, show, cancel):
    # A name of its own: a download stopped in the background can never
    # touch the file of a later download
    part = f.path.with_name('{}.{}.part'.format(f.path.name, uuid.uuid4().hex[:8]))
    show.waiting(f)
    nbytes = 0
    try:
        response = post(f.url, {f.field: f.value}, f.label, proxies=proxies,
                        timeout=TIMEOUT, stream=True)
        try:
            if cancel.is_set():
                raise _Cancelled()
            status = response.status_code
            if status >= 500:
                raise _RetryableError("server error HTTP {}".format(status))
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                raise DownloadError("Could not download {}: {}".format(f.label, e)) from e
            if not 200 <= status < 300:
                raise DownloadError("Could not download {}: unexpected HTTP status {}"
                                    .format(f.label, status))
            f.path.parent.mkdir(parents=True, exist_ok=True)
            sha = hashlib.sha256()
            with open(part, 'wb') as out:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if cancel.is_set():
                        raise _Cancelled()
                    if not chunk:
                        continue
                    nbytes += len(chunk)
                    if nbytes > f.expected:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    show.update(f, nbytes)
        finally:
            response.close()
        if nbytes == 0 and not f.may_be_unpublished:
            # Older years are published: an empty reply is a server problem
            raise _RetryableError("IMD returned an empty file, although {} is published"
                                  .format(f.label))
        if f.source == 'archive':
            empty = "{} is not published yet.".format(f.label)
        else:
            empty = "Real-time {} is not available.".format(f.label)
        check_download_size(nbytes, f.expected, f.label, empty_msg=empty)
        if cancel.is_set():
            raise _Cancelled()
        os.replace(part, f.path)
    except BaseException as e:
        try:
            part.unlink()
        except OSError:
            pass
        if cancel.is_set() or isinstance(e, (_Cancelled, KeyboardInterrupt)):
            # Stopped: silent, load() may have returned already
            show.done(f, None)
        elif isinstance(e, DataNotAvailableError):
            show.done(f, "not published yet (empty reply)" if f.source == 'archive'
                      else "not available (empty reply)")
        else:
            show.done(f, "failed")
        raise
    show.done(f, "downloaded {}".format(_format_size(nbytes)))
    f.record = {
        'url': f.url,
        'post_data': {f.field: f.value},
        'size': nbytes,
        'sha256': sha.hexdigest(),
        'downloaded': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'imdlib_version': __version__,
    }


###############################################################################
# Progress display
###############################################################################

def _in_notebook():
    """
    True in Jupyter, Colab or JupyterLite (output that can be updated in
    place). IPython is only used if already imported.
    """
    if 'IPython' not in sys.modules:
        return False
    try:
        from IPython import get_ipython
        shell = get_ipython()
    except Exception:
        return False
    if shell is None:
        return False
    # The terminal IPython cannot update output; kernels can (ipykernel,
    # Colab, the Pyodide and Xeus kernels of JupyterLite)
    return shell.__class__.__name__ != 'TerminalInteractiveShell'


def _is_tty():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _format_size(nbytes, unit=None):
    if unit is None:
        unit = 'MB' if nbytes >= 1e6 else 'kB'
    return "{:.1f} {}".format(nbytes / (1e6 if unit == 'MB' else 1e3), unit)


def _group_label(files):
    """'rain 2017-2018', 'tmax 2026-10-01 to 2026-10-05' or 'rain 40 files'."""
    f = files[0]
    periods = cache._format_periods(f.source, [g.name for g in files])
    if len(periods) > 30:
        periods = "{} files".format(len(files))
    return "{} {}".format(f.var, periods)


class _Progress:
    """
    Progress of the downloads of one ``load()`` call (several files at a time).

    One line for all files: the elapsed time while waiting for the server,
    then a bar against the known total size (IMD omits Content-Length).
    When no data arrives for a while, the line says so: the IMD server
    often pauses during a download.

    Modes: 'tty' (one line updated with \\r), 'notebook' (updating display),
    'plain' (one line per event, e.g. logs) or None (silent).
    """

    WIDTH = 24
    # Seconds without data before the line shows that the server is paused
    STALL = 5

    def __init__(self, enabled=True):
        if not enabled:
            self.mode = None
        elif _in_notebook():
            self.mode = 'notebook'
        elif _is_tty():
            self.mode = 'tty'
        else:
            self.mode = 'plain'
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ticker = None
        self._handle = None
        self._width = 0
        self._last = 0.0
        self._active = False
        encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
        try:
            '█░…'.encode(encoding)
            self._full, self._empty, self._dots = '█', '░', '…'
        except (UnicodeEncodeError, LookupError):
            self._full, self._empty, self._dots = '#', '-', '...'

    def message(self, text):
        """Print a line (above the progress line, if one is shown)."""
        if self.mode is None:
            return
        with self._lock:
            if self._active and self.mode == 'tty':
                pad = max(self._width - len(text), 0)
                sys.stdout.write('\r' + text + ' ' * pad + '\n')
                sys.stdout.flush()
                self._width = 0
                self._draw()
            else:
                print(text, flush=True)

    def start(self, files):
        if self.mode is None:
            return
        self.label = _group_label(files)
        self.total = sum(f.expected for f in files)
        self.count = len(files)
        self.received = {}
        self.finished = 0
        self.empty = 0
        self.t0 = self._data_time = time.monotonic()
        self._handle = None
        if self.mode == 'plain':
            print("{}  waiting for IMD server...".format(self.label), flush=True)
            return
        self._active = True
        self._render()
        self._stop.clear()
        self._ticker = _start_thread(self._tick)

    def _tick(self):
        while not self._stop.wait(1.0):
            self._render()

    def waiting(self, f):
        """A request for ``f`` is sent (again): nothing received yet."""
        if self.mode is None:
            return
        with self._lock:
            self.received[f.label] = 0
            f.t0 = time.monotonic()

    def update(self, f, nbytes):
        if self.mode is None:
            return
        now = time.monotonic()
        with self._lock:
            self.received[f.label] = nbytes
            self._data_time = now
        if self.mode != 'plain' and now - self._last >= 0.2:
            self._last = now
            self._render()

    def done(self, f, status):
        """``f`` is finished: downloaded, failed (status) or stopped (None)."""
        if self.mode is None:
            return
        with self._lock:
            if status is None or status == 'failed' or 'empty' in status:
                self.received.pop(f.label, None)
                if status and 'empty' in status:
                    self.empty += 1
            else:
                self.finished += 1
            if status is None:
                return
            if self.mode == 'plain' or not status.startswith('downloaded'):
                self.message("{}  {} in {:.0f} s".format(
                    f.label, status, time.monotonic() - getattr(f, 't0', self.t0)))

    def finish(self):
        if self.mode is None or (self.mode != 'plain' and not self._active):
            return
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join()
            self._ticker = None
        with self._lock:
            secs = time.monotonic() - self.t0
            got = sum(self.received.values())
            # Done unless downloads were stopped (empty replies are done)
            if self.finished + self.empty == self.count:
                status = "downloaded {} in {:.0f} s".format(_format_size(got), secs)
            elif self.finished:
                status = "stopped: {} of {} file{} downloaded".format(
                    self.finished, self.count, '' if self.count == 1 else 's')
            else:
                status = "stopped"
            if self.mode == 'plain':
                if self.count > 1:
                    print("{}  {}".format(self.label, status), flush=True)
                return
            self._render(status, final=True)
            self._active = False

    def _line(self, status=None):
        secs = time.monotonic() - self.t0
        if status is not None:
            return "{}  {}".format(self.label, status)
        got = sum(self.received.values())
        if got == 0:
            return "{}  waiting for IMD server{} {:.0f}s".format(self.label, self._dots, secs)
        done = min(got / self.total, 1.0) if self.total else 1.0
        n = int(round(done * self.WIDTH))
        bar = self._full * n + self._empty * (self.WIDTH - n)
        total = _format_size(self.total)
        line = "{}  {}  {} / {}".format(
            self.label, bar, _format_size(got, total.split()[1]).split()[0], total)
        if self.count > 1:
            line += "  {} of {} files".format(self.finished, self.count)
        line += "  {:.0f}s".format(secs)
        idle = time.monotonic() - self._data_time
        if idle >= self.STALL:
            line += "  waiting for IMD server{} {:.0f}s".format(self._dots, idle)
        return line

    def _render(self, status=None, final=False):
        with self._lock:
            if not self._active:
                return
            if self.mode == 'tty':
                self._draw(status, final)
            else:
                self._render_notebook(self._line(status))

    def _draw(self, status=None, final=False):
        line = self._line(status)
        pad = max(self._width - len(line), 0)
        sys.stdout.write('\r' + line + ' ' * pad + ('\n' if final else ''))
        sys.stdout.flush()
        self._width = 0 if final else len(line)

    def _render_notebook(self, line):
        try:
            from IPython.display import display, Pretty
            if self._handle is None:
                self._handle = display(Pretty(line), display_id=True)
            else:
                self._handle.update(Pretty(line))
        except Exception:
            # Fall back to plain output if the display cannot be updated
            self.mode = 'plain'
            self._active = False
            print(line, flush=True)
