"""
(c) by Saswata Nandi
contributed by Pratiman Patel
"""


import array
import numpy as np
import pandas as pd
import os
import requests
import xarray as xr
from imdlib.util import LeapYear, get_lat_lon, total_days, get_filename


class IMD(object):
    """
    Class to handle binary (.grd) IMD gridded meteorological data.
    Currently Rainfall, TMIN and TMAX variable processing is supported.

    Attributes
    ----------
    data : numpy 3D array
        Stores information in 3d numpy array.
        shape is (no_of_days, lon_size, lat_size).

    cat : str or None
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

    start_day : str
        starting date in format of <year(4 digit)-month(2 digit)-day(2 digit)>
        e.g. ('2018-01-01')

    end_day : str
        ending date in format of <year(4 digit)-month(2 digit)-day(2 digit)>
        e.g. ('2018-12-31')

    Methods
    ----------
    shape : show dimension of an IMD object

    get_xarray : return an xarray object from an IMD object

    to_netcdf : write an IMD object to a netcdf file

    to_csv : write an IMD object to a csv file

    to_ascii : write an IMD object to a ASCII/txt file

    ----------
    Returns
    ----------
    IMD object

    """

    def __init__(self, data, cat, start_day, end_day, no_days, lat, lon):
        self.data = data
        self.cat = cat
        self.start_day = start_day
        self.end_day = end_day
        self.lat_array = lat
        self.lon_array = lon
        self.no_days = no_days

    @property
    def shape(self):
        print(self.data.shape)

    def to_csv(self, file_name, lat=None, lon=None, out_dir=None):

        if self.cat == 'rain':
            lat_index, lon_index = get_lat_lon(lat, lon,
                                               self.lat_array, self.lon_array)
        elif self.cat == 'tmin' or self.cat == 'tmax':
            lat_index, lon_index = get_lat_lon(lat, lon, self.lat_array,
                                               self.lon_array)
        else:
            raise Exception("Error in variable type declaration."
                            "It must be 'rain'/'tmin'/'tmax'. ")

        if out_dir is not None:
            outname = "{}{}{}{}{:.2f}{}{:.2f}{}".format(out_dir, '/',
                                                        file_name, '_',
                                                        lat, '_', lon, '.csv')
        else:
            outname = "{}{}{:.2f}{}{:.2f}{}".format(file_name, '_',
                                                    lat, '_', lon, '.csv')

        pd.DataFrame(self.data[:, lon_index, lat_index]
                     ).to_csv(outname,
                              index=False,
                              header=None,
                              float_format='%.4f')

    def to_ascii(self, file_name, lat=None, lon=None, out_dir=None):

        if self.cat == 'rain':
            lat_index, lon_index = get_lat_lon(lat, lon,
                                               self.self.lat_array,
                                               self.self.lon_array)
        elif self.cat == 'tmin' or self.cat == 'tmax':
            lat_index, lon_index = get_lat_lon(lat, lon,
                                               self.self.lat_array,
                                               self.self.lon_array)
        else:
            raise Exception("Error in variable type declaration."
                            "It must be 'rain'/'tmin'/'tmax'. ")

        if out_dir is not None:
            outname = "{}{}{}{}{:.2f}{}{:.2f}{}".format(out_dir, '/',
                                                        file_name, '_',
                                                        lat, '_', lon,
                                                        '.txt')
        else:
            outname = "{}{}{:.2f}{}{:.2f}{}".format(file_name, '_',
                                                    lat, '_', lon, '.txt')

        outname = "{}{:.2f}{}{:.2f}{}".format('point_data_',
                                              lat, '_', lon, '.txt')
        pd.DataFrame(self.data[:, lon_index, lat_index]
                     ).to_csv(outname, sep=' ', index=False,
                              header=None, float_format='%.4f')

    def get_xarray(self):

        # swaping axes (time,lon,lat) > (lat,lon, time)
        # to create xarray object
        data_xr = np.swapaxes(self.data, 0, 2)
        time = pd.date_range(self.start_day, periods=self.no_days)
        if self.cat == 'rain':
            xr_da = xr.DataArray(data_xr,
                                 dims=('lat', 'lon', 'time'),
                                 coords={'lat': self.lat_array,
                                         'lon': self.lon_array, 'time': time},
                                 attrs={'long_name': 'rainfall',
                                        'units': 'mm/day'},
                                 name='rain')
            xr_da_masked = xr_da.where(xr_da.values != -999.)
        elif self.cat == 'tmin':
            xr_da = xr.DataArray(data_xr,
                                 dims=('lat', 'lon', 'time'),
                                 coords={'lat': self.lat_array,
                                         'lon': self.lon_array, 'time': time},
                                 attrs={'long_name': 'Minimum Temperature',
                                        'units': 'C'},
                                 name='tmin')
            xr_da_masked = xr_da.where(xr_da.values != data_xr[0, 0, 0])
        elif self.cat == 'tmax':
            xr_da = xr.DataArray(data_xr,
                                 dims=('lat', 'lon', 'time'),
                                 coords={'lat': self.lat_array,
                                         'lon': self.lon_array, 'time': time},
                                 attrs={'long_name': 'Maximum Temperature',
                                        'units': 'C'},
                                 name='tmax')
            xr_da_masked = xr_da.where(xr_da.values != data_xr[0, 0, 0])

        return xr_da_masked

    def to_netcdf(self, file_name, out_dir=None):
        xr_da_masked = self.get_xarray()
        if out_dir is not None:
            outname = "{}{}{}{}".format(out_dir, '/', file_name, '.nc')
        else:
            outname = "{}{}{}".format(file_name, '_', '.nc')
        xr_da_masked.to_netcdf(outname)


