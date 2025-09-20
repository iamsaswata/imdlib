"""
Developed by Saswata Nandi, Pratiman Patel and Sabyasachi Swain 
"""

import array
import numpy as np
import pandas as pd
import os
import sys
import requests
import xarray as xr
from dataclasses import dataclass
from pathlib import Path
from imdlib.util import LeapYear, get_lat_lon, total_days, get_filename
from datetime import datetime, date
# Added 14-05-2023 #
from scipy.interpolate import griddata 
from imdlib.compute import Compute
from imdlib.naming import long_name_dict_anu, short_name_dict, units_dic_anu
try:
    import rioxarray as rio
    has_rioxarray = True
except ImportError:
    has_rioxarray = False
try:
    from shapefile import Reader
    has_shapefile = True
except ImportError:
    has_shapefile = False
try:
    from shapely.geometry import shape, Point
    from shapely.ops import unary_union
    has_shapely = True
except ImportError:
    has_shapely = False

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
# Added 14-05-2023 #

class IMD(Compute):
    """
    Class to handle binary (.grd) IMD gridded meteorological data.
    Currently rain, tmin and tmax variable processing is supported.

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
        self.computed = False

    @property
    def shape(self):
        print(self.data.shape)

    def to_csv(self, file_name=None, lat=None, lon=None, out_dir=None):

        if file_name is None:
            file_name = 'test'

        root, ext = os.path.splitext(file_name)
        if not ext:
            ext = '.csv'

        # check lat and lon are in the feasible range
        if lat is not None and lon is not None:
            if lat > max(self.lat_array) and lat < min(self.lat_array):
                raise Exception("Error in given lat coordinates."
                                "Given lat value is not in the IMD data range!! ")
            if lon > max(self.lon_array) and lon < min(self.lon_array):
                raise Exception("Error in in given lon coordinates."
                                "Given lon value is not in the IMD data range!! ")

        if lat is None and lon is None:
            print("Latitude and Longitude are not given!!")
            print("Converting 3D data to 2D data!!")
            print("You should reconsider this operation!!")
            outname = root + ext
            self.get_xarray().to_dataframe().to_csv(outname)

        elif sum([bool(lat), bool(lon)]) == 1:
            raise Exception("Error in lat lon setting."
                            "One of them is set and the other one remained unset!! ")
        else:
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
                                                            root, '_',
                                                            lat, '_', lon, ext)
            else:
                outname = "{}{}{:.2f}{}{:.2f}{}".format(root, '_',
                                                        lat, '_', lon, ext)
            csv = pd.DataFrame(self.data[:, lon_index, lat_index],
                               index=pd.date_range(start=self.start_day,
                                                   end=self.end_day, freq='D'))
            #csv = csv.columns(str(lat) + str(lon))
            # pd.DataFrame(self.data[:, lon_index, lat_index]
            #             ).to_csv(outname,
            #                      index=True,
            #                      header=True,
            #                      float_format='%.4f')
            csv.to_csv(outname,
                       index=True,
                       index_label='DateTime',
                       header=[str(lat) + ' ' + str(lon)],
                       float_format='%.4f')

    def get_xarray(self):

        # swaping axes (time,lon,lat) > (time, lat,lon)
        # to create xarray object
        data_xr = np.swapaxes(self.data, 1, 2)
        # To support computed data; Added on 23-07-2023
        if self.computed:
            if self.scale  == 'A':
                time = pd.date_range(self.start_day, periods=self.data.shape[0], freq = 'A')
        else:
            time = pd.date_range(self.start_day, periods=self.no_days)
        time_units = 'days since {:%Y-%m-%d 00:00:00}'.format(time[0])
        if self.cat == 'rain':
            xr_da = xr.Dataset({'rain': (['time', 'lat', 'lon'], data_xr,
                                         {'units': 'mm/day', 'long_name': 'Rainfall'})},
                               coords={'lat': self.lat_array,
                                       'lon': self.lon_array, 'time': time})
            xr_da_masked = xr_da.where(xr_da.values != -999.)
        elif self.cat == 'tmin':
            xr_da = xr.Dataset({'tmin': (['time', 'lat', 'lon'], data_xr,
                                         {'units': 'C', 'long_name': 'Minimum Temperature'})},
                               coords={'lat': self.lat_array,
                                       'lon': self.lon_array, 'time': time})
            xr_da_masked = xr_da.where(xr_da.values != data_xr[0, 0, 0])
        elif self.cat == 'tmax':
            xr_da = xr.Dataset({'tmax': (['time', 'lat', 'lon'], data_xr,
                                         {'units': 'C', 'long_name': 'Maximum Temperature'})},
                               coords={'lat': self.lat_array,
                                       'lon': self.lon_array, 'time': time})
            xr_da_masked = xr_da.where(xr_da.values != data_xr[0, 0, 0])

        xr_da_masked.time.encoding['units'] = time_units
        xr_da_masked.time.attrs['standard_name'] = 'time'
        xr_da_masked.time.attrs['long_name'] = 'time'

        xr_da_masked.lon.attrs['axis'] = 'X'  # Optional
        xr_da_masked.lon.attrs['long_name'] = 'longitude'
        xr_da_masked.lon.attrs['long_name'] = 'longitude'
        xr_da_masked.lon.attrs['units'] = 'degrees_east'

        xr_da_masked.lat.attrs['axis'] = 'Y'  # Optional
        xr_da_masked.lat.attrs['standard_name'] = 'latitude'
        xr_da_masked.lat.attrs['long_name'] = 'latitude'
        xr_da_masked.lat.attrs['units'] = 'degrees_north'

        xr_da_masked.attrs['Conventions'] = 'CF-1.7'
        xr_da_masked.attrs['title'] = 'IMD gridded data'
        xr_da_masked.attrs['source'] = 'https://imdpune.gov.in/'
        xr_da_masked.attrs['history'] = str(datetime.utcnow()) + ' Python'
        xr_da_masked.attrs['references'] = ''
        xr_da_masked.attrs['comment'] = ''
        xr_da_masked.attrs['crs'] = 'epsg:4326'

        return xr_da_masked

    def to_netcdf(self, file_name=None, out_dir=None):

        if file_name is None:
            file_name = 'test'

        root, ext = os.path.splitext(file_name)
        if not ext:
            ext = '.nc'

        xr_da_masked = self.get_xarray()
        if out_dir is not None:
            outname = "{}{}{}{}".format(out_dir, '/', root, ext)
        else:
            outname = "{}{}".format(root, ext)
        xr_da_masked.to_netcdf(outname)

    def to_geotiff(self, file_name=None, out_dir=None):
        try:
            import rioxarray as rio
            if file_name is None:
                file_name = 'test'
            root, ext = os.path.splitext(file_name)
            if not ext:
                ext = '.tif'
            xr_da_masked = self.get_xarray()
            xr_da_masked.rio.write_crs(xr_da_masked.crs, inplace=True)
            if out_dir is not None:
                outname = "{}{}{}{}".format(out_dir, '/', root, ext)
            else:
                outname = "{}{}".format(root, ext)
            xr_da_masked[self.cat].rio.write_nodata(xr_da_masked[self.cat].data[0, -1, -1], inplace=True).rio.to_raster(outname)
        except:
            raise Exception("rioxarray is not installed")
            
    def compute(self, method=None, scale=None, **kwargs) -> Compute:
        """
        Function for computing climat indices.

        It's easiest to use IMD_object.compute(...) to use Compute.

        Parameters
        ----------
        obj : IMD

        method : str
            Details in imdlib.core.naming module

        scale : str
           'A' -> Annual

        Returns
        -------
        IMD object
            Modified IMD object with computed climatic indices
        """

        self.computed = True
        self.method = method
        self.scale = scale
        return super().compute(method, scale, **kwargs)

    def fill_na(self):
        """
        Function to fill missing data in IMD object.

        This function perform spatial filling from neighbourhood gridpoints

        Returns
        -------
        IMD object
            Modified IMD object with filled missing values

        Examples
        --------
        >>> start_yr = 2015
        >>> end_yr = 2015
        >>> variable = 'tmax'
        >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
        >>> data.fill_na()
        """
        nan_hint = self.data[0, 0, 0]
        self.data[self.data == nan_hint] = np.nan
        # Find grid index where few nan present but not all times they are nan
        id_x, id_y = np.where(np.isnan(self.data).any(axis=0)
                              * ~ np.isnan(self.data).all(axis=0))
        if len(id_x) > 0:
            print('filling missing data')
            print('No of missing grids: {}'.format(len(id_x)))

            for i in range(len(id_x)):
                # print('filling missing data for {} grid out of {} grids'.
                # format(i+1, len(id_x)))
                # print('id_x[] : {}, id_y : {}'.format(id_x[i], id_y[i]))
                # not fill for odd lower right position in temperature data
                if self.cat != 'rain':
                    if (id_x[i] >= 24) and (id_x[i] <= 26) and (id_y[i] >= 1) \
                            and (id_y[i] <= 5):
                        self.data[:, id_x[i], id_y[i]] = np.nan
                        continue
                    if id_y[i] == 0:
                        self.data[:, id_x[i], id_y[i]] = np.nan
                        continue
                        continue
                for j in range(self.data.shape[0]):
                    window = 0
                    while np.isnan(self.data[j, id_x[i], id_y[i]]):
                        window += 1
                        trial_1 = np.nanmean(
                            self.data[j, id_x[i] - window:id_x[i] + window + 1,
                                      id_y[i] - window:id_y[i] + window + 1])
                        # print(window, trial_1)
                        self.data[j, id_x[i], id_y[i]] = trial_1                        

        else:
            print('No missing data')

    def remap(self, dnew):
        """
        Function to change resolution of IMD object.

        Parameters
        ----------
        dnew : float
            The new resolution.

        Returns
        -------
        IMD object
            Modified IMD object with new resolution

        Examples
        --------
        >>> start_yr = 2015
        >>> end_yr = 2015
        >>> variable = 'tmax'
        >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
        >>> data.remap(0.25)
        """
        x = self.lon_array
        y = self.lat_array
        Y, X = np.meshgrid(y, x)
        xf, yf = X.flatten(), Y.flatten()
        xnew = np.arange(self.lon_array.min(), self.lon_array.max() + dnew,
                         dnew)
        ynew = np.arange(self.lat_array.min(), self.lat_array.max() + dnew,
                         dnew)
        Ynew, Xnew = np.meshgrid(ynew, xnew)

        tmp = self.data.copy()
        tmp[tmp == tmp[0, 0, 0]] = np.nan
        self.data = np.zeros((tmp.shape[0], len(xnew), len(ynew)),
                             dtype=np.float64)
        for i in range(tmp.shape[0]):
            self.data[i, :, :] = griddata((xf, yf), tmp[i, :, :].flatten(),
                                          (Xnew, Ynew), method='nearest')

        self.lat_array = ynew
        self.lon_array = xnew

    def clip(self, shpfile):
        """
        Function to clip a IMD object for a roi using shapefile.

        Parameters
        ----------
        shpfile : str
            The name of shapefile with full path.

        Returns
        -------
        IMD object
            Modified IMD object clipped using the shapefile

        Examples
        --------
        >>> start_yr = 2015
        >>> end_yr = 2015
        >>> variable = 'tmax'
        >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
        >>> data.clip('shapefile_folder_path/shapefile_name.shp')
        """
        if has_shapefile and has_shapely:
            sf = Reader(shpfile)

            shapes = sf.shapes()
            polygons = [shape(shape_obj.__geo_interface__) for shape_obj in shapes]
            combined_polygon = unary_union(polygons)
            
            lon_min, lat_min, lon_max, lat_max = combined_polygon.bounds

            lon_min_indx = np.abs(self.lon_array - lon_min).argmin()
            lon_max_indx = np.abs(self.lon_array - lon_max).argmin()
            lat_min_indx = np.abs(self.lat_array - lat_min).argmin()
            lat_max_indx = np.abs(self.lat_array - lat_max).argmin()

            lon_min = self.lon_array[lon_min_indx]
            lon_max = self.lon_array[lon_max_indx]
            lat_min = self.lat_array[lat_min_indx]
            lat_max = self.lat_array[lat_max_indx]

            self.lon_array = self.lon_array[(self.lon_array >= lon_min) *
                                            (self.lon_array <= lon_max)]
            self.lat_array = self.lat_array[(self.lat_array >= lat_min) *
                                            (self.lat_array <= lat_max)]

            self.data = self.data[:, lon_min_indx:lon_max_indx + 1,
                                  lat_min_indx:lat_max_indx + 1]

            for i in range(self.data.shape[1]):      # lon loop
                for j in range(self.data.shape[2]):  # lat loop
                    pt = Point(self.lon_array[i], self.lat_array[j])
                    if not pt.within(combined_polygon):
                        self.data[:, i, j] = np.nan
        else:
            raise Exception("shapefile or shapely library is missing")

    def copy(self):
        """
        Function to make a non-immutable copy of the IMD object.

        Examples
        --------
        >>> start_yr = 2015
        >>> end_yr = 2015
        >>> variable = 'tmax'
        >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
        >>> tmp = data.copy() 
        """
        return IMD(self.data, self.cat, self.start_day, self.end_day, self.no_days, self.lat_array, self.lon_array)



def open_data(var_type, start_yr, end_yr=None, fn_format=None, file_dir=None):
    """   
    Function to read binary data and return an IMD class object
    time range is tuple or list or numpy array of 2 int number

    Parameters
    ----------
    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

    start_yr : int
        Starting year for opening data

    end_yr : int
        Ending year for opening data

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

    if start_yr is None:
        raise Exception(
            "Starting year must be provided when requesting rain, tmin or tmax data."
        )

    # Handling ending year not given case
    if sum([bool(start_yr), bool(end_yr)]) == 1:
        end_yr = start_yr

    start_day = "{}{}{:02d}{}{:02d}".format(start_yr, '-', 1, '-', 1)
    end_day = "{}{}{:02d}{}{:02d}".format(end_yr, '-', 12, '-', 31)

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
                        "It must be 'rain'/'tmin'/'tmax'. ")

    # Loop through all the years
    # all_data -> container to store data for all the year
    # all_data.shape = (no_days, len(lon), len(lat))
    all_data = np.empty((no_days, lon_size_class, lat_size_class))
    # Counter for total days. It helps filling 'all_data' array.
    count_day = 0
    for i in range(start_yr, end_yr + 1):

        # Decide resolution of input file name
        fname = get_filename(i, var_type, fn_format, file_dir)

        # Check if current year is leap year or not
        if LeapYear(i):
            days_in_year = 366
        else:
            days_in_year = 365

        # length of total data point for current year
        nlen = days_in_year * lat_size_class * lon_size_class

        # temporary variable to read binary data
        temp = array.array("f")
        with open(fname, 'rb') as f:
            temp.fromfile(f, os.stat(fname).st_size // temp.itemsize)

        data = np.array(list(map(lambda x: x, temp)))
        
        # Added for new url (dated:Oct 10, 2022)
        # Removing first element for rain for new url: https://imdpune.gov.in/lrfindex.php 
        # Removing (commented out) as it was no longer needed to remove the first element anymore. (dated: March 31, 2023)
        # if var_type == 'rain':
        #    data = data[1:]
            
        # Check consistency of data points
        if len(data) != nlen:
            raise Exception("Error in file reading,"
                            "mismatch in size of data-length")

        # Reshape data into a shape of
        # (days_in_year, lon_size_class, lat_size_class)
        data = np.transpose(np.reshape(data, (days_in_year, lat_size_class,
                                              lon_size_class), order='C'), (0, 2, 1))
        all_data[count_day:count_day + len(data), :, :] = data
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
                        "It must be 'rain'/'tmin'/'tmax'. ")

    return data            


