import os
import numpy as np
import pandas as pd
from datetime import date
from pathlib import Path


# IMD download endpoints: (url, POST field name)
ARCHIVE_URLS = {
    'rain': ('https://imdpune.gov.in/cmpg/Griddata/rainfall.php', 'rain'),
    'tmax': ('https://imdpune.gov.in/cmpg/Griddata/maxtemp.php', 'maxtemp'),
    'tmin': ('https://imdpune.gov.in/cmpg/Griddata/mintemp.php', 'mintemp'),
}
REALTIME_URLS = {
    'rain': ('https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/rain.php', 'rain'),
    'rain_gpm': ('https://www.imdpune.gov.in/cmpg/Realtimedata/gpm/rain.php', 'rain'),
    'tmax': ('https://imdpune.gov.in/cmpg/Realtimedata/max/max.php', 'max'),
    'tmin': ('https://imdpune.gov.in/cmpg/Realtimedata/min/min.php', 'min'),
}

# Grid size (n_lat, n_lon) of the binary files
ARCHIVE_GRIDS = {'rain': (129, 135), 'tmin': (31, 31), 'tmax': (31, 31)}
REALTIME_GRIDS = {'rain': (129, 135), 'rain_gpm': (281, 241),
                  'tmin': (61, 61), 'tmax': (61, 61)}


class DataNotAvailableError(Exception):
    """IMD has no file for the requested period (not published yet or outside
    the period covered by the server). The server signals this with an empty file."""


class DownloadError(Exception):
    """A download failed, or the file received has the wrong size."""


def check_download_size(nbytes, expected, what, empty_msg=None):
    """
    Validate the byte size of a downloaded .grd file.

    An empty reply means IMD has no data for the period (HTTP 200 with
    0 bytes); any other size that is not exactly ``expected`` is a
    corrupt or truncated download.

    Raises DataNotAvailableError or DownloadError.
    """
    if nbytes == 0:
        if empty_msg is None:
            empty_msg = ("{} is not available from IMD (the server returned an "
                         "empty file). Nothing was saved.".format(what))
        raise DataNotAvailableError(empty_msg)
    if nbytes != expected:
        raise DownloadError(
            "Download of {} is incomplete or corrupt: received {:,} bytes, "
            "expected exactly {:,}. Nothing was saved.".format(what, nbytes, expected))


def save_download(content, fname, expected, what, empty_msg=None):
    """
    Write downloaded bytes to ``fname`` only if their size is exactly
    ``expected``. The data is written to ``<fname>.part`` first and then
    renamed, so an interrupted write never leaves a partial file behind.
    """
    check_download_size(len(content), expected, what, empty_msg)
    part = str(fname) + '.part'
    try:
        with open(part, 'wb') as f:
            f.write(content)
        os.replace(part, fname)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise


def read_grd(fname, days, nlat, nlon):
    """
    Read an IMD binary (.grd) file of little-endian float32 values stored
    as (days, lat, lon) in C order.

    Returns a float32 array of shape (days, lon, lat).
    """
    data = np.fromfile(fname, dtype='<f4')
    # Check consistency of data points
    if data.size != days * nlat * nlon:
        raise Exception("Error in file reading,"
                        "mismatch in size of data-length")
    return np.transpose(data.reshape(days, nlat, nlon), (0, 2, 1))


# Missing values of IMD files (GPM rain has none)
RAIN_MISSING = -999.0
TEMP_MISSING = 99.9


def _missing(values, cat):
    """True where ``values`` are the missing value of IMD files of this variable."""
    if cat == 'rain':
        return values == RAIN_MISSING
    if cat in ('tmin', 'tmax'):
        # 99.9 as stored (float32) and as typed
        return (values == TEMP_MISSING) | (values == float(np.float32(TEMP_MISSING)))
    return np.zeros(np.shape(values), dtype=bool)          # GPM rain has none


# Rain cells that are zero on all days are masked only for this many days or more
RAIN_MASK_MIN_DAYS = 365

# Tolerance (degrees) when matching cell coordinates
COORD_TOL = 1e-6


def mask_needs_all_days(cat, no_days):
    """True if the land mask needs all days, not only the first."""
    return cat == 'rain' and no_days >= RAIN_MASK_MIN_DAYS


