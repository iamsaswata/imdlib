import numpy as np
import pandas as pd
from datetime import datetime
from scipy.stats import norm, rankdata
import imdlib


class Compute(object):
    """
    Class for computing climat indices.

    It's easiest to use IMD_object.compute(...) to use Compute.

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
    """

    def compute(self, method, scale, **kwargs):
        if scale == 'A':
            try:
                method in func_anu_dict
                return func_anu_dict[method](self, **kwargs)
            except TypeError:
                raise Exception('{} method is not found'.format(method))
        elif scale == 'M':
            raise Exception('Monthly scale is not implemented yet')
        else:
            raise Exception('Called temporal scale is not defined')


def bk_point(data):
    """
    This method provides index (last date) of every year from daily data.

    Parameters
    ----------
        data:   a IMD class object

    Returns
    -------
        bk_list: numpy array of indices

    Examples
    --------
      >>> start_yr = 2016
      >>> end_yr = 2018
      >>> variable = 'tmax' # other options are ('tmin'/ 'tmax')
      >>> data_tmax = imd.open_data(variable, start_yr, end_yr,'yearwise')
      >>> bk_list = bk_point(data_tmax)
      >>> bk_list = bk_point(data_tmax)
      >>> array([ 366,  731, 1096])
    """
    if not isinstance(data, imdlib.core.IMD):
        raise Exception('Input data is not an instance of IMD class')
    else:
        start_year = datetime.strptime(data.start_day, '%Y-%m-%d').year
        end_year = datetime.strptime(data.end_day, '%Y-%m-%d').year
        N = end_year - start_year + 1
        bk_list = []
        for i in range(N):
            if pd.Timestamp(year=start_year + i, month=1, day=1).is_leap_year:
                bk_list.append(366)
            else:
                bk_list.append(365)
        bk_list = np.array(bk_list, dtype=np.int32)
        bk_list = np.array(np.cumsum(bk_list))
        return bk_list


def bk_point_month(data):
    """
    This method provides index (last date) of every month from daily data.

    Parameters
    ----------
        data:   a IMD class object

    Returns
    -------
        bk_list: numpy array of indices

    Examples
    --------
      >>> start_yr = 2016
      >>> end_yr = 2018
      >>> variable = 'tmax' # other options are ('tmin'/ 'tmax')
      >>> data_tmax = imd.open_data(variable, start_yr, end_yr,'yearwise')
      >>> bk_list = bk_point(data_tmax)
      >>> bk_list = bk_point(data_tmax)
      >>> array([ 31,   59,   90,  120,  151,  181,  212,  243,  273,  304,
                 334,  365,  396,  424,  455,  485,  516,  546,  577,  608,
                 638,  669, 699,  730,  761,  789,  820,  850,  881,  911,
                 942,  973, 1003, 1034, 1064, 1095])
    """

    if not isinstance(data, imdlib.core.IMD):
        raise Exception('Input data is not an instance of IMD class')
    else:
        start_year = datetime.strptime(data.start_day, '%Y-%m-%d').year
        end_year = datetime.strptime(data.end_day, '%Y-%m-%d').year
        N = end_year - start_year + 1
        bk_list = []
        nrm_yr = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        lp_yr = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        for i in range(N):
            if pd.Timestamp(year=start_year + i, month=1, day=1).is_leap_year:
                bk_list.append(lp_yr)
            else:
                bk_list.append(nrm_yr)
        bk_list = np.array(bk_list, dtype=np.int32)
        bk_list = np.array(np.cumsum(bk_list))
        return bk_list


def __preprocessing(x):
    """
    Function for supporting computation of trend.

    Parameters
    ----------
    x : ndarray

    Returns
    -------
    x : ndarray
        Flatten if (2D)

    c : int
        Second dimension
    """

    x = np.asarray(x)
    dim = x.ndim
    if dim == 1:
        c = 1
    elif dim == 2:
        (n, c) = x.shape

        if c == 1:
            dim = 1
            x = x.flatten()
    else:
        print('Please check your dataset.')
    return x, c


