import numpy as np
from datetime import date
from pathlib import Path


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
                        " It must be 'rain'/'temp'/'tmax'.")

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
                        " It must be 'rain'/'temp'/'tmax'.")

    return fname
