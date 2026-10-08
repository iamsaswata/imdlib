"""
``imdlib.load()``: download, validate, cache and read IMD gridded data in one call.
"""

import hashlib
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from imdlib import cache
from imdlib.core import _open_archive
from imdlib.real import _open_realtime
from imdlib.util import (ARCHIVE_GRIDS, ARCHIVE_URLS, REALTIME_GRIDS, REALTIME_URLS,
                         DataNotAvailableError, DownloadError, LeapYear,
                         check_download_size, parse_date_input)
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
# Waits (s) before each retry after a connection error, timeout or server error
RETRY_WAITS = (5, 15, 45)
CHUNK_SIZE = 64 * 1024


def load(var, start, end=None, *, source='archive', cache_dir=None, offline=False,
         proxies=None, progress=True):
    """
    Load IMD gridded data, downloading and caching the files as needed.

    Files missing from the local cache are downloaded (one at a time, with
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
        returned; files downloaded before the error stay cached.
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

    start_day, end_day, start_yr, end_yr = parse_date_input(start, end)
    root = cache.get_dir(cache_dir)
    today = date.today()

    if source == 'archive':
        _check_archive_period(var, start_yr, end_yr, root, today)
        nlat, nlon = ARCHIVE_GRIDS[var]
        url, field = ARCHIVE_URLS[var]
        files = [_File(root, 'archive', var, year, str(year),
                       (366 if LeapYear(year) else 365) * nlat * nlon * 4, url, field)
                 for year in range(start_yr, end_yr + 1)]
    else:
        nlat, nlon = REALTIME_GRIDS[var]
        url, field = REALTIME_URLS[var]
        dates = pd.date_range(start_day, end_day, freq='D')
        future = [d for d in dates if d.date() > today]
        if future:
            raise DataNotAvailableError(
                "Real-time {} is not available for {}: these dates are in the future."
                .format(var, _format_days(future)))
        files = [_File(root, 'realtime', var, d, d.strftime('%Y-%m-%d'),
                       nlat * nlon * 4, url, field, d.strftime('%d%m%Y'))
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
        unavailable = _download_missing(root, missing, proxies, _Progress(progress), today)
        if unavailable and source == 'archive':
            f = unavailable[0]
            raise DataNotAvailableError(
                "{} is not published yet (IMD returned an empty file). ".format(f.label)
                + _archive_hint(var, f.period, start_yr, root))
        if unavailable:
            raise DataNotAvailableError(_realtime_message(var, unavailable, today))

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
    nlat, nlon = ARCHIVE_GRIDS[var]
    prev = year - 1
    known = _File(root, 'archive', var, prev, str(prev),
                  (366 if LeapYear(prev) else 365) * nlat * nlon * 4, '', '').is_cached()
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

def _download_missing(root, missing, proxies, show, today):
    """
    Download the missing files under the cache lock, newest first, so that a
    period that is not published yet fails before long downloads start.

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
        unavailable = []
        for f in missing:
            try:
                _download(f, proxies, show)
            except DataNotAvailableError:
                unavailable.append(f)
                # Recent real-time days may be in the publication lag:
                # go on with the previous day to find the last published one
                if f.source == 'realtime' and (today - f.period.date()).days <= RECENT_DAYS:
                    continue
                break
            else:
                manifest = cache.read_manifest(root)
                manifest['files'][f.key] = f.record
                cache.write_manifest(root, manifest)
                if unavailable:
                    break
    return unavailable


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


def _download(f, proxies, show):
    """Download one file with retries; sets ``f.record`` (manifest entry)."""
    attempts = len(RETRY_WAITS) + 1
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(f, proxies, show)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError, _RetryableError) as e:
            if attempt == attempts:
                raise DownloadError("Could not download {} after {} attempts: {}"
                                    .format(f.label, attempts, e)) from e
            wait = RETRY_WAITS[attempt - 1]
            show.message("{}  {}; retrying in {} s (attempt {} of {})".format(
                f.label, _short_error(e), wait, attempt + 1, attempts))
            time.sleep(wait)


def _short_error(e):
    text = str(e) or type(e).__name__
    return text if len(text) <= 100 else text[:97] + '...'