def __missing_values_analysis(x, method='skip'):
    """
    Function for missing value analysis for computation of trend.

    Parameters
    ----------
    x : ndarray
    method : str

    Returns
    -------
    x : ndarray

    n : int
        number of datapoints with no missing value
    """
    if method.lower() == 'skip':
        if x.ndim == 1:
            x = x[~np.isnan(x)]
        else:
            x = x[~np.isnan(x).any(axis=1)]
    n = len(x)
    return x, n


def __mk_score(x, n):
    """
    Function for computing "S" score in MK method.

    Parameters
    ----------
    x : ndarray
    n : int

    Returns
    -------
    s : float
    """
    s = 0
    demo = np.ones(n)
    for k in range(n - 1):
        s = s + np.sum(demo[k + 1:n][x[k + 1:n] > x[k]]) - \
            np.sum(demo[k + 1:n][x[k + 1:n] < x[k]])
    return s


def __variance_s(x, n):
    """
    Function for computing original Mann-Kendal's variance S

    Parameters
    ----------
    x : ndarray
    n : int

    Returns
    -------
    var_s : float
    """

    # calculate the unique data
    unique_x = np.unique(x)
    g = len(unique_x)

    # calculate the var(s)
    if n == g:            # there is no tie
        var_s = (n * (n - 1) * (2 * n + 5)) / 18

    else:                 # there are some ties in data
        tp = np.zeros(unique_x.shape)
        demo = np.ones(n)

        for i in range(g):
            tp[i] = np.sum(demo[x == unique_x[i]])

        var_s = (n * (n - 1) * (2 * n + 5) -
                 np.sum(tp * (tp - 1) * (2 * tp + 5))) / 18

    return var_s


def __sens_estimator(x):
    """
    Function for computing original Sens Estimator
    Parameters
    ----------
    x : ndarray

    Returns
    -------
    d : ndarray
    """

    idx = 0
    n = len(x)
    d = np.ones(int(n * (n - 1) / 2))

    for i in range(n - 1):
        j = np.arange(i + 1, n)
        d[idx: idx + len(j)] = (x[j] - x[i]) / (j - i)
        idx = idx + len(j)

    return d


def sens_slope(x):
    """
    This method proposed by Theil (1950) and Sen (1968) to estimate the
    magnitude of the monotonic trend.
    Intercept calculated using Conover, W.J. (1980) method.

    Parameters
    ----------
        x:   a one dimensional vector (list, numpy array or pandas series) data

    Returns
    -------
        slope: Theil-Sen estimator/slope

    Examples
    --------
      >>> x = np.random.rand(120)
      >>> slope,intercept = sens_slope(x)
    """

    x, c = __preprocessing(x)
    slope = np.nanmedian(__sens_estimator(x))
    return slope


def __acf(x, nlags):
    """
    Function for computing ACF

    Parameters
    ----------
    x : ndarray
    nlags : int

    Returns
    -------
    ACF : float
    """

    y = x - x.mean()
    n = len(x)
    d = n * np.ones(2 * n - 1)

    acov = (np.correlate(y, y, 'full') / d)[n - 1:]

    return acov[:nlags + 1] / acov[0]


def __z_score(s, var_s):
    """
    Function for computing standardized test statistic Z

    Parameters
    ----------
    s : float
    var_s : float

    Returns
    -------
    z : float
    """

    if s > 0:
        z = (s - 1) / np.sqrt(var_s)
    elif s == 0:
        z = 0
    elif s < 0:
        z = (s + 1) / np.sqrt(var_s)

    return z


def __p_value(z, alpha):
    """
    Function for computing standardized test statistic p_value

    Parameters
    ----------
    z : float
    alpha : float

    Returns
    -------
    p : float
    h : float
    trend : str
    """

    # two tail test
    p = 2 * (1 - norm.cdf(abs(z)))
    h = abs(z) > norm.ppf(1 - alpha / 2)

    if (z < 0) and h:
        trend = 'decreasing'
    elif (z > 0) and h:
        trend = 'increasing'
    else:
        trend = 'no trend'

    return p, h, trend