def open_data(var_type, start_yr, end_yr=None, fn_format=None, file_dir=None):
    """   
    Function to read binary data and return an IMD class object
    time range is tuple or list or numpy array of 2 int number

    Parameters
    ----------
    var_type : str
        Three possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values

    start_yr : int
        Starting year for opening data

    end_yr : int
        Ending year for opening data

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

    # Handling ending year not given case
    if sum([bool(start_yr), bool(end_yr)]) == 1:
        end_yr = start_yr

    start_day = "{}{}{:02d}{}{:02d}".format(start_yr, '-', 1, '-', 1)
    end_day = "{}{}{:02d}{}{:02d}".format(end_yr, '-', 12, '-', 31)

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
                        "It must be 'rain'/'tmin'/'tmax'. ")

    # Loop through all the years
    # all_data -> container to store data for all the year
    # all_data.shape = (no_days, len(lon), len(lat))
    all_data = np.empty((no_days, lon_size_class, lat_size_class))
    # Counter for total days. It helps filling 'all_data' array.
    count_day = 0
    for i in range(start_yr, end_yr + 1):

        # Decide resolution of input file name
        fname = get_filename(i, var_type, fn_format, file_dir)

        # Check if current year is leap year or not
        if LeapYear(i):
            days_in_year = 366
        else:
            days_in_year = 365

        # length of total data point for current year
        nlen = days_in_year * lat_size_class * lon_size_class

        # temporary variable to read binary data
        temp = array.array("f")
        with open(fname, 'rb') as f:
            temp.fromfile(f, os.stat(fname).st_size // temp.itemsize)

        data = np.array(list(map(lambda x: x, temp)))
        
        # Added for new url (dated:Oct 10, 2022)
        # Removing first element for rain for new url: https://imdpune.gov.in/lrfindex.php 
        # Removing (commented out) as it was no longer needed to remove the first element anymore. (dated: March 31, 2023)
        # if var_type == 'rain':
        #    data = data[1:]
            
        # Check consistency of data points
        if len(data) != nlen:
            raise Exception("Error in file reading,"
                            "mismatch in size of data-length")

        # Reshape data into a shape of
        # (days_in_year, lon_size_class, lat_size_class)
        data = np.transpose(np.reshape(data, (days_in_year, lat_size_class,
                                              lon_size_class), order='C'), (0, 2, 1))
        all_data[count_day:count_day + len(data), :, :] = data
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
                        "It must be 'rain'/'tmin'/'tmax'. ")

    return data


@dataclass
class IMDData:
    """Container for IMD gridded datasets represented as :class:`xarray.Dataset`."""

    dataset: xr.Dataset
    variable: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp

    def to_xarray(self):
        """Return the underlying :class:`xarray.Dataset`."""

        return self.dataset

    def __getattr__(self, item):
        """Proxy attribute access to the wrapped dataset."""

        try:
            return getattr(self.dataset, item)
        except AttributeError as exc:  # pragma: no cover - defensive programming
            raise AttributeError(item) from exc


def _normalise_timestamp(value, parameter_name):
    """Convert different date representations to :class:`pandas.Timestamp`."""

    if value is None:
        raise ValueError(f"{parameter_name} must be provided")

    if isinstance(value, pd.Timestamp):
        ts = value
    elif isinstance(value, (datetime, date)):
        ts = pd.Timestamp(value)
    elif isinstance(value, (tuple, list)) and len(value) == 3:
        ts = pd.Timestamp(year=int(value[0]), month=int(value[1]), day=int(value[2]))
    else:
        try:
            ts = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Unable to interpret {parameter_name}: {value!r}"
            ) from exc

    return ts.normalize()


def _resolve_gpm_frequency(fn_format=None, frequency=None):
    """Return the canonical (format, frequency) tuple for GPM downloads."""

    fmt = fn_format.lower() if isinstance(fn_format, str) else None
    freq = frequency.lower() if isinstance(frequency, str) else None

    valid_formats = {None, "grd", "nc"}
    valid_frequencies = {None, "realtime", "monthly"}

    if fmt not in valid_formats:
        raise ValueError("fn_format must be either 'grd' or 'nc' for rain_gpm data")
    if freq not in valid_frequencies:
        raise ValueError("frequency must be 'realtime' or 'monthly' for rain_gpm data")

    if fmt is None and freq is None:
        fmt, freq = "grd", "realtime"
    elif fmt is None:
        fmt = "nc" if freq == "monthly" else "grd"
    elif freq is None:
        freq = "monthly" if fmt == "nc" else "realtime"

    if fmt == "nc" and freq != "monthly":
        raise ValueError("Monthly rain_gpm data must be requested with frequency='monthly'.")
    if fmt == "grd" and freq != "realtime":
        raise ValueError("Real-time rain_gpm data must use fn_format='grd'.")

    return fmt, freq


def _download_file(url, destination, proxies=None, chunk_size=2 ** 14):
    """Download a file unless it is already present on disk."""

    if Path(destination).exists():
        return

    response = requests.get(url, stream=True, proxies=proxies)
    response.raise_for_status()

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("wb") as file_handle:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                file_handle.write(chunk)


def _resolve_grid_shape(values_count, grid_shape=None):
    """Determine the grid shape for binary GPM rainfall files."""

    if grid_shape is not None:
        nlat, nlon = int(grid_shape[0]), int(grid_shape[1])
        expected = nlat * nlon
        if values_count in (0, expected, expected + 1):
            return nlat, nlon
        raise ValueError(
            "Binary rainfall file does not match the provided grid shape "
            f"({nlat}x{nlon})."
        )

    default_shape = (129, 135)
    expected = default_shape[0] * default_shape[1]
    if values_count in (0, expected, expected + 1):
        return default_shape

    raise ValueError(
        "Binary rainfall file does not match the expected grid size "
        f"({default_shape[0]}x{default_shape[1]})."
    )


def _build_coordinate(bounds, size):
    """Create a coordinate array spanning ``bounds`` with ``size`` points."""

    start, end = bounds
    if size == 1:
        return np.array([float(start)], dtype="float32")
    return np.linspace(float(start), float(end), int(size), dtype="float32")


def _infer_nc_variable(dataset):
    """Infer the rainfall variable name from a NetCDF dataset."""

    preferred_names = {"rain", "rainfall", "precip", "precipitation"}
    candidate = None

    for name, data_array in dataset.data_vars.items():
        dims_lower = {dim.lower() for dim in data_array.dims}
        if {"lat", "lon"}.issubset(dims_lower) or {
            "latitude",
            "longitude",
        }.issubset(dims_lower):
            if name.lower() in preferred_names:
                return name
            candidate = candidate or name

    if candidate:
        return candidate

    if dataset.data_vars:
        return next(iter(dataset.data_vars))

    raise ValueError("Unable to determine rainfall variable in NetCDF dataset")


def _load_gpm_grd(files, times, grid_shape=None, lat_bounds=None, lon_bounds=None):
    """Load daily binary rainfall files into an :class:`xarray.Dataset`."""

    if not files:
        raise ValueError("No files were provided for loading")

    lat_bounds = lat_bounds or (6.5, 38.5)
    lon_bounds = lon_bounds or (66.5, 100.0)

    data_arrays = []
    nlat = nlon = None

    for path in files:
        path = Path(path)
        raw = np.fromfile(path, dtype="<f4")

        if nlat is None or nlon is None:
            nlat, nlon = _resolve_grid_shape(raw.size, grid_shape)
            latitudes = _build_coordinate(lat_bounds, nlat)
            longitudes = _build_coordinate(lon_bounds, nlon)

        # Remove optional header value if present
        if raw.size == nlat * nlon + 1:
            raw = raw[1:]

        if raw.size != nlat * nlon:
            raise ValueError(
                f"File {path} does not match expected grid size {nlat}x{nlon}"
            )

        reshaped = raw.reshape((nlat, nlon))
        reshaped = np.where(reshaped < -998.0, np.nan, reshaped)
        data_arrays.append(reshaped.astype("float32"))

    stacked = np.stack(data_arrays, axis=0)
    dataset = xr.Dataset(
        {"rain_gpm": (("time", "lat", "lon"), stacked)},
        coords={
            "time": pd.DatetimeIndex(times),
            "lat": latitudes,
            "lon": longitudes,
        },
    )

    dataset = dataset.sortby("time")
    dataset["rain_gpm"].attrs.update(
        {"units": "mm/day", "long_name": "IMD GPM + Gauge merged rainfall"}
    )
    dataset.attrs.update(
        {
            "source": "India Meteorological Department",
            "frequency": "realtime",
        }
    )

    return dataset


def _load_gpm_nc(files, times):
    """Load monthly NetCDF rainfall files into an :class:`xarray.Dataset`."""

    datasets = []

    for path, timestamp in zip(files, times):
        path = Path(path)
        with xr.open_dataset(path) as opened:
            variable_name = _infer_nc_variable(opened)
            data_array = opened[variable_name].load()

        rename_map = {}
        for name in list(data_array.dims) + list(data_array.coords):
            lower = name.lower()
            if lower in {"latitude", "lat", "y"}:
                rename_map[name] = "lat"
            elif lower in {"longitude", "lon", "x"}:
                rename_map[name] = "lon"
            elif lower == "time":
                rename_map[name] = "time"

        if rename_map:
            data_array = data_array.rename(rename_map)

        if "lat" not in data_array.dims or "lon" not in data_array.dims:
            raise ValueError(
                f"Unable to identify latitude/longitude dimensions in {path}"
            )

        if "time" in data_array.dims:
            if data_array.sizes.get("time", 1) != 1:
                raise ValueError(
                    f"Expected a single time step in monthly file {path}"
                )
            data_array = data_array.isel(time=0)

        data_array = data_array.expand_dims(time=[pd.Timestamp(timestamp)])
        data_array = data_array.astype("float32")
        dataset = data_array.to_dataset(name="rain_gpm")

        dataset["rain_gpm"].attrs.update(
            {
                "units": "mm",
                "long_name": "IMD GPM + Gauge merged rainfall",
            }
        )
        dataset.attrs.update(
            {
                "source": "India Meteorological Department",
                "frequency": "monthly",
            }
        )

        datasets.append(dataset)

    if not datasets:
        raise ValueError("No NetCDF files were provided for loading")

    combined = xr.concat(datasets, dim="time").sortby("time")
    return combined


def _get_gpm_data(
    start_date,
    end_date,
    fn_format=None,
    download_dir="./data",
    frequency=None,
    proxies=None,
    grid_shape=None,
    lat_bounds=None,
    lon_bounds=None,
):
    """Download and open IMD GPM + gauge merged rainfall data."""

    start_ts = _normalise_timestamp(start_date, "start_date")
    end_ts = _normalise_timestamp(end_date, "end_date")

    if start_ts > end_ts:
        raise ValueError("start_date must be before or equal to end_date")

    fmt, freq = _resolve_gpm_frequency(fn_format, frequency)

    download_path = Path(download_dir).expanduser()
    download_path.mkdir(parents=True, exist_ok=True)

    files = []
    times = []
    urls = []

    if freq == "realtime":
        base_url = "https://www.imdpune.gov.in/cmpg/Realtimedata/gpm"
        for timestamp in pd.date_range(start_ts, end_ts, freq="D"):
            fname = f"{timestamp.day:02d}{timestamp.month:02d}{timestamp.year:04d}.{fmt}"
            local_path = download_path / fname
            files.append(local_path)
            times.append(timestamp)
            urls.append((f"{base_url}/{fname}", local_path))

        loader = lambda: _load_gpm_grd(
            files,
            times,
            grid_shape=grid_shape,
            lat_bounds=lat_bounds,
            lon_bounds=lon_bounds,
        )

    else:
        base_url = "https://www.imdpune.gov.in/cmpg/Griddata/Rainfall25Merged"
        start_period = start_ts.to_period("M")
        end_period = end_ts.to_period("M")

        for period in pd.period_range(start_period, end_period, freq="M"):
            fname = f"Rainfall25Merged_{period.year:04d}{period.month:02d}.{fmt}"
            local_path = download_path / fname
            files.append(local_path)
            times.append(period.to_timestamp())
            urls.append((f"{base_url}/{fname}", local_path))

        loader = lambda: _load_gpm_nc(files, times)

    for url, path in urls:
        _download_file(url, path, proxies=proxies)

    dataset = loader()
    time_index = pd.DatetimeIndex(dataset["time"].values)

    return IMDData(
        dataset=dataset,
        variable="rain_gpm",
        start_date=time_index.min(),
        end_date=time_index.max(),
    )


def get_data(
    var_type=None,
    start_yr=None,
    end_yr=None,
    fn_format=None,
    file_dir=None,
    sub_dir=False,
    proxies=None,
    **kwargs,
):
    """
    Function to download binary data and return an IMD class object
    time range is tuple or list or numpy array of 2 int number

    Idea and drafted by Pratiman Patel
    Implemented by Saswata Nandi

    Parameters
    ----------
    var_type : str
        Four possible values.
        1. "rain" -> input files are for daily rainfall values
        2. "tmin" -> input files are for daily minimum temperature values
        3. "tmax" -> input files are for daily maximum tempereature values
        4. "rain_gpm" -> IMD + GPM gauge merged rainfall (daily or monthly)

    start_yr : int or tuple, optional
        Starting year for downloading data. When ``var_type`` is ``'rain_gpm'`` this
        can also be provided as a date tuple (``YYYY, MM, DD``) or string if
        ``start_date`` is not supplied via keyword argument.

    end_yr : int or tuple, optional
        Ending year for downloading data. When ``var_type`` is ``'rain_gpm'`` this can
        also be expressed as a date tuple or string if ``end_date`` is omitted.

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

    proxies : dict
        Give details in curly bracket as shown in the example below
        e.g. proxies = { 'http' : 'http://uname:password@ip:port'}

    Returns
    -------
    IMD object or :class:`IMDData`

    """

    var_type = kwargs.get('var', var_type)

    if var_type == 'rain_gpm':
        start_date = kwargs.pop('start_date', start_yr)
        if end_yr is not None:
            default_end = end_yr
        else:
            default_end = start_date
        end_date = kwargs.pop('end_date', default_end)
        download_dir = kwargs.pop('download_dir', file_dir if file_dir is not None else './data')
        frequency = kwargs.pop('frequency', None)
        grid_shape = kwargs.pop('grid_shape', None)
        lat_bounds = kwargs.pop('lat_bounds', None)
        lon_bounds = kwargs.pop('lon_bounds', None)

        return _get_gpm_data(
            start_date=start_date,
            end_date=end_date,
            fn_format=fn_format,
            download_dir=download_dir,
            frequency=frequency,
            proxies=proxies,
            grid_shape=grid_shape,
            lat_bounds=lat_bounds,
            lon_bounds=lon_bounds,
        )

    if var_type == 'rain':
        var = 'rain'
        # url = 'https://imdpune.gov.in/Clim_Pred_LRF_New/rainfall.php' (old url)
        url = 'https://imdpune.gov.in/cmpg/Griddata/rainfall.php' # new url (dated:Oct 10, 2022)
        fini = 'Rainfall_ind'
        if fn_format == 'yearwise':
            fend = '.grd'
        else:
            fend = '_rfp25.grd'
    elif var_type == 'tmax':
        var = 'maxtemp'
        # url = 'https://imdpune.gov.in/Clim_Pred_LRF_New/maxtemp.php' (old url)
        url = 'https://imdpune.gov.in/cmpg/Griddata/maxtemp.php' # new url (dated:Oct 10, 2022)
        fini = 'Maxtemp_MaxT_'
        fend = '.GRD'
    elif var_type == 'tmin':
        var = 'mintemp'
        # url = 'https://imdpune.gov.in/Clim_Pred_LRF_New/mintemp.php' (old url)
        url ='https://imdpune.gov.in/cmpg/Griddata/mintemp.php' # new url (dated:Oct 10, 2022)
        fini = 'Mintemp_MinT_'
        fend = '.GRD'
    else:
        raise Exception("Error in variable type declaration."
                        "It must be 'rain'/'tmin'/'tmax'. ")

    # Handling ending year not given case
    if sum([bool(start_yr), bool(end_yr)]) == 1:
        end_yr = start_yr

    years = np.arange(start_yr, end_yr + 1)

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
            print("Downloading: " + var + " for year " + str(year))

            data = {var: year}
            # Requesting the dataset
            response = requests.post(url, data=data, proxies=proxies)
            response.raise_for_status()

            # Setting file name
            if file_dir is not None:
                if fn_format == 'yearwise':
                    fname = os.path.join(file_dir, var_type) + \
                        '/' + str(year) + fend
                else:
                    if sub_dir:
                        fname = os.path.join(
                            file_dir, var_type) + '/' + fini + str(year) + fend
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

        print("Download Successful !!!")

        data = open_data(var_type, start_yr, end_yr, fn_format, file_dir)
        return data

    except requests.exceptions.HTTPError as e:
        print("File Download Failed! Error: {}".format(e))