def _download_once(f, proxies, show):
    f.path.parent.mkdir(parents=True, exist_ok=True)
    part = f.path.with_name(f.path.name + '.part')
    show.start(f.label, f.expected)
    nbytes = 0
    try:
        response = requests.post(f.url, data={f.field: f.value}, proxies=proxies,
                                 timeout=TIMEOUT, stream=True)
        try:
            if response.status_code >= 500:
                raise _RetryableError("server error HTTP {}".format(response.status_code))
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                raise DownloadError("Could not download {}: {}".format(f.label, e)) from e
            sha = hashlib.sha256()
            with open(part, 'wb') as out:
                for chunk in response.iter_content(CHUNK_SIZE):
                    if not chunk:
                        continue
                    nbytes += len(chunk)
                    if nbytes > f.expected:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    show.update(nbytes)
        finally:
            response.close()
        if f.source == 'archive':
            empty = "{} is not published yet.".format(f.label)
        else:
            empty = "Real-time {} is not available.".format(f.label)
        check_download_size(nbytes, f.expected, f.label, empty_msg=empty)
        os.replace(part, f.path)
    except BaseException as e:
        if part.exists():
            part.unlink()
        if isinstance(e, DataNotAvailableError):
            show.finish("not available (empty reply)")
        else:
            show.finish("failed")
        raise
    show.finish()
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
    """True in Jupyter/Colab. IPython is only used if already imported."""
    if 'IPython' not in sys.modules:
        return False
    try:
        from IPython import get_ipython
        shell = get_ipython()
    except Exception:
        return False
    if shell is None:
        return False
    return shell.__class__.__name__ == 'ZMQInteractiveShell' or 'google.colab' in sys.modules


def _is_tty():
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _format_size(nbytes, unit=None):
    if unit is None:
        unit = 'MB' if nbytes >= 1e6 else 'kB'
    return "{:.1f} {}".format(nbytes / (1e6 if unit == 'MB' else 1e3), unit)


class _Progress:
    """
    Two-phase progress of one download at a time.

    Phase 1 (no bytes received yet): elapsed waiting time.
    Phase 2: bar against the known file size (IMD often omits Content-Length).

    Modes: 'tty' (one line updated with \\r), 'notebook' (updating display),
    'plain' (one line per event, e.g. logs) or None (silent).
    """

    WIDTH = 24

    def __init__(self, enabled=True):
        if not enabled:
            self.mode = None
        elif _in_notebook():
            self.mode = 'notebook'
        elif _is_tty():
            self.mode = 'tty'
        else:
            self.mode = 'plain'
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ticker = None
        self._handle = None
        self._width = 0
        self._last = 0.0
        encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
        try:
            '█░…'.encode(encoding)
            self._full, self._empty, self._dots = '█', '░', '…'
        except (UnicodeEncodeError, LookupError):
            self._full, self._empty, self._dots = '#', '-', '...'

    def message(self, text):
        """Print a one-off line (not while a download line is active)."""
        if self.mode is not None:
            print(text, flush=True)

    def start(self, label, total):
        if self.mode is None:
            return
        self.label, self.total = label, total
        self.received = 0
        self.t0 = time.monotonic()
        self._handle = None
        if self.mode == 'plain':
            print("{}  waiting for IMD server...".format(label), flush=True)
            return
        self._render()
        self._stop.clear()
        self._ticker = threading.Thread(target=self._tick, daemon=True)
        self._ticker.start()

    def _tick(self):
        while not self._stop.wait(1.0):
            self._render()

    def update(self, nbytes):
        if self.mode is None:
            return
        self.received = nbytes
        if self.mode == 'plain':
            return
        now = time.monotonic()
        if now - self._last >= 0.2 or nbytes >= self.total:
            self._last = now
            self._render()

    def finish(self, status=None):
        if self.mode is None:
            return
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join()
            self._ticker = None
        secs = time.monotonic() - self.t0
        if self.mode == 'plain':
            if status is None:
                status = "downloaded {} in {:.0f} s".format(_format_size(self.received), secs)
            print("{}  {}".format(self.label, status), flush=True)
            return
        self._render(status, final=True)

    def _line(self, status=None):
        secs = time.monotonic() - self.t0
        if self.received == 0 and status is None:
            return "{}  waiting for IMD server{} {:.0f}s".format(self.label, self._dots, secs)
        done = min(self.received / self.total, 1.0) if self.total else 1.0
        n = int(round(done * self.WIDTH))
        bar = self._full * n + self._empty * (self.WIDTH - n)
        total = _format_size(self.total)
        line = "{}  {}  {} / {}  {:.0f}s".format(
            self.label, bar, _format_size(self.received, total.split()[1]).split()[0],
            total, secs)
        if status is not None:
            line = "{}  {}  {:.0f}s".format(self.label, status, secs)
        return line

    def _render(self, status=None, final=False):
        with self._lock:
            line = self._line(status)
            if self.mode == 'tty':
                pad = max(self._width - len(line), 0)
                sys.stdout.write('\r' + line + ' ' * pad + ('\n' if final else ''))
                sys.stdout.flush()
                self._width = 0 if final else len(line)
            else:
                self._render_notebook(line)

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
            print(line, flush=True)
