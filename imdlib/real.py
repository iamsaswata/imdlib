import array
import numpy as np
import pandas as pd
import os
import requests
from imdlib.core import IMD
from imdlib.util import get_filename_realtime

def open_real_data(var_type, start_dy, end_dy=None, file_dir=None):

    """

    Function to read real-time binary data and return an IMD class object  
      
    Binary Rainfall @0.25 spatial resolution  
      
    Bineary Temperature @0.25 spatial resolution  

    Parameters
    ----------
    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

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

    lat_size_rain = 129
    lon_size_rain = 135
    lat_rain = np.linspace(6.5, 38.5, lat_size_rain)
    lon_rain = np.linspace(66.5, 100.0, lon_size_rain)

    lat_size_temp = 61
    lon_size_temp = 61
    lat_temp = np.linspace(7.5, 37.5, lat_size_temp)
    lon_temp = np.linspace(67.5, 97.5, lon_size_temp)
    #######################################
    # Format Date into <yyyy-mm-dd>

    # Handling ending year not given case
    if sum([bool(start_dy), bool(end_dy)]) == 1:
        end_dy = start_dy

    # no_days = total_days(start_day, end_day)
    days = pd.date_range(start_dy, end_dy, freq='D')

    # Decide which variable we are looking into
    if var_type == 'rain':
        lat_size_class = lat_size_rain
        lon_size_class = lon_size_rain
    elif var_type == 'tmin' or var_type == 'tmax':
        lat_size_class = lat_size_temp
        lon_size_class = lon_size_temp
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'tmin'/'tmax'. ")

    # Loop through all the years
    # all_data -> container to store data for all the year
    # all_data.shape = (no_days, len(lon), len(lat))
    all_data = np.empty((len(days), lon_size_class, lat_size_class))
    # Counter for total days. It helps filling 'all_data' array.
    #print(all_data.shape)
    
    count_day = 0
    for day in days:

        # Decide resolution of input file name
        fname = get_filename_realtime(day, var_type, file_dir)

        # temporary variable to read binary data
        temp = array.array("f")
        with open(fname, 'rb') as f:
            temp.fromfile(f, os.stat(fname).st_size // temp.itemsize)

        data = np.array(list(map(lambda x: x, temp)))
        
        # Added for new url (dated:Oct 10, 2022)
        # Removing first element for rain for new url: https://imdpune.gov.in/lrfindex.php 
        #if var_type == 'rain':
        #    data = data[1:]
        
        nlen = lat_size_class * lon_size_class
        # Check consistency of data points
        if len(data) != nlen:
            raise Exception("Error in file reading,"
                            "mismatch in size of data-length")

        # Reshape data into a shape of
        # (days_in_year, lon_size_class, lat_size_class)
        data = np.transpose(np.reshape(data, (1, lat_size_class,
                                              lon_size_class), order='C'), (0, 2, 1))
        all_data[count_day:count_day + 1, :, :] = data
        count_day += len(data)
        # Stack data vertically to get multi-year data
        # if i != time_range[0]:
        #     all_data = np.vstack((all_data, data))
        # else:
        #     all_data = data

    # Create a IMD object
    if var_type == 'rain':
        data = IMD(all_data, 'rain', start_dy, end_dy, len(days),
                   lat_rain, lon_rain)
    elif var_type == 'tmin':
        data = IMD(all_data, 'tmin', start_dy, end_dy, len(days),
                   lat_temp, lon_temp)
    elif var_type == 'tmax':
        data = IMD(all_data, 'tmax', start_dy, end_dy, len(days),
                   lat_temp, lon_temp)
    else:
        raise Exception("Error in variable type declaration.\n"
                        "It must be 'rain'/'tmin'/'tmax'. ")

    return data


def get_real_data(var_type, start_dy, end_dy=None, file_dir=None, proxies=None):
    """
    Function to download real-time IMD data at daily timescale   
    
    Binary Rainfall @0.25 spatial resolution  
    
    Bineary Temperature @0.25 spatial resolution 
    
    Parameters
    ----------
    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

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
        url = 'https://imdpune.gov.in/cmpg/Realtimedata/Rainfall/rain.php' # new url (dated:Oct 10, 2022)
        fini = 'rain_ind0.25_'
    elif var_type == 'tmax':
        var = 'max'
        url = 'https://imdpune.gov.in/cmpg/Realtimedata/max/max.php' # new url (dated:Oct 10, 2022)
        fini = 'max'
    elif var_type == 'tmin':
        var = 'min'
        url = 'https://imdpune.gov.in/cmpg/Realtimedata/min/min.php' # new url (dated:Oct 10, 2022)
        fini = 'min'
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'tmin'/'tmax'. ")

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
            # Setting parameters
            print("Downloading: " + var + " for date " + str(day.date()))

            data = {var: day.strftime("%d%m%Y")}
            # Requesting the dataset
            response = requests.post(url, data=data, proxies=proxies)
            response.raise_for_status()
            
            if len(response.content) < 1:
                raise Exception("Error in file download. \nData not downloaded for date : {}. \nStopping IMDLIB".format(str(day.date())))
                return data
            
            if var_type == 'rain':
                f_mid = day.strftime("%y_%m_%d")
            else:
                f_mid = day.strftime("%d%m%Y")
                
            # Setting file name
            if file_dir is not None:
                fname = file_dir + '/' + fini + f_mid + fend
            else:
                fname = fini + f_mid + fend
                
            # Saving file
            with open(fname, 'wb') as f:
                f.write(response.content)

        print("Download Successful !!!")

        data = open_real_data(var_type, start_dy, end_dy, file_dir)
        return data

    except requests.exceptions.HTTPError as e:
        print("File Download Failed! Error: {}".format(e))    
