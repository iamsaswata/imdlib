"""
Drought index computation for IMD gridded data.

Implements SPI (Standardized Precipitation Index) and SPEI (Standardized
Precipitation Evapotranspiration Index) following standard methodologies.

SPI: McKee et al. (1993), WMO-No. 1090 (2012)
     Gamma distribution with MLE (Thom 1958 approximation)

SPEI: Vicente-Serrano et al. (2010), Begueria et al. (2014)
      Log-logistic distribution with unbiased PWMs (Hosking 1990)

PET: Hargreaves-Samani (1985), FAO recommended when PM not possible.

References:
    - McKee et al. (1993), 8th Conf. Applied Climatology, AMS
    - WMO (2012), Standardized Precipitation Index User Guide, WMO-No. 1090
    - Vicente-Serrano et al. (2010), J. Climate, 23: 1696-1718
    - Begueria et al. (2014), Int J Climatol, 34: 3001-3023
    - Hargreaves & Samani (1985), Applied Eng. in Agriculture, 1(2): 96-99
"""

import numpy as np
from scipy.stats import gamma as gamma_dist
from scipy.stats import norm


# ====================================================================
# Gamma fitting for SPI (MLE, Thom 1958)
# ====================================================================

def _thom_gamma_fit(x):
    """
    Fit a two-parameter gamma distribution using Thom (1958) MLE.

    Parameters
    ----------
    x : 1D array of strictly positive values (zeros removed)

    Returns
    -------
    alpha, beta : float
        Shape and scale parameters. Returns (NaN, NaN) on failure.
    """
    if len(x) < 10:
        return np.nan, np.nan

    x_bar = np.mean(x)
    A = np.log(x_bar) - np.mean(np.log(x))

    if A <= 0:
        return np.nan, np.nan

    alpha = (1.0 / (4.0 * A)) * (1.0 + np.sqrt(1.0 + 4.0 * A / 3.0))
    beta = x_bar / alpha

    return alpha, beta


def _spi_transform(values, alpha, beta, q):
    """
    Transform precipitation values to SPI using mixed gamma-normal CDF.

    Parameters
    ----------
    values : 1D array of accumulated precipitation (may contain zeros)
    alpha, beta : gamma parameters (fitted on non-zero values)
    q : float, fraction of zero values

    Returns
    -------
    spi : 1D array of SPI values
    """
    spi = np.ones_like(values) * np.nan

    for k in range(len(values)):
        x = values[k]
        if np.isnan(x):
            continue
        if x == 0:
            H = q
        else:
            H = q + (1.0 - q) * gamma_dist.cdf(x, a=alpha, scale=beta)

        # Clamp to avoid ±infinity from norm.ppf
        H = np.clip(H, 0.001, 0.999)
        spi[k] = norm.ppf(H)

    return spi


# ====================================================================
# PWM and log-logistic fitting for SPEI (Hosking 1990, Begueria 2014)
# ====================================================================

def _pwm(x_sorted, n):
    """
    Compute first 3 unbiased probability weighted moments.

    Parameters
    ----------
    x_sorted : 1D array, sorted ascending
    n : int, length of array

    Returns
    -------
    w0, w1, w2 : float
    """
    w0 = np.mean(x_sorted)
    w1 = 0.0
    w2 = 0.0
    for i in range(n):
        w1 += (i / (n - 1)) * x_sorted[i]
        if n > 2:
            w2 += (i * (i - 1) / ((n - 1) * (n - 2))) * x_sorted[i]
    w1 /= n
    w2 /= n
    return w0, w1, w2


def _genlogistic_fit(x):
    """
    Fit a generalized logistic distribution using L-moments (Hosking 1997).

    Used for SPEI computation. Handles negative values (water balance).

    Parameters
    ----------
    x : 1D array of water balance values (can be negative)

    Returns
    -------
    xi, alpha, k : float
        Location, scale, shape. Returns (NaN, NaN, NaN) on failure.
    """
    n = len(x)
    if n < 10:
        return np.nan, np.nan, np.nan

    x_sorted = np.sort(x)
    w0, w1, w2 = _pwm(x_sorted, n)

    # L-moments from PWMs
    L1 = w0
    L2 = 2.0 * w1 - w0
    L3 = 6.0 * w2 - 6.0 * w1 + w0

    if abs(L2) < 1e-10:
        return np.nan, np.nan, np.nan

    t3 = L3 / L2  # L-skewness
    k = -t3

    if abs(k) >= 1.0:
        return np.nan, np.nan, np.nan

    if abs(k) < 1e-6:
        # k ≈ 0: standard logistic
        alpha = L2
        xi = L1
    else:
        alpha = L2 * np.sin(k * np.pi) / (k * np.pi)
        xi = L1 - alpha * (1.0 / k - np.pi / np.sin(k * np.pi))

    if alpha <= 0:
        return np.nan, np.nan, np.nan

    return xi, alpha, k