def land_mask_of(cat, chunks, no_days):
    """
    Land mask (True = cell with data) from ``chunks`` of consecutive days
    (days first), ``no_days`` days in total: not missing on the first day
    and, for rain over ``RAIN_MASK_MIN_DAYS`` days or more, not zero on all days.
    """
    chunks = iter(chunks)
    first = next(chunks)
    mask = ~_missing(first[0], cat)
    if mask_needs_all_days(cat, no_days):
        all_zero = (first == 0.0).all(axis=0)
        for chunk in chunks:
            all_zero &= (chunk == 0.0).all(axis=0)
        mask = mask & ~all_zero
    return np.asarray(mask)


def _check_same_cells(objs, what):
    """
    Raise ValueError unless the IMD objects ``objs`` (a dict name -> object)
    are on the same grid cells: the same longitudes and latitudes, and the
    same ``cell_fraction`` (both unclipped, or clipped to the same region).
    ``what`` is the function named in the error.
    """
    def cells(obj):
        clipped = getattr(obj, 'cell_fraction', None) is not None
        return "{} x {} cells from {:g}E, {:g}N{}".format(
            len(obj.lon_array), len(obj.lat_array), float(obj.lon_array[0]),
            float(obj.lat_array[0]), ' (clipped)' if clipped else '')

    def same(a, b):
        if len(a.lon_array) != len(b.lon_array) or len(a.lat_array) != len(b.lat_array):
            return False
        if not (np.allclose(a.lon_array, b.lon_array, rtol=0, atol=COORD_TOL) and
                np.allclose(a.lat_array, b.lat_array, rtol=0, atol=COORD_TOL)):
            return False
        fa, fb = getattr(a, 'cell_fraction', None), getattr(b, 'cell_fraction', None)
        if fa is None or fb is None:
            return fa is None and fb is None
        return np.array_equal(fa, fb)

    names = list(objs)
    first = objs[names[0]]
    for name in names[1:]:
        if not same(first, objs[name]):
            raise ValueError(
                "{} needs {} on the same grid cells, but {} has {} and {} has {}. Use data "
                "from the same grid, clipped to the same region or not clipped at all.".format(
                    what, ' and '.join(names), names[0], cells(first), name,
                    cells(objs[name])))


def parse_date_input(start, end=None):
    """Parse date input that can be int year or 'YYYY-MM-DD' string.

    Returns (start_day, end_day, start_yr, end_yr)

    Raises ValueError if date string is not a valid date.
    """
    if end is None:
        end = start

    if isinstance(start, (int, np.integer)):
        start_day = f"{start}-01-01"
        start_yr = int(start)
    elif isinstance(start, str):
        try:
            parsed = pd.Timestamp(start)
        except (ValueError, pd.errors.OutOfBoundsDatetime):
            raise ValueError(
                f"Invalid start date '{start}'. Expected format: 'YYYY-MM-DD' or integer year."
            )
        start_day = parsed.strftime('%Y-%m-%d')
        start_yr = parsed.year
    else:
        raise TypeError(
            f"start must be an int (year) or str ('YYYY-MM-DD'), got {type(start).__name__}"
        )

    if isinstance(end, (int, np.integer)):
        end_day = f"{end}-12-31"
        end_yr = int(end)
    elif isinstance(end, str):
        try:
            parsed = pd.Timestamp(end)
        except (ValueError, pd.errors.OutOfBoundsDatetime):
            raise ValueError(
                f"Invalid end date '{end}'. Expected format: 'YYYY-MM-DD' or integer year."
            )
        end_day = parsed.strftime('%Y-%m-%d')
        end_yr = parsed.year
    else:
        raise TypeError(
            f"end must be an int (year) or str ('YYYY-MM-DD'), got {type(end).__name__}"
        )

    if pd.Timestamp(start_day) > pd.Timestamp(end_day):
        raise ValueError(
            f"Start date ({start_day}) must not be after end date ({end_day})."
        )

    return start_day, end_day, start_yr, end_yr


def LeapYear(year):
    """
    Check leap year or not
    """
    if (year % 4) == 0:
        if (year % 100) == 0:
            if (year % 400) == 0:
                return True
            else:
                return False
        else:
            return True
    else:
        return False


def get_lat_lon(lat, lon, lat_rage, lon_range):
    """
    Check INDEX of closest lat lon for a given co-ordinate
    """
    lat_index = np.abs(lat_rage - lat).argmin()
    lon_index = np.abs(lon_range - lon).argmin()
    return lat_index, lon_index


