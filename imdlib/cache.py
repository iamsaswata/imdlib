"""
Local cache of IMD files used by ``imdlib.load()``.

Layout inside the cache directory::

    manifest.json
    archive/<var>/<year>.grd
    realtime/<var>/<YYYY-MM-DD>.grd

The cache directory is chosen in this order:
``cache_dir`` argument > ``imdlib.cache.set_dir()`` > ``IMDLIB_CACHE``
environment variable > the default user cache directory of the OS.
"""

import json
import numbers
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

VARIABLES = ('rain', 'tmin', 'tmax', 'rain_gpm')
SOURCES = ('archive', 'realtime')
MANIFEST = 'manifest.json'
LOCK = '.lock'
# A lock older than this is considered stale even if its process looks alive
LOCK_MAX_AGE = 24 * 3600
# Seconds between checks while waiting for another download to finish
LOCK_POLL = 2.0
# A lock file that cannot be read (e.g. crash right after creation)
# is considered stale after this many seconds
LOCK_UNREADABLE_AGE = 60

# Directory set with set_dir() for this Python session
_session_dir = None


def default_dir():
    """
    Return the default cache directory of the operating system.

    - Linux: ``$XDG_CACHE_HOME/imdlib``, else ``~/.cache/imdlib``
    - Windows: ``%LOCALAPPDATA%\\imdlib\\Cache``
    - macOS: ``~/Library/Caches/imdlib``
    """
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA')
        if not base:
            base = Path.home() / 'AppData' / 'Local'
        return Path(base) / 'imdlib' / 'Cache'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Caches' / 'imdlib'
    base = os.environ.get('XDG_CACHE_HOME')
    # The XDG specification ignores relative paths
    if not base or not os.path.isabs(base):
        base = Path.home() / '.cache'
    return Path(base) / 'imdlib'


def get_dir(cache_dir=None):
    """
    Return the cache directory that ``imdlib.load()`` uses.

    Parameters
    ----------
    cache_dir : str or path-like or None
        If given, it is returned (as an absolute path). Otherwise the
        directory set with :func:`set_dir`, then the ``IMDLIB_CACHE``
        environment variable, then the OS default is used.

    Returns
    -------
    pathlib.Path
    """
    if cache_dir is None:
        cache_dir = _session_dir
    if cache_dir is None:
        cache_dir = os.environ.get('IMDLIB_CACHE') or None
    if cache_dir is None:
        return default_dir()
    return Path(cache_dir).expanduser().absolute()


def set_dir(path):
    """
    Set the cache directory for the rest of this Python session.

    It takes precedence over the ``IMDLIB_CACHE`` environment variable and
    the OS default, but not over the ``cache_dir`` argument of
    ``imdlib.load()``. Files already cached in the previous directory are
    not moved.

    Parameters
    ----------
    path : str or path-like or None
        New cache directory. ``None`` restores the default behaviour.
    """
    global _session_dir
    _session_dir = None if path is None else Path(path).expanduser().absolute()


def file_path(root, source, var, period):
    """
    Path of a cached file. ``period`` is a year (archive) or a
    date/Timestamp (realtime).
    """
    if source == 'archive':
        name = '{}.grd'.format(int(period))
    else:
        name = '{:%Y-%m-%d}.grd'.format(period)
    return Path(root) / source / var / name


def manifest_key(source, var, name):
    """Key of a file in the manifest (path relative to the cache root)."""
    return '{}/{}/{}'.format(source, var, name)


def read_manifest(root):
    """Read ``manifest.json``; return an empty manifest if it does not exist."""
    path = Path(root) / MANIFEST
    try:
        with open(path) as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest = {}
    except (OSError, ValueError) as e:
        print("Warning: could not read {} ({}); starting a new manifest.".format(path, e))
        manifest = {}
    manifest.setdefault('version', 1)
    manifest.setdefault('files', {})
    return manifest


def write_manifest(root, manifest):
    """Write ``manifest.json`` atomically (temporary file + rename)."""
    path = Path(root) / MANIFEST
    tmp = path.with_name('{}.{}.tmp'.format(MANIFEST, os.getpid()))
    with open(tmp, 'w') as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


###############################################################################
# Lock file
###############################################################################

def _kernel32():
    """Return (kernel32, get_last_error) on Windows (separate for testing)."""
    import ctypes
    return ctypes.WinDLL('kernel32', use_last_error=True), ctypes.get_last_error