def _genlogistic_cdf(x, xi, alpha, k):
    """
    CDF of the generalized logistic distribution (Hosking 1997).

    F(x) = 1 / (1 + exp(-y))
    where y = -k^{-1} * ln(1 - k*(x-xi)/alpha)  if k != 0
              (x-xi)/alpha                         if k == 0
    """
    z = (x - xi) / alpha
    if abs(k) < 1e-6:
        y = z
    else:
        arg = 1.0 - k * z
        if arg <= 0:
            return 0.999 if k > 0 else 0.001
        y = -np.log(arg) / k
    return 1.0 / (1.0 + np.exp(-y))


def _spei_transform(values, xi, alpha, k):
    """
    Transform water balance values to SPEI using generalized logistic CDF.

    Parameters
    ----------
    values : 1D array of accumulated water balance (P - PET)
    xi, alpha, k : generalized logistic parameters

    Returns
    -------
    spei : 1D array of SPEI values
    """
    spei_out = np.ones_like(values) * np.nan

    for i in range(len(values)):
        x = values[i]
        if np.isnan(x):
            continue
        F = _genlogistic_cdf(x, xi, alpha, k)
        F = np.clip(F, 0.001, 0.999)
        spei_out[i] = norm.ppf(F)

    return spei_out


# ====================================================================
# Hargreaves-Samani PET
# ====================================================================

def _hargreaves_pet(tmax_monthly, tmin_monthly, lat_array):
    """
    Compute monthly PET using Hargreaves-Samani (1985) method.

    Parameters
    ----------
    tmax_monthly : 3D array, shape (N_months, lon, lat) — monthly mean Tmax (C)
    tmin_monthly : 3D array, shape (N_months, lon, lat) — monthly mean Tmin (C)
    lat_array : 1D array of latitudes (degrees)

    Returns
    -------
    pet : 3D array, shape (N_months, lon, lat) — monthly PET (mm/month)
    """
    n_months = tmax_monthly.shape[0]
    lon_size = tmax_monthly.shape[1]
    lat_size = tmax_monthly.shape[2]

    # Mid-month day of year and days per month (repeated for each year)
    mid_doy = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]
    days_per_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    pet = np.ones_like(tmax_monthly) * np.nan

    # Latitude in radians — broadcast over lon axis
    # lat_array has shape (lat_size,), need shape (lon_size, lat_size)
    lat_rad = np.deg2rad(lat_array)
    lat_rad_2d = np.broadcast_to(lat_rad, (lon_size, lat_size))

    for t in range(n_months):
        m = t % 12  # calendar month (0-based)
        J = mid_doy[m]
        n_days = days_per_month[m]

        # Solar geometry
        delta = 0.409 * np.sin(2.0 * np.pi * J / 365.0 - 1.39)
        dr = 1.0 + 0.033 * np.cos(2.0 * np.pi * J / 365.0)

        # Sunset hour angle
        ws_arg = -np.tan(lat_rad_2d) * np.tan(delta)
        ws = np.arccos(np.clip(ws_arg, -1.0, 1.0))

        # Extraterrestrial radiation (MJ/m²/day)
        Ra = (24.0 * 60.0 / np.pi) * 0.0820 * dr * (
            ws * np.sin(lat_rad_2d) * np.sin(delta) +
            np.cos(lat_rad_2d) * np.cos(delta) * np.sin(ws))
        Ra = np.maximum(0.0, Ra)

        # Hargreaves ETo (mm/day)
        Tmean = (tmax_monthly[t, :, :] + tmin_monthly[t, :, :]) / 2.0
        Td = np.maximum(0.0, tmax_monthly[t, :, :] - tmin_monthly[t, :, :])

        # 0.408 converts MJ/m²/day to mm/day (1/lambda, lambda=2.45)
        ETo = 0.0023 * Ra * (Tmean + 17.8) * np.sqrt(Td) * 0.408

        # Monthly PET
        pet[t, :, :] = ETo * n_days

    return pet


# ====================================================================
# SPI core
# ====================================================================

