"""
On-demand reading of IMD binary (.grd) files for the IMD objects returned
by ``imdlib.load()``. Internal module.
"""

import os
import warnings

import numpy as np

from imdlib.util import land_mask_of, mask_needs_all_days

# Warn when reading all data needs more memory than this (bytes)
MEMORY_WARNING = 2e9

_preadv = getattr(os, 'preadv', None)


def warn_memory(var, days, mask_days, cells, stacklevel=2):
    """Warn if ``days`` days of float64 data (plus the land mask) are large."""
    need = days * cells * 8 + mask_days * cells
    if need > MEMORY_WARNING:
        warnings.warn(
            "Loading {} for {:,} days needs about {:.1f} GB of memory (float64 data). "
            "If this fails or is slow, load a shorter period.".format(var, days, need / 1e9),
            stacklevel=stacklevel + 1)


def _read_exactly(f, offset, buf, path):
    """Fill the writable bytes ``buf`` from the binary file ``f`` at ``offset``."""
    # One system call where available (Linux, macOS), else seek and read
    done = _preadv(f.fileno(), [buf], offset) if _preadv else 0
    while done < len(buf):
        f.seek(offset + done)
        got = f.readinto(buf[done:])
        if not got:
            raise OSError("The file {} ended before the expected size.".format(path))
        done += got


class GrdFiles:
    """
    Days ``offset`` to ``offset + no_days - 1`` of a series of IMD binary
    files (little-endian float32, stored as (days, lat, lon) in C order),
    read only when needed. Internal: used by IMD objects from ``load()``.

    Grid cells are selected with ``lon_idx`` and ``lat_idx`` as in
    ``data[:, lon_idx, lat_idx]`` of the full (days, lon, lat) array:
    integers, slices, or integer arrays (combined as numpy does).

    Parameters
    ----------
    var : str
        'rain', 'rain_gpm', 'tmin' or 'tmax'.
    files : list of (path, days)
        The files in time order and the number of days in each. A path of
        None gives NaN for its days (a day missing at IMD).
    nlat, nlon : int
        Grid size.
    offset : int
        First day to use, counted from the start of the first file.
    no_days : int
        Number of days to use.
    land_mask : bool
        Whether the data has a land mask (archive data) or not (real-time).
    """

    def __init__(self, var, files, nlat, nlon, offset, no_days, land_mask):
        self.var = var
        self.files = [(None if path is None else str(path), days) for path, days in files]
        self.nlat = nlat
        self.nlon = nlon
        self.offset = offset
        self.no_days = no_days
        self.has_land_mask = land_mask

    @property
    def shape(self):
        return (self.no_days, self.nlon, self.nlat)

    def _parts(self, max_days=None):
        """
        Return [(path, days in file, first day, number of days), ...] to read,
        after checking that all these files are still in place.
        """
        parts = []
        skip = self.offset
        left = self.no_days if max_days is None else min(max_days, self.no_days)
        for path, days in self.files:
            if left == 0:
                break
            if skip >= days:
                skip -= days
                continue
            n = min(days - skip, left)
            parts.append((path, days, skip, n))
            skip = 0
            left -= n
        for path, days, _, _ in parts:
            if path is not None:
                self._check(path, days)
        return parts

    def _check(self, path, days):
        expected = days * self.nlat * self.nlon * 4
        try:
            size = os.path.getsize(path)
        except OSError:
            raise FileNotFoundError(
                "The {} file {} has been removed since load(); the data is read when it "
                "is first used. Call imdlib.load() again to download it.".format(self.var, path)
            ) from None
        if size != expected:
            raise OSError(
                "The {} file {} has changed since load() ({:,} bytes, expected {:,}). "
                "Call imdlib.load() again to download it.".format(self.var, path, size,
                                                                  expected))

    def _rows(self, lat_idx):
        """
        The band of latitude rows that ``lat_idx`` selects: (first row,
        number of rows, ``lat_idx`` relative to the first row).
        """
        rows = np.arange(self.nlat)[lat_idx]  # IndexError as numpy gives it
        if isinstance(lat_idx, slice):
            r = range(self.nlat)[lat_idx]
            if len(r) == 0 or r.step < 0:
                return 0, self.nlat, lat_idx
            return r.start, r[-1] - r.start + 1, slice(0, r.stop - r.start, r.step)
        if rows.size == 0:
            return 0, self.nlat, lat_idx
        first = int(rows.min())
        return first, int(rows.max()) - first + 1, rows - first

    def _chunks(self, lon_idx, lat_idx, max_days=None):
        """Yield the selected cells file by file (float32, days first)."""
        first_row, nrows, lat_rel = self._rows(lat_idx)
        day_bytes = self.nlat * self.nlon * 4
        band_bytes = nrows * self.nlon * 4
        for path, days, first, n in self._parts(max_days):
            # Read only the rows of the band: one read per day, or one read
            # for all days if the band is the whole grid. This needs far
            # fewer file accesses than a memory map on slow file systems.
            if path is None:
                band = np.full((n, nrows, self.nlon), np.nan, dtype='<f4')
                yield np.array(band.transpose(0, 2, 1)[:, lon_idx, lat_rel])
                continue
            band = np.empty((n, nrows, self.nlon), dtype='<f4')
            buf = memoryview(band).cast('B')
            with open(path, 'rb', buffering=0) as f:
                if nrows == self.nlat:
                    _read_exactly(f, first * day_bytes, buf, path)
                else:
                    start = first * day_bytes + first_row * self.nlon * 4
                    for d in range(n):
                        _read_exactly(f, start + d * day_bytes,
                                      buf[d * band_bytes:(d + 1) * band_bytes], path)
            yield np.array(band.transpose(0, 2, 1)[:, lon_idx, lat_rel])

    def read(self):
        """Read all days and cells: float64 array of shape (days, lon, lat)."""
        cells = self.nlat * self.nlon
        out = np.empty(self.shape)
        k = 0
        for path, days, first, n in self._parts():
            if path is None:
                out[k:k + n] = np.nan
                k += n
                continue
            values = np.fromfile(path, dtype='<f4', count=n * cells, offset=first * cells * 4)
            out[k:k + n] = values.reshape(n, self.nlat, self.nlon).transpose(0, 2, 1)
            k += n
        return out

    def read_cells(self, lon_idx, lat_idx):
        """
        Read selected cells for all days. Returns float64, equal to
        ``read()[:, lon_idx, lat_idx]``.
        """
        return np.concatenate([c.astype(np.float64)
                               for c in self._chunks(lon_idx, lat_idx)])

    def land_mask(self, lon_idx=slice(None), lat_idx=slice(None)):
        """
        Land mask of the selected cells (True = valid cell), equal to the
        mask built from the full array by ``open_data()``; None for data
        without land mask.
        """
        if not self.has_land_mask:
            return None
        max_days = None if mask_needs_all_days(self.var, self.no_days) else 1
        return land_mask_of(self.var, self._chunks(lon_idx, lat_idx, max_days), self.no_days)

    def mask_from_values(self, values, lon_idx, lat_idx):
        """
        Land mask of the selected cells (as :meth:`land_mask`), built from
        ``values`` of these cells as returned by :meth:`read_cells`.
        """
        if not self.has_land_mask:
            return None
        return land_mask_of(self.var, [values], len(values))


