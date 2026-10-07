"""
On-demand reading of IMD binary (.grd) files for the IMD objects returned
by ``imdlib.load()``. Internal module.
"""

import os
import warnings

import numpy as np

# Warn when reading all data needs more memory than this (bytes)
MEMORY_WARNING = 2e9


def warn_memory(var, days, mask_days, cells, stacklevel=2):
    """Warn if ``days`` days of float64 data (plus the land mask) are large."""
    need = days * cells * 8 + mask_days * cells
    if need > MEMORY_WARNING:
        warnings.warn(
            "Loading {} for {:,} days needs about {:.1f} GB of memory (float64 data). "
            "If this fails or is slow, load a shorter period.".format(var, days, need / 1e9),
            stacklevel=stacklevel + 1)


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
        The files in time order and the number of days in each.
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
        self.files = [(str(path), days) for path, days in files]
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

    def _chunks(self, lon_idx, lat_idx, max_days=None):
        """Yield the selected cells file by file (float32, days first)."""
        for path, days, first, n in self._parts(max_days):
            mm = np.memmap(path, dtype='<f4', mode='r', shape=(days, self.nlat, self.nlon))
            # Copy, so that no reference to the mapped file is kept
            chunk = np.array(mm[first:first + n].transpose(0, 2, 1)[:, lon_idx, lat_idx])
            del mm
            yield chunk

    def read(self):
        """Read all days and cells: float64 array of shape (days, lon, lat)."""
        cells = self.nlat * self.nlon
        out = np.empty(self.shape)
        k = 0
        for path, days, first, n in self._parts():
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
        if self.var == 'rain':
            chunks = self._chunks(lon_idx, lat_idx)
            first = next(chunks)
            # -999 sentinel (ocean/outside India) on the first day
            mask = first[0] != -999.0
            # Zero rainfall on all days (boundary cells), for a year or more
            if self.no_days >= 365:
                all_zero = (first == 0.0).all(axis=0)
                for chunk in chunks:
                    all_zero &= (chunk == 0.0).all(axis=0)
                mask = mask & ~all_zero
            return np.asarray(mask)
        # tmin/tmax: sentinel is the corner value of the first day
        corner = next(self._chunks(0, 0, max_days=1))[0]
        day0 = next(self._chunks(lon_idx, lat_idx, max_days=1))[0]
        return np.asarray(day0 != corner)