def spi(imd_obj, **kwargs):
    """
    Compute Standardized Precipitation Index (SPI).

    Uses gamma distribution with MLE (Thom 1958) per grid cell per
    calendar month. Handles zero-precipitation via mixed distribution.

    Parameters (via kwargs)
    ----------
    timescale : int, accumulation window in months (default: 3)
    cal_start : int, optional calibration period start year
    cal_end : int, optional calibration period end year

    Returns
    -------
    IMD object with SPI values, shape (N_months, lon, lat)
    """
    from datetime import datetime

    if imd_obj.cat not in ('rain', 'rain_gpm'):
        raise Exception('SPI requires rainfall data')

    timescale = kwargs.get('timescale', 3)
    cal_start = kwargs.get('cal_start', None)
    cal_end = kwargs.get('cal_end', None)

    # Monthly aggregation
    mon_data = imd_obj._monthly_aggregate()
    n_months = mon_data.shape[0]
    lon_size = mon_data.shape[1]
    lat_size = mon_data.shape[2]

    data_start_yr = datetime.strptime(imd_obj.start_day, '%Y-%m-%d').year
    data_end_yr = datetime.strptime(imd_obj.end_day, '%Y-%m-%d').year
    data_years = data_end_yr - data_start_yr + 1

    if data_years < 10:
        raise Exception('SPI requires at least 10 years of data')

    # Determine calibration period
    if cal_start is None:
        cal_start = data_start_yr
    if cal_end is None:
        cal_end = data_end_yr

    if cal_end - cal_start + 1 < 10:
        raise Exception('Calibration period must be at least 10 years')

    # Rolling accumulation
    accum = np.ones_like(mon_data) * np.nan
    for t in range(timescale - 1, n_months):
        window = mon_data[t - timescale + 1:t + 1, :, :]
        accum[t, :, :] = np.nansum(window, axis=0)
        all_nan = np.all(np.isnan(window), axis=0)
        accum[t, all_nan] = np.nan

    # Calibration indices (within monthly array)
    cal_start_idx = (cal_start - data_start_yr) * 12
    cal_end_idx = (cal_end - data_start_yr + 1) * 12

    # Result array
    spi_data = np.ones_like(accum) * np.nan

    # Fit per cell, per calendar month
    for i in range(lon_size):
        for j in range(lat_size):
            # Skip masked cells
            if imd_obj.land_mask is not None and not imd_obj.land_mask[i, j]:
                continue

            for m in range(12):
                # Collect calibration values for this calendar month
                cal_indices = np.arange(m, n_months, 12)
                cal_indices = cal_indices[
                    (cal_indices >= cal_start_idx) &
                    (cal_indices < cal_end_idx) &
                    (cal_indices >= timescale - 1)]
                cal_values = accum[cal_indices, i, j]
                cal_values = cal_values[~np.isnan(cal_values)]

                if len(cal_values) < 10:
                    continue

                # Zero handling
                nonzero = cal_values[cal_values > 0]
                q = 1.0 - len(nonzero) / len(cal_values)

                if len(nonzero) < 10:
                    continue

                alpha, beta = _thom_gamma_fit(nonzero)
                if np.isnan(alpha):
                    continue

                # Transform ALL months (not just calibration)
                all_indices = np.arange(m, n_months, 12)
                all_indices = all_indices[all_indices >= timescale - 1]
                all_values = accum[all_indices, i, j]

                spi_values = _spi_transform(all_values, alpha, beta, q)
                spi_data[all_indices, i, j] = spi_values

    imd_obj.data = spi_data
    imd_obj.computed = True
    imd_obj.scale = 'M'
    imd_obj.var_long_name = 'Standardized Precipitation Index (SPI-{})'.format(
        timescale)

    return imd_obj


# ====================================================================
# SPEI core
# ====================================================================

