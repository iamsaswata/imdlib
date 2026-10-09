import numpy as np
import pandas as pd
import os
import requests
from imdlib.core import IMD
from imdlib.lazy import GrdFiles
from imdlib.util import get_filename_realtime
from imdlib.util import GRIDS, REALTIME_GRID, REALTIME_URLS, save_download, read_grd, post
from imdlib.util import REALTIME_GRIDS  # noqa: F401 (was importable from here)

def open_real_data(var_type, start_dy, end_dy=None, file_dir=None):

    """

    Function to read real-time binary data and return an IMD class object

    Binary Rainfall @0.25 spatial resolution

    Binary Temperature @0.50 spatial resolution

    Parameters
    ----------
    var_type : str
        Four possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "rain_gpm" -> input files are for daily GPM rainfall values
        3. "tmin" -> input files are for daily minimum temperature values
        4. "tmax" -> input files are for daily maximum tempereature values

    start_dy : str
        Starting day for opening data in format YYYY-MM-DD (e.g., '2020-01-31')

    end_dy : str
        Ending day for opening data in format YYYY-MM-DD (e.g., '2020-02-05')

    file_dir   : str or None
        Directory where files are stored.
        If None, the currently working directory is used.

    Returns
    -------
    IMD object

    """

    return _open_realtime(var_type, start_dy, end_dy,
                          lambda day: get_filename_realtime(day, var_type, file_dir))


def _open_realtime(var_type, start_dy, end_dy, fname_of_day, lazy=False):
    """
    Read daily real-time files and build an IMD object (shared by
    ``open_real_data`` and ``load``). ``fname_of_day(day)`` returns the
    file path for a day (pandas Timestamp). If ``lazy``, the files are
    read on first use of the data (``load``).
    """

    # Format Date into <yyyy-mm-dd>

    # Handling ending year not given case
    if sum([bool(start_dy), bool(end_dy)]) == 1:
        end_dy = start_dy

    # no_days = total_days(start_day, end_day)
    days = pd.date_range(start_dy, end_dy, freq='D')

    # Decide which variable we are looking into
    # tuple(): an unhashable var_type gets the same error as before, not a TypeError
    if var_type not in tuple(REALTIME_GRID):
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'rain_gpm'/'tmin'/'tmax'. ")
    grid = GRIDS[REALTIME_GRID[var_type]]
    lat_size_class, lon_size_class = grid.shape

    if lazy:
        source = GrdFiles(var_type, [(fname_of_day(day), 1) for day in days],
                          lat_size_class, lon_size_class, 0, len(days), land_mask=False)
        data = IMD(None, var_type, start_dy, end_dy, len(days), grid.lat, grid.lon)
        data._attach_source(source)
        return data

    # Loop through all the years
    # all_data -> container to store data for all the year
    # all_data.shape = (no_days, len(lon), len(lat))
    all_data = np.empty((len(days), lon_size_class, lat_size_class))
    # Counter for total days. It helps filling 'all_data' array.
    #print(all_data.shape)
    
    count_day = 0
    for day in days:

        # Decide resolution of input file name
        fname = fname_of_day(day)

        # Read float32 values and reshape into a shape of
        # (1, lon_size_class, lat_size_class);
        # they are stored as float64 in 'all_data'
        data = read_grd(fname, 1, lat_size_class, lon_size_class)
        all_data[count_day:count_day + 1, :, :] = data
        count_day += len(data)
        # Stack data vertically to get multi-year data
        # if i != time_range[0]:
        #     all_data = np.vstack((all_data, data))
        # else:
        #     all_data = data

    # Create a IMD object
    return IMD(all_data, var_type, start_dy, end_dy, len(days), grid.lat, grid.lon)


def get_real_data(var_type, start_dy, end_dy=None, file_dir=None, proxies=None):
    """
    Function to download real-time IMD data at daily timescale

    Binary Rainfall @0.25 spatial resolution

    Binary Temperature @0.50 spatial resolution

    Parameters
    ----------
    var_type : str
        Four possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "rain_gpm" -> input files are for daily GPM rainfall values
        3. "tmin" -> input files are for daily minimum temperature values
        4. "tmax" -> input files are for daily maximum tempereature values

    start_dy : str
        Starting day for opening data in format YYYY-MM-DD (e.g., '2020-01-31')

    end_dy : str
        Ending day for opening data in format YYYY-MM-DD (e.g., '2020-02-05')

    file_dir   : str or None
        Directory for saving downloaded files.
        If None, the currently working directory is used.

    proxies : dict
        Give details in curly bracket as shown in the example below
        e.g. proxies = { 'http' : 'http://uname:password@ip:port'}
        
    Returns
    -------
    IMD object

    """
    
    fend = '.grd'
    if var_type == 'rain':
        var = 'rain'
        url = REALTIME_URLS['rain'][0] # new url (dated:Oct 10, 2022)
        fini = 'rain_ind0.25_'
    elif var_type == 'rain_gpm':
        var = 'rain'
        url = REALTIME_URLS['rain_gpm'][0]
        fini = ''
    elif var_type == 'tmax':
        var = 'max'
        url = REALTIME_URLS['tmax'][0] # new url (dated:Oct 10, 2022)
        fini = 'max'
    elif var_type == 'tmin':
        var = 'min'
        url = REALTIME_URLS['tmin'][0] # new url (dated:Oct 10, 2022)
        fini = 'min'
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'rain_gpm'/'tmin'/'tmax'. ")

    # Handling ending date not given case
    if sum([bool(start_dy), bool(end_dy)]) == 1:
        end_dy = start_dy

    # no_days = total_days(start_day, end_day)
    days = pd.date_range(start_dy, end_dy, freq='D')

    # Handling location for saving data
    if file_dir is not None:
        if not os.path.isdir(file_dir):
            os.mkdir(file_dir)
    try:
        for day in days:
            if var_type == 'rain':
                f_mid = day.strftime("%y_%m_%d")
            elif var_type == 'rain_gpm':
                f_mid = day.strftime("%d%m%Y")
            else:
                f_mid = day.strftime("%d%m%Y")

            # Setting file name
            if file_dir is not None:
                fname = file_dir + '/' + fini + f_mid + fend
            else:
                fname = fini + f_mid + fend

            # Skip download if file already exists and is not corrupt
            if os.path.isfile(fname) and os.path.getsize(fname) >= 1024:
                print("File already exists: " + fname + ". Skipping download.")
                continue

            # Setting parameters
            print("Downloading: " + var + " for date " + str(day.date()))

            data = {var: day.strftime("%d%m%Y")}
            # Requesting the dataset
            response = post(url, data, "{} for date {}".format(var_type, str(day.date())), proxies=proxies)
            response.raise_for_status()

            # Saving file (only if it has exactly the expected size)
            save_download(response.content, fname, GRIDS[REALTIME_GRID[var_type]].file_size(),
                          "{} for date {}".format(var_type, str(day.date())),
                          empty_msg="Error in file download. Real-time {} for date {} is "
                                    "not available (IMD returned an empty file; recent days "
                                    "may not be published yet). Nothing was saved."
                                    .format(var_type, str(day.date())))

        print("Download Successful !!!")

        data = open_real_data(var_type, start_dy, end_dy, file_dir)
        return data

    except requests.exceptions.HTTPError as e:
        print("File Download Failed! Error: {}".format(e))    