class _Window:
    """
    A box of grid cells of the files of a lazy object (``GrdFiles``), for
    clip(): cells outside the region (``keep`` False) are NaN, and False in
    the land mask. Cells are selected as in ``GrdFiles``, relative to the box.
    """

    def __init__(self, source, i0, j0, keep):
        if isinstance(source, _Window):            # a box of a box
            keep = keep & source.keep[i0:i0 + keep.shape[0], j0:j0 + keep.shape[1]]
            i0, j0, source = i0 + source.i0, j0 + source.j0, source.source
        self.source, self.i0, self.j0, self.keep = source, i0, j0, keep
        self.nlon, self.nlat = keep.shape
        self.var = source.var
        self.no_days = source.no_days
        self.has_land_mask = source.has_land_mask

    @property
    def shape(self):
        return (self.no_days, self.nlon, self.nlat)

    def _in_source(self, lon_idx, lat_idx):
        return _shift(lon_idx, self.nlon, self.i0), _shift(lat_idx, self.nlat, self.j0)

    def read(self):
        return self.read_cells(slice(None), slice(None))

    def read_cells(self, lon_idx, lat_idx):
        values = self.source.read_cells(*self._in_source(lon_idx, lat_idx))
        return np.where(self.keep[lon_idx, lat_idx], values, np.nan)

    def land_mask(self, lon_idx=slice(None), lat_idx=slice(None)):
        mask = self.source.land_mask(*self._in_source(lon_idx, lat_idx))
        return None if mask is None else np.asarray(mask & self.keep[lon_idx, lat_idx])

    def mask_from_values(self, values, lon_idx, lat_idx):
        """
        Land mask of the selected cells, for ``values`` of these cells as
        returned by :meth:`read_cells` (all days), as ``GrdFiles.mask_from_values``.
        """
        mask = self.source.mask_from_values(values, *self._in_source(lon_idx, lat_idx))
        return None if mask is None else np.asarray(mask & self.keep[lon_idx, lat_idx])


def _shift(idx, n, offset):
    """Index ``idx`` into an axis of length ``n`` as an index into the axis shifted by ``offset``."""
    if isinstance(idx, slice):
        r = range(n)[idx]
        if r.step > 0:
            return slice(r.start + offset, r.stop + offset, r.step)
        return np.array(r, dtype=np.intp) + offset
    a = np.asarray(idx)
    if a.dtype == bool or not np.issubdtype(a.dtype, np.integer):
        raise IndexError("only integers, slices and integer arrays select cells")
    if ((a < -n) | (a >= n)).any():
        raise IndexError("index out of range for an axis of size {}".format(n))
    a = np.where(a < 0, a + n, a) + offset
    return int(a) if a.ndim == 0 else a