def mmk_hr(x_old, alpha=0.05, lag=None):
    """
    This function checks the Modified Mann-Kendall (MK) test
    using Hamed and Rao (1998) method.

    Parameters
    ----------
    x_old: a vector (list, numpy array or pandas series) data
    alpha: significance level (0.05 default)
    lag: No. of First Significant Lags (default None,
        You can use 3 for considering first 3 lags,
        which also proposed by Hamed and Rao(1998))

    Returns
    -------
    z: normalized test statistics
    """

    x, c = __preprocessing(x_old)
    x, n = __missing_values_analysis(x, method='skip')

    s = __mk_score(x, n)
    var_s = __variance_s(x, n)
    # Tau = s / (.5 * n * (n - 1))

    # Hamed and Rao (1998) variance correction
    if lag is None:
        lag = n
    else:
        lag = lag + 1

    # detrending
    # x_detrend = x - np.multiply(range(1,n+1), np.median(x))
    slope = sens_slope(x_old)
    x_detrend = x - np.arange(1, n + 1) * slope
    II = rankdata(x_detrend)

    # account for autocorrelation
    acf_1 = __acf(II, nlags=lag - 1)
    interval = norm.ppf(1 - alpha / 2) / np.sqrt(n)
    upper_bound = 0 + interval
    lower_bound = 0 - interval

    sni = 0
    for i in range(1, lag):
        if (acf_1[i] <= upper_bound and acf_1[i] >= lower_bound):
            sni = sni
        else:
            sni += (n - i) * (n - i - 1) * (n - i - 2) * acf_1[i]

    n_ns = 1 + (2 / (n * (n - 1) * (n - 2))) * sni
    var_s = var_s * n_ns

    z = __z_score(s, var_s)
    # p, h, trend = __p_value(z, alpha)
    return z


def sen_percerntage(x):
    """
    Computes change magnitude as percentage of mean from
    Sen's slope estimator

    Parameters
    ----------
    x: ndarray
        Numpy array of a variable

    Returns
    -------
    pct_change: float
        Percentage change from sens slope estimates
    """

    slope = sens_slope(x)
    pct_change = slope * 100 * len(x) / np.mean(x)
    return pct_change


def spr(x):
    """
    Computes Spearman’s rho test statistics (Zsr) for trend analysis

    Parameters
    ----------
    x: ndarray
        Numpy array of a variable

    Returns
    -------
    Zsr: float
    """

    rx = rankdata(x)
    n = len(x)
    rho_sp_nume = 6 * np.sum(np.square((rx - np.arange(1, n+1))))
    rho_sp_deno = n * (n * n - 1)
    rho_sp = 1 - rho_sp_nume / rho_sp_deno
    Zsr = rho_sp * np.sqrt((n - 2) / (1 - rho_sp * rho_sp))
    return Zsr


