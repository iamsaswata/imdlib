import numpy as np
import pandas as pd
from datetime import date
from pathlib import Path


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