def _pid_alive_windows(pid):
    try:
        import ctypes
        kernel32, get_last_error = _kernel32()
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = get_last_error()
            if err == 87:   # ERROR_INVALID_PARAMETER: no such process
                return False
            if err == 5:    # ERROR_ACCESS_DENIED: process exists
                return True
            return None
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def pid_alive(pid):
    """
    Return True if process ``pid`` is running on this computer, False if
    not, and None if this cannot be determined.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if sys.platform == 'win32':
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _read_lock(path):
    """Return the lock content (dict), or None if missing/unreadable."""
    try:
        with open(path) as f:
            info = json.load(f)
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def _describe_lock(info):
    if not info:
        return "unknown owner"
    return "pid {}, host {}, started {}".format(info.get('pid'), info.get('host'),
                                                info.get('started'))


def _stale_reason(path, info):
    """Return why the lock at ``path`` is stale, or None if it is valid."""
    now = time.time()
    if info is None:
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            return None
        if age > LOCK_UNREADABLE_AGE:
            return "unreadable lock file"
        return None
    age = now - float(info.get('time', now))
    if age > LOCK_MAX_AGE:
        return "older than {:.0f} h".format(LOCK_MAX_AGE / 3600)
    if info.get('host') == socket.gethostname() and pid_alive(info.get('pid')) is False:
        return "process {} is no longer running".format(info.get('pid'))
    return None


class CacheLock:
    """
    Lock file ``<cache>/.lock``, held while downloading and while changing
    the manifest. Created atomically; a second process waits for it. Stale
    locks (dead process on this computer, or older than 24 h) are removed.
    """

    def __init__(self, root, say=print):
        self.root = Path(root)
        self.path = self.root / LOCK
        self.say = say
        self.held = False

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        announced = False
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                info = _read_lock(self.path)
                reason = _stale_reason(self.path, info)
                if reason:
                    # Re-read just before removing, in case another process
                    # replaced the stale lock in the meantime
                    if _read_lock(self.path) == info:
                        self.say("Removing stale imdlib lock ({}; {}).".format(
                            _describe_lock(info), reason))
                        try:
                            os.remove(self.path)
                        except FileNotFoundError:
                            pass
                    continue
                if not announced:
                    self.say("Another imdlib download is running ({}). Waiting for it "
                             "to finish...".format(_describe_lock(info)))
                    announced = True
                time.sleep(LOCK_POLL)
                continue
            now = datetime.now(timezone.utc)
            with os.fdopen(fd, 'w') as f:
                json.dump({'pid': os.getpid(), 'host': socket.gethostname(),
                           'started': now.strftime('%Y-%m-%d %H:%M:%S UTC'),
                           'time': now.timestamp()}, f)
            self.held = True
            return

    def release(self):
        if self.held:
            self.held = False
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def unlock(cache_dir=None):
    """
    Remove a leftover lock file of the cache, e.g. after a crash.

    Normally not needed: stale locks are removed automatically. Prints
    which process held the lock.

    Parameters
    ----------
    cache_dir : str or path-like or None
        Cache directory (default: see :func:`get_dir`).
    """
    path = get_dir(cache_dir) / LOCK
    if not path.exists():
        print("No lock file in {}.".format(path.parent))
        return
    info = _read_lock(path)
    os.remove(path)
    msg = "Removed lock file {} ({}).".format(path, _describe_lock(info))
    if info and info.get('host') == socket.gethostname() and pid_alive(info.get('pid')):
        msg += " Note: process {} is still running.".format(info.get('pid'))
    print(msg)


###############################################################################
# info / clear
###############################################################################

def _format_size(nbytes):
    if nbytes >= 1e9:
        return "{:.2f} GB".format(nbytes / 1e9)
    if nbytes >= 1e6:
        return "{:.1f} MB".format(nbytes / 1e6)
    return "{:.1f} kB".format(nbytes / 1e3)


def _format_periods(source, stems):
    """Compact list of years or days, e.g. '1901-1905, 2010'."""
    if source == 'archive':
        values = sorted(int(s) for s in stems)
    else:
        values = sorted(datetime.strptime(s, '%Y-%m-%d').toordinal() for s in stems)
    ranges = []
    for v in values:
        if ranges and v - ranges[-1][1] == 1:
            ranges[-1][1] = v
        else:
            ranges.append([v, v])

    def fmt(v):
        if source == 'archive':
            return str(v)
        return datetime.fromordinal(v).strftime('%Y-%m-%d')

    sep = '-' if source == 'archive' else ' to '
    return ', '.join(fmt(a) if a == b else fmt(a) + sep + fmt(b) for a, b in ranges)


def _valid_stem(source, stem):
    try:
        if source == 'archive':
            int(stem)
        else:
            datetime.strptime(stem, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def _scan(root, sources, variables, years=None):
    """
    Find cached files. Returns {(source, var): [(path, stem), ...]}.
    ``.part`` leftovers are included with the stem of their target file.
    """
    found = {}
    for source in sources:
        for var in variables:
            folder = Path(root) / source / var
            if not folder.is_dir():
                continue
            for p in sorted(folder.iterdir()):
                name = p.name
                if name.endswith('.grd.part'):
                    stem = name[:-len('.grd.part')]
                elif name.endswith('.grd'):
                    stem = name[:-len('.grd')]
                else:
                    continue
                if not _valid_stem(source, stem):
                    continue
                if years is not None and int(stem[:4]) not in years:
                    continue
                found.setdefault((source, var), []).append((p, stem))
    return found


def _check_choice(value, choices, name):
    if value is None:
        return list(choices)
    if isinstance(value, str):
        value = [value]
    value = list(value)
    for v in value:
        if v not in choices:
            raise ValueError("{} must be one of {}, got {!r}.".format(
                name, ', '.join(repr(c) for c in choices), v))
    return value


def info(cache_dir=None):
    """
    Print the cache location and what is cached: periods, number of files,
    size and download dates per source and variable.

    SHA-256 checksums and source URLs of every file are in
    ``manifest.json`` in the cache directory.

    Parameters
    ----------
    cache_dir : str or path-like or None
        Cache directory (default: see :func:`get_dir`).
    """
    root = get_dir(cache_dir)
    print("IMDLIB cache: {}".format(root))
    found = _scan(root, SOURCES, VARIABLES)
    files = read_manifest(root)['files'] if root.is_dir() else {}
    total_n, total_size = 0, 0
    for (source, var), entries in found.items():
        entries = [(p, s) for p, s in entries if p.suffix == '.grd']
        if not entries:
            continue
        size = sum(p.stat().st_size for p, _ in entries)
        dates = sorted(files[k]['downloaded'][:10]
                       for k in (manifest_key(source, var, p.name) for p, _ in entries)
                       if k in files and 'downloaded' in files[k])
        if dates:
            when = dates[0] if dates[0] == dates[-1] else dates[0] + ' to ' + dates[-1]
        else:
            when = 'unknown'
        print("  {:<8} {:<4}  {}  ({} file{}, {}, downloaded {})".format(
            source, var, _format_periods(source, [s for _, s in entries]),
            len(entries), '' if len(entries) == 1 else 's', _format_size(size), when))
        total_n += len(entries)
        total_size += size
    if total_n == 0:
        print("  (empty)")
    else:
        print("Total: {} file{}, {}".format(total_n, '' if total_n == 1 else 's',
                                           _format_size(total_size)))
        print("Checksums and sources: {}".format(root / MANIFEST))


def clear(var=None, years=None, *, source=None, cache_dir=None):
    """
    Delete cached files (and their manifest entries).

    Prints what is removed and how much space is freed. Use it to
    refresh data that IMD has republished: clear it, then call
    ``imdlib.load()`` again.

    Parameters
    ----------
    var : str or list of str or None
        'rain', 'tmin', 'tmax' and/or 'rain_gpm'. None means all variables.

    years : int or iterable of int or None
        Years to remove (for real-time data: all days of these years).
        None means all years.

    source : str or None
        'archive' or 'realtime'. None means both.

    cache_dir : str or path-like or None
        Cache directory (default: see :func:`get_dir`).

    Examples
    --------
    >>> imd.cache.clear('rain', 2025)             # one year of rainfall
    >>> imd.cache.clear('tmax', range(2001, 2011))
    >>> imd.cache.clear()                         # everything
    """
    variables = _check_choice(var, VARIABLES, 'var')
    sources = _check_choice(source, SOURCES, 'source')
    if years is not None:
        years = {int(years)} if isinstance(years, (numbers.Integral, str)) else {int(y) for y in years}

    root = get_dir(cache_dir)
    if not root.is_dir():
        print("Nothing to remove: {} does not exist.".format(root))
        return

    with CacheLock(root):
        found = _scan(root, sources, variables, years)
        manifest = read_manifest(root)
        stale_keys = []
        for key in manifest['files']:
            parts = key.split('/')
            if len(parts) != 3 or not parts[2].endswith('.grd'):
                continue
            src, v, stem = parts[0], parts[1], parts[2][:-4]
            if src in sources and v in variables and _valid_stem(src, stem) and \
                    (years is None or int(stem[:4]) in years):
                stale_keys.append(key)

        total = 0
        for (src, v), entries in found.items():
            size = sum(p.stat().st_size for p, _ in entries)
            total += size
            print("Removing {}/{}: {} ({} file{}, {})".format(
                src, v, _format_periods(src, sorted({s for _, s in entries})),
                len(entries), '' if len(entries) == 1 else 's', _format_size(size)))
            for p, _ in entries:
                os.remove(p)

        if stale_keys:
            for key in stale_keys:
                del manifest['files'][key]
            write_manifest(root, manifest)

        if not found and not stale_keys:
            print("Nothing to remove.")
        else:
            print("Freed {}.".format(_format_size(total)))