def spei(imd_obj, **kwargs):
    """
    Compute Standardized Precipitation Evapotranspiration Index (SPEI).

    Uses log-logistic distribution with unbiased PWMs per grid cell per
    calendar month. PET computed via Hargreaves-Samani from tmax/tmin.

    Parameters (via kwargs)
    ----------
    timescale : int, accumulation window in months (default: 3)
    tmax : IMD object with tmax data (required)
    tmin : IMD object with tmin data (required)
    cal_start : int, optional calibration period start year
    cal_end : int, optional calibration period end year

    Returns
    -------
    IMD object with SPEI values, shape (N_months, lon, lat)
    """
    from datetime import datetime
    from imdlib.core import IMD

    if imd_obj.cat not in ('rain', 'rain_gpm'):
        raise Exception('SPEI requires rainfall data as the primary input')

    tmax_obj = kwargs.get('tmax', None)
    tmin_obj = kwargs.get('tmin', None)
    if tmax_obj is None or tmin_obj is None:
        raise Exception('SPEI requires tmax and tmin data (pass as kwargs)')
    if tmax_obj.cat != 'tmax':
        raise Exception('tmax parameter must be tmax data')
    if tmin_obj.cat != 'tmin':
        raise Exception('tmin parameter must be tmin data')

    timescale = kwargs.get('timescale', 3)
    cal_start = kwargs.get('cal_start', None)
    cal_end = kwargs.get('cal_end', None)

    data_start_yr = datetime.strptime(imd_obj.start_day, '%Y-%m-%d').year
    data_end_yr = datetime.strptime(imd_obj.end_day, '%Y-%m-%d').year
    data_years = data_end_yr - data_start_yr + 1

    if data_years < 10:
        raise Exception('SPEI requires at least 10 years of data')

    if cal_start is None:
        cal_start = data_start_yr
    if cal_end is None:
        cal_end = data_end_yr

    if cal_end - cal_start + 1 < 10:
        raise Exception('Calibration period must be at least 10 years')

    # --- Monthly aggregation of precipitation ---
    mon_precip = imd_obj._monthly_aggregate()
    n_months = mon_precip.shape[0]

    # --- Monthly aggregation of temperature ---
    tmax_copy = tmax_obj.copy()
    tmin_copy = tmin_obj.copy()
    tmax_monthly = tmax_copy._monthly_aggregate()
    tmin_monthly = tmin_copy._monthly_aggregate()

    # --- Compute PET at 1° grid ---
    pet_100 = _hargreaves_pet(tmax_monthly, tmin_monthly, tmax_obj.lat_array)

    # --- Remap PET from 1° to 0.25° using imdlib remap ---
    pet_obj = IMD(pet_100, 'rain', imd_obj.start_day, imd_obj.end_day,
                  pet_100.shape[0], tmax_obj.lat_array, tmax_obj.lon_array)
    pet_obj.remap(0.25)
    pet_025_raw = pet_obj.data

    # --- Align PET to rainfall grid by coordinate matching ---
    # PET remap produces a grid based on temp lon/lat extents (67.5-97.5, 7.5-37.5)
    # Rain grid is wider (66.5-100.0, 6.5-38.5). Embed PET into rain-shaped array.
    rain_lon = imd_obj.lon_array
    rain_lat = imd_obj.lat_array
    pet_lon = pet_obj.lon_array
    pet_lat = pet_obj.lat_array

    pet_025 = np.ones_like(mon_precip) * np.nan
    # Find index offsets where PET grid starts in rain grid
    lon_offset = np.argmin(np.abs(rain_lon - pet_lon[0]))
    lat_offset = np.argmin(np.abs(rain_lat - pet_lat[0]))
    lon_end = lon_offset + len(pet_lon)
    lat_end = lat_offset + len(pet_lat)
    # Clip to rain grid bounds
    lon_end = min(lon_end, len(rain_lon))
    lat_end = min(lat_end, len(rain_lat))
    pet_ln = lon_end - lon_offset
    pet_lt = lat_end - lat_offset
    pet_025[:, lon_offset:lon_end, lat_offset:lat_end] = \
        pet_025_raw[:, :pet_ln, :pet_lt]

    lon_size = mon_precip.shape[1]
    lat_size = mon_precip.shape[2]

    # --- Water balance: D = P - PET ---
    water_balance = mon_precip - pet_025

    # --- Rolling accumulation ---
    accum = np.ones_like(water_balance) * np.nan
    for t in range(timescale - 1, n_months):
        window = water_balance[t - timescale + 1:t + 1, :, :]
        accum[t, :, :] = np.nansum(window, axis=0)
        all_nan = np.all(np.isnan(window), axis=0)
        accum[t, all_nan] = np.nan

    # Calibration indices
    cal_start_idx = (cal_start - data_start_yr) * 12
    cal_end_idx = (cal_end - data_start_yr + 1) * 12

    # Result array
    spei_data = np.ones((n_months, lon_size, lat_size),
                        dtype=np.float64) * np.nan

    # Fit per cell, per calendar month
    for i in range(lon_size):
        for j in range(lat_size):
            if imd_obj.land_mask is not None and not imd_obj.land_mask[i, j]:
                continue

            for m in range(12):
                cal_indices = np.arange(m, n_months, 12)
                cal_indices = cal_indices[
                    (cal_indices >= cal_start_idx) &
                    (cal_indices < cal_end_idx) &
                    (cal_indices >= timescale - 1)]
                cal_values = accum[cal_indices, i, j]
                cal_values = cal_values[~np.isnan(cal_values)]

                if len(cal_values) < 10:
                    continue

                xi, alpha, k = _genlogistic_fit(cal_values)
                if np.isnan(xi):
                    continue

                # Transform all months
                all_indices = np.arange(m, n_months, 12)
                all_indices = all_indices[all_indices >= timescale - 1]
                all_values = accum[all_indices, i, j]

                spei_values = _spei_transform(
                    all_values, xi, alpha, k)
                spei_data[all_indices, i, j] = spei_values

    if imd_obj.land_mask is not None:
        spei_data[:, ~imd_obj.land_mask] = np.nan

    imd_obj.data = spei_data
    imd_obj.computed = True
    imd_obj.scale = 'M'
    imd_obj.var_long_name = (
        'Standardized Precipitation Evapotranspiration Index (SPEI-{})'
        .format(timescale))

    return imd_obj