def total_days(starting_day, ending_day):
    """
    Calculate to no of days for a given starting and ending day
    """
    start_year = int(starting_day[0:4])
    start_month = int(starting_day[5:7])
    start_day = int(starting_day[8:10])
    end_year = int(ending_day[0:4])
    end_month = int(ending_day[5:7])
    end_day = int(ending_day[8:10])
    days = date(end_year, end_month, end_day) - date(
        start_year, start_month, start_day)
    return days.days + 1


def get_filename(year, var_type, fn_format, file_dir):
    """
    Get filename for reading the file content in future
    """
    if var_type == 'rain':
        if file_dir is not None:
            if fn_format == 'yearwise':
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + \
                            str(year) + '.grd'
                else:
                    fname = file_dir + '/' + str(year) + '.grd'
            else:
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + \
                            'Rainfall_ind' + str(year) + '_rfp25.grd'
                else:
                    fname = file_dir + '/' + 'Rainfall_ind' + str(year) + '_rfp25.grd'
        else:
            if fn_format == 'yearwise':
                if Path(var_type).exists():
                    fname = var_type + '/' + str(year) + '.grd'
                else:
                    fname = str(year) + '.grd'
            else:
                if Path(var_type).exists():
                    fname = var_type + '/' + 'Rainfall_ind' + str(year) + '_rfp25.grd'
                else:
                    fname = 'Rainfall_ind' + str(year) + '_rfp25.grd'

    elif var_type == 'tmax':

        if file_dir is not None:
            if fn_format == 'yearwise':
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + \
                            str(year) + '.GRD'
                else:
                    fname = file_dir + '/' + str(year) + '.GRD'
            else:
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + 'Maxtemp_MaxT_' + \
                            str(year) + '.GRD'
                else:
                    fname = file_dir + '/' + 'Maxtemp_MaxT_' + str(year) + '.GRD'

        else:
            if fn_format == 'yearwise':
                if Path(var_type).exists():
                    fname = var_type + '/' + str(year) + '.GRD'
                else:
                    fname = str(year) + '.GRD'

            else:
                if Path(var_type).exists():
                    fname = var_type + '/' + 'Maxtemp_MaxT_' + str(year) + '.GRD'
                else:
                    fname = 'Maxtemp_MaxT_' + str(year) + '.GRD'

    elif var_type == 'tmin':

        if file_dir is not None:
            if fn_format == 'yearwise':
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + str(year) + \
                            '.GRD'
                else:
                    fname = file_dir + '/' + str(year) + '.GRD'
            else:
                if Path('{}{}{}'.format(file_dir, '/', var_type)).exists():
                    fname = file_dir + '/' + var_type + '/' + 'Mintemp_MinT_' + \
                            str(year) + '.GRD'
                else:
                    fname = file_dir + '/' + 'Mintemp_MinT_' + str(year) + '.GRD'

        else:
            if fn_format == 'yearwise':
                if Path(var_type).exists():
                    fname = var_type + '/' + str(year) + '.GRD'
                else:
                    fname = str(year) + '.GRD'

            else:
                if Path(var_type).exists():
                    fname = var_type + '/' + 'Mintemp_MinT_' + str(year) + '.GRD'
                else:
                    fname = 'Mintemp_MinT_' + str(year) + '.GRD'

    else:
        raise Exception("Error in variable type declaration."
                        " It must be 'rain'/'tmin'/'tmax'.")

    return fname


def get_filename_realtime(day, var_type, file_dir):
    """
    Get filename for reading the real-time file content in future
    """
    if var_type == 'rain':
        if file_dir is not None:
            fname = file_dir + '/' + 'rain_ind0.25_' + day.strftime("%y_%m_%d") + '.grd'
        else:
            fname = 'rain_ind0.25_' + day.strftime("%y_%m_%d") + '.grd'

    elif var_type == 'rain_gpm':

        if file_dir is not None:
            fname = file_dir + '/' + day.strftime("%d%m%Y") + '.grd'
        else:
            fname = day.strftime("%d%m%Y") + '.grd'

    elif var_type == 'tmax':

        if file_dir is not None:
            fname = file_dir + '/' + 'max' + day.strftime("%d%m%Y") + '.grd'
        else:
            fname = 'max' + day.strftime("%d%m%Y") + '.grd'

    elif var_type == 'tmin':

        if file_dir is not None:
            fname = file_dir + '/' + 'min' + day.strftime("%d%m%Y") + '.grd'
        else:
            fname = 'min' + day.strftime("%d%m%Y") + '.grd'

    else:
        raise Exception("Error in variable type declaration."
                        " It must be 'rain'/'rain_gpm'/'temp'/'tmax'.")

    return fname