def anu_trend(imd_obj):
    """
    Function for annual trend analysis

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed trend statistics

    Examples
    --------
    Computes trend statistics using one of the below method

    1. Modified Mann-Kendall (MK) test
    2. Spearman’s rho test
    3. Sens slope estimator
    4. Percentage change in trend magnitude using sens slope estimator

    >>> start_yr = 1990
    >>> end_yr = 2019
    >>> variable = 'tmax'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('spr', 'A')
    """

    if not isinstance(imd_obj, imdlib.core.IMD):
        raise Exception('Input data is not an instance of IMD class')
    else:
        # data = imd_obj.data.copy()
        data = imd_obj.data
        # nan_hint = data[0, 0, 0]
        bk_list = bk_point(imd_obj)
        out_data = np.ones(
            (1, data.shape[1], data.shape[2]), dtype=np.float64) * np.nan
        if imd_obj.cat == 'rain':
            new_data = np.ones(
                (bk_list.shape[0], data.shape[1], data.shape[2]),
                dtype=np.float64) * np.nan
            for i in range(bk_list.shape[0]):
                if i == 0:
                    tmp_data = data[0:bk_list[i], :, :]
                else:
                    tmp_data = data[bk_list[i - 1]:bk_list[i], :, :]

                # tmp_data[tmp_data == nan_hint] = np.nan
                new_data[i, :, :] = tmp_data[:, :, :].sum(0)
                # new_data[new_data == np.nan] = nan_hint
        else:
            new_data = np.ones(
                (bk_list.shape[0], data.shape[1], data.shape[2]),
                dtype=np.float64) * np.nan
            for i in range(bk_list.shape[0]):
                if i == 0:
                    tmp_data = data[0:bk_list[i], :, :]
                else:
                    tmp_data = data[bk_list[i - 1]:bk_list[i], :, :]

                # tmp_data[tmp_data == nan_hint] = np.nan
                new_data[i, :, :] = tmp_data[:, :, :].mean(0)

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        # idx = np.argwhere(new_data[0, :, :] != nan_hint)
        idx = np.argwhere(~ np.isnan(new_data[0, :, :]))
        if imd_obj.method == 'mmk_hr':
            for i2 in range(len(idx)):
                out_data[0, idx[i2, 0], idx[i2, 1]] = mmk_hr(
                    new_data[:, idx[i2, 0], idx[i2, 1]])
        elif imd_obj.method == 'sse':
            for i2 in range(len(idx)):
                out_data[0, idx[i2, 0], idx[i2, 1]] = sens_slope(
                    new_data[:, idx[i2, 0], idx[i2, 1]])
        elif imd_obj.method == 'spr':
            for i2 in range(len(idx)):
                out_data[0, idx[i2, 0], idx[i2, 1]] = spr(
                    new_data[:, idx[i2, 0], idx[i2, 1]])
        elif imd_obj.method == 'sstr':
            for i2 in range(len(idx)):
                out_data[0, idx[i2, 0], idx[i2, 1]] = sen_percerntage(
                    new_data[:, idx[i2, 0], idx[i2, 1]])
        else:
            raise Exception('{} method is not found for trend analysis'
                            .format(imd_obj.method))

        imd_obj.data = out_data
        imd_obj.time_step = out_data.shape[0]
        return imd_obj