def open_data(time_range, var_type, fn_format=None, file_dir=None):
    """
    get_data(time_range, var_type, proxies=None, fn_format=None, file_dir=None, sub_dir=False):
    Function to read binary data and return an IMD class object
    time range is tuple or list or numpy array of 2 int number

    Parameters
    ----------
    time range : tuple, list or nd.array
        Two integers time_range represents starting and
        ending year for reading input files

    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

    fn_format   : str or None
        fn_format represent filename format. Default vales is None.
        Which means filesnames are accoding to the IMD conventionm and
        they are not changed after downloading from IMD server.
        If we specify fn_format = 'yearwise',
        filenames are renamed like <year.grd> (e.g. 2018.grd)

    file_dir   : str or None
        Directory cache the files for future processing.
        If None, the currently working directory is used.
        If specify the directory address,
        Main directory should contain 3 subdirectory. <rain>, <tmin>, <tmax>

    Returns
    -------
    IMD object

    """

    # Parameters about IMD grid from:
    # http://www.imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html
    #######################################
    lat_size_rain = 129
    lon_size_rain = 135
    lat_rain = np.linspace(6.5, 38.5, lat_size_rain)
    lon_rain = np.linspace(66.5, 100.0, lon_size_rain)

    lat_size_temp = 31
    lon_size_temp = 31
    lat_temp = np.linspace(7.5, 37.5, lat_size_temp)
    lon_temp = np.linspace(67.5, 97.5, lon_size_temp)
    #######################################
    # Format Date into <yyyy-mm-dd>
    start_day = "{}{}{:02d}{}{:02d}".format(time_range[0], '-', 1, '-', 1)
    end_day = "{}{}{:02d}{}{:02d}".format(time_range[1], '-', 12, '-', 31)

    # Get total no of days (considering leap years)
    no_days = total_days(start_day, end_day)

    # Decide which variable we are looking into
    if var_type == 'rain':
        lat_size_class = lat_size_rain
        lon_size_class = lon_size_rain
    elif var_type == 'tmin' or var_type == 'tmax':
        lat_size_class = lat_size_temp
        lon_size_class = lon_size_temp
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'temp'. ")

    # Loop through all the years
    # all_data -> container to store data for all the year
    # all_data.shape = (no_days, len(lon), len(lat))
    all_data = np.empty((no_days, lon_size_class, lat_size_class))
    # Counter for total days. It helps filling 'all_data' array.
    count_day = 0
    for i in range(time_range[0], time_range[1]+1):

        # Decide resolution of input file name
        fname = get_filename(i, var_type, fn_format, file_dir)

        # Check if current year is leap year or not
        if LeapYear(i):
            days_in_year = 366
        else:
            days_in_year = 365

        # length of total data point for current year
        nlen = days_in_year*lat_size_class*lon_size_class

        # temporary variable to read binary data
        temp = array.array("f")
        with open(fname, 'rb') as f:
            temp.fromfile(f, os.stat(fname).st_size // temp.itemsize)

        data = np.array(list(map(lambda x: x, temp)))

        # Check consistency of data points
        if len(data) != nlen:
            raise Exception("Error in file reading,"
                            "mismatch in size of data-length")

        # Reshape data into a shape of
        # (days_in_year, lon_size_class, lat_size_class)
        data = np.transpose(np.reshape(data, (days_in_year, lat_size_class,
                            lon_size_class), order='C'), (0, 2, 1))
        all_data[count_day:count_day+len(data), :, :] = data
        count_day += len(data)
        # Stack data vertically to get multi-year data
        # if i != time_range[0]:
        #     all_data = np.vstack((all_data, data))
        # else:
        #     all_data = data

    # Create a IMD object
    if var_type == 'rain':
        data = IMD(all_data, 'rain', start_day, end_day, no_days,
                   lat_rain, lon_rain)
    elif var_type == 'tmin':
        data = IMD(all_data, 'tmin', start_day, end_day, no_days,
                   lat_temp, lon_temp)
    elif var_type == 'tmax':
        data = IMD(all_data, 'tmax', start_day, end_day, no_days,
                   lat_temp, lon_temp)
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'temp'/'tmax'. ")

    return data



def get_data(time_range, var_type, proxies=None, fn_format=None, file_dir=None, sub_dir=False):
    """
    Function to read binary data and return an IMD class object
    time range is tuple or list or numpy array of 2 int number

    Idea and drafted by Pratiman Patel
    Implemented by Saswata Nandi

    Parameters
    ----------
    time range : tuple, list or nd.array
        Two integers time_range represents starting and
        ending year for reading input files

    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

    proxies : dict
        Give details in curly bracket as shown in the example below
        e.g. proxies = { 'http' : 'http://uname:password@ip:port'}


    fn_format   : str or None
        fn_format represent filename format. Default vales is None.
        Which means filesnames are accoding to the IMD conventionm and
        they are not changed after downloading from IMD server.
        If we specify fn_format = 'yearwise',
        filenames are renamed like <year.grd> (e.g. 2018.grd)

    file_dir   : str or None
        Directory cache the files for future processing.
        If None, the currently working directory is used.
        If specify the directory address,
        Main directory should contain 3 subdirectory. <rain>, <tmin>, <tmax>

    sub_dir : bool
        True : if you need subdirectory for each variable type
        False: Files will be saved directly under main directory

    Returns
    -------
    IMD object

    """

    if var_type=='rain':
        var = 'rain'
        url = 'http://www.imdpune.gov.in/Clim_Pred_LRF_New/rainfall.php'
        fini = 'Rainfall_ind'
        if fn_format == 'yearwise':
            fend = '.grd'
        else:
            fend = '_rfp25.grd'
    elif var_type=='tmax':
        var = 'maxtemp'
        url = 'http://www.imdpune.gov.in/Clim_Pred_LRF_New/maxtemp.php'
        fini = 'Maxtemp_MaxT_'
        fend = '.GRD'
    elif var_type=='tmin':
        var = 'mintemp'
        url = 'http://www.imdpune.gov.in/Clim_Pred_LRF_New/mintemp.php'
        fini = 'Mintemp_MinT_'
        fend = '.GRD'
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'temp'/'tmax'. ")

    years = np.arange(time_range[0], time_range[1]+1)

    # Handling location for saving data
    if file_dir is not None:

        if not os.path.isdir(file_dir):
            os.mkdir(file_dir)

        if fn_format == 'yearwise':
            if not os.path.isdir(os.path.join(file_dir, var_type)):
                os.mkdir(os.path.join(file_dir, var_type))
        else:
            if sub_dir:
                if not os.path.isdir(os.path.join(file_dir, var_type)):
                    os.mkdir(os.path.join(file_dir, var_type))
    else:
        if fn_format == 'yearwise':
            if not os.path.isdir(var_type):
                os.mkdir(var_type)
        else:
            if sub_dir:
                if not os.path.isdir(var_type):
                    os.mkdir(var_type)

    try:
        for year in years: 
            # Setting parameters
            data = {var: year}
            # Requesting the dataset 
            response = requests.post(url, data=data, proxies=proxies)
            response.raise_for_status()

            # Setting file name
            if file_dir is not None:
                if fn_format == 'yearwise':
                    fname = os.path.join(file_dir, var_type) + '/' + str(year) + fend
                else:
                    if sub_dir:
                        fname = os.path.join(file_dir, var_type) + '/' + fini + str(year) + fend
                    else:
                        fname = fini + str(year) + fend
            else:
                if fn_format == 'yearwise':
                    fname = var_type + '/' + str(year) + fend
                else:
                    if sub_dir:
                        fname = var_type + '/' + fini + str(year) + fend
                    else:
                        fname = fini + str(year) + fend

            # Saving file
            with open(fname, 'wb') as f:
                f.write(response.content)

        print("File Download Successful")
    except requests.exceptions.HTTPError as e:
        print("File Download Failed! Error: {}".format(e))