def dtr_anu(tmx, **kwargs):
    """
    Function for annual mean Diurnal Temperature Range

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed annual dtr

    Examples
    --------
    Computes annual annual mean Diurnal Temperature Range

    >>> start_yr = 2010
    >>> end_yr = 2019
    >>> variable = 'tmax'
    >>> data1 = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> variable = 'tmin'
    >>> data2 = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data1.compute('dtr', 'A', tmin=data2)
    """

    if 'tmin' in kwargs:
        tmn = kwargs.get('tmin')
    else:
        raise Exception('tmin data not set/given')
    if tmx.cat == 'tmax' and tmn.cat == 'tmin':
        data1 = tmx.data
        nan_hint = data1[0, 0, 0]
        data1[data1 == nan_hint] = np.nan
        data2 = tmn.data
        data2[data2 == nan_hint] = np.nan
        data = data1 - data2
        bk_list = bk_point(tmx)
        bk_list.shape[0]
        new_data = np.ones(
            (bk_list.shape[0], data.shape[1], data.shape[2]),
            dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = data[0:bk_list[i], :, :]
            else:
                tmp_data = data[bk_list[i - 1]:bk_list[i], :, :]

            tmp_data[tmp_data == tmp_data[0, 0, 0]] = np.nan
            new_data[i, :, :] = tmp_data[:, :, :].mean(0)

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        tmx.data = new_data
        tmx.time_step = new_data.shape[0]
        return tmx
    else:
        raise Exception('Input data is not tmax and tmin')


def mxadt(imd_obj):
    """
    Function for maximum Annual Daily Tmax (Hottest Day)

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed Max Annual Daily Tmax

    Examples
    --------
    Computes max annual daily Tmax

    >>> start_yr = 2010
    >>> end_yr = 2019
    >>> variable = 'tmax'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('mxadt', 'A')
    """

    if not imd_obj.cat == 'tmax':
        raise Exception('Input data is not maximum temperature type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones(
            (bk_list.shape[0], imd_obj.data.shape[1], imd_obj.data.shape[2]),
            dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            tmp_data[tmp_data == nan_hint] = np.nan
            new_data[i, :, :] = tmp_data[:, :, :].max(0)

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def mnadt_anu(imd_obj):
    """
    Function for minimum Annual Daily Tmin (Coolest night)

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed Min Annual Daily Tmin

    Examples
    --------
    Computes min annual daily Tmin

    >>> start_yr = 2010
    >>> end_yr = 2019
    >>> variable = 'tmax'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('mnadt', 'A')
    """

    if not imd_obj.cat == 'tmin':
        raise Exception('Input data is not minimum temperature type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones(
            (bk_list.shape[0], imd_obj.data.shape[1], imd_obj.data.shape[2]),
            dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i - 1]:bk_list[i], :, :].copy()

            tmp_data[tmp_data == nan_hint] = np.nan
            new_data[i, :, :] = tmp_data[:, :, :].min(0)

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def rxa(imd_obj):
    """
    Function for Maximum annual rainfall

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed Max annual daily rainfall

    Examples
    --------
    Computes max annual daily rainfall

    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('rxa', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0], imd_obj.data.shape[1],
                           imd_obj.data.shape[2]), dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            tmp_data[tmp_data == nan_hint] = np.nan
            new_data[i, :, :] = tmp_data[:, :, :].max(0)

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def rx5d(imd_obj):
    """
    Function for Maximum Max 5 day rainfall in a year

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed Max 'Max 5 day rainfall in a year'

    Examples
    --------
    Computes max 5 day rainfall in a year

    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('rx5d', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        # nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            idx = np.argwhere(~ np.isnan(tmp_data[0, :, :]))
            for i2 in range(len(idx)):
                new_data[i, idx[i2, 0], idx[i2, 1]] = \
                np.max(np.convolve(tmp_data[:, idx[i2, 0], idx[i2, 1]],
                np.ones(5, dtype=float), 'valid'))

        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def dr(imd_obj, threshold=2.5):
    """
    Function for finding total Rainy days in a year

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed total Rainy days in a year

    Examples
    --------
    Computes total Rainy days in a year

    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('dr', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        # nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan
        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            idx = np.argwhere(~ np.isnan(tmp_data[0, :, :]))

            for i2 in range(len(idx)):
                new_data[i, idx[i2, 0], idx[i2, 1]] = \
                    sum(tmp_data[:, idx[i2, 0], idx[i2, 1]] >= threshold)

        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def cwd(imd_obj, threshold=2.5):
    """
    Function for finding Consecutive Wet Days in a year

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed Consecutive Wet Days in a year

    Examples
    --------
    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('cwd', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        # nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan

        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            idx = np.argwhere(~ np.isnan(tmp_data[0, :, :]))

            for i2 in range(len(idx)):
                new_data[i, idx[i2, 0], idx[i2, 1]] = \
                    np.max(np.diff(np.concatenate(([-1],) +
                           np.nonzero(np.diff(np.where(tmp_data[:, idx[i2, 0],
                                      idx[i2, 1]] >= threshold)[0]) != 1) +
                                    ([len(np.where(tmp_data[:, idx[i2, 0],
                                     idx[i2, 1]] >= threshold)[0])-1],))))

        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def d64(imd_obj, threshold=64.5):
    """
    Function for finding number of heavy rainfall days in a year

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with computed No. of heavy rainfall days in a year

    Examples
    --------
    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('d64', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        bk_list.shape[0]
        # nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan

        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            idx = np.argwhere(~ np.isnan(tmp_data[0, :, :]))

            for i2 in range(len(idx)):
                new_data[i, idx[i2, 0], idx[i2, 1]] = \
                    sum(tmp_data[:, idx[i2, 0], idx[i2, 1]] >= threshold)

        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def rtwd(imd_obj, threshold=2.5):
    """
    Function for finding Total precipitation in wet days in a year

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with Total precipitation in wet days in a year

    Examples
    --------
    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('rtwd', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        bk_list = bk_point(imd_obj)
        # nan_hint = imd_obj.data[0, 0, 0]
        new_data = np.ones((bk_list.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan

        for i in range(bk_list.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[bk_list[i-1]:bk_list[i], :, :].copy()

            idx = np.argwhere(~ np.isnan(tmp_data[0, :, :]))
            for i2 in range(len(idx)):
                new_data[i, idx[i2, 0], idx[i2, 1]] = \
                    sum(tmp_data[:, idx[i2, 0],
                        idx[i2, 1]][tmp_data[:, idx[i2, 0], idx[i2, 1]]
                        >= threshold])

        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


def sdii(imd_obj):
    """
    Function for finding Simple precipitation intensity index

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with Simple precipitation intensity index

    Examples
    --------
    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('sdii', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        const_data = imd_obj.data.copy()
        nume = rtwd(imd_obj)
        nume = nume.data
        nan_hint = nume[0, 0, 0]
        nume[nume == nan_hint] = np.nan
        imd_obj.data = const_data
        deno = dr(imd_obj)
        deno = deno.data
        deno[deno == nan_hint] = np.nan
        new_data = np.divide(nume, deno)
        # new_data[np.where(np.isnan(new_data))] = nan_hint
        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]
        return imd_obj


def pci(imd_obj):
    """
    Function for finding Precipitation concentration index

    Parameters
    ----------
    obj : IMD

    Returns
    -------
    IMD object
        Modified IMD object with Precipitation concentration index

    Examples
    --------
    >>> start_yr = 2015
    >>> end_yr = 2019
    >>> variable = 'rain'
    >>> data = imd.open_data(variable, start_yr, end_yr, 'yearwise')
    >>> data.compute('pci', 'A')
    """

    if not imd_obj.cat == 'rain':
        raise Exception('Input data is not rainfall type')
    else:
        nan_hint = imd_obj.data[0, 0, 0]
        bk_list_month = bk_point_month(imd_obj)

        new_data = np.ones((int(bk_list_month.shape[0]/12),
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan

        mon_data = np.ones((bk_list_month.shape[0],
                            imd_obj.data.shape[1],
                            imd_obj.data.shape[2]),
                           dtype=np.float64) * np.nan

        for i in range(bk_list_month.shape[0]):
            if i == 0:
                tmp_data = imd_obj.data[0:bk_list_month[i], :, :].copy()
            else:
                tmp_data = imd_obj.data[
                    bk_list_month[i-1]:bk_list_month[i], :, :].copy()

            tmp_data[tmp_data == nan_hint] = np.nan
            mon_data[i, :, :] = tmp_data[:, :, :].sum(0)

            if ((i+1) % 12 == 0 and i > 1):
                new_data[((i+1)//12)-1, :, :] = \
                    100*np.sum(np.square(mon_data[i-11:i+1, :, :]), 0) /\
                    np.square(mon_data[i-11:i+1, :, :].sum(0))

        # new_data[np.where(np.isnan(new_data))] = nan_hint
        imd_obj.data = new_data
        imd_obj.time_step = new_data.shape[0]

        return imd_obj


# Dictionary of functions to be called by the compute module


func_anu_dict = {
    "cwd": cwd,
    "dr": dr,
    "dtr": dtr_anu,
    "d64": d64,
    "mmk_hr": anu_trend,
    "mnadt": mnadt_anu,
    "mxadt": mxadt,
    "pci": pci,
    "rtwd": rtwd,
    "rx5d": rx5d,
    "rxa": rxa,
    "sdii": sdii,
    "spr": anu_trend,
    "sse": anu_trend,
    "sstr": anu_trend
    }
