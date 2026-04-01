"""
Extreme weather event detection for IMD gridded data.

Implements heat wave and cold wave detection following IMD's official
two-gate classification system with terrain-specific thresholds.

Region classification:
    -1 = Ocean / no data
     0 = Plains
     1 = Hilly
     2 = Coastal

References:
    - IMD Heat/Cold Wave FAQ: https://mausam.imd.gov.in/pdfs/heatcolduser/faq.pdf
    - Mandal et al. (2022), Sci. Reports, doi:10.1038/s41598-022-24065-0
"""

import numpy as np
import os

# Path to bundled region mask
_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def _load_region_mask():
    """Load the 1.0 degree region mask for temperature grid."""
    path = os.path.join(_DATA_DIR, 'region_mask_100.npy')
    return np.load(path)


def _daily_climatology(data, start_day, no_days, norm_start, norm_end, window=15):
    """
    Compute smoothed daily climatological normals from daily data.

    Groups by day-of-year, computes mean across years within the
    reference period, then applies a centered running mean for smoothing.

    Parameters
    ----------
    data : numpy 3D array, shape (no_days, lon, lat)
    start_day : str, 'YYYY-MM-DD'
    no_days : int
    norm_start : int, start year of normal period
    norm_end : int, end year of normal period
    window : int, running mean window size (days)

    Returns
    -------
    normals : numpy 3D array, shape (366, lon, lat)
        Daily climatological normal for each DOY (1-indexed: index 0 = DOY 1)
    """
    import pandas as pd

    dates = pd.date_range(start_day, periods=no_days)
    doy = dates.dayofyear.values  # 1-based
    years = dates.year.values

    # Select only the reference period
    ref_mask = (years >= norm_start) & (years <= norm_end)

    lon_size = data.shape[1]
    lat_size = data.shape[2]

    # Group by DOY and compute mean
    normals = np.ones((366, lon_size, lat_size), dtype=np.float64) * np.nan

    for d in range(1, 367):
        day_mask = ref_mask & (doy == d)
        if day_mask.sum() == 0:
            continue
        normals[d - 1, :, :] = np.nanmean(data[day_mask, :, :], axis=0)

    # Fill DOY 366 from neighbors if no leap years in ref period
    if np.all(np.isnan(normals[365, :, :])):
        normals[365, :, :] = (normals[364, :, :] + normals[0, :, :]) / 2.0

    # Smooth with centered running mean (circular for wrap-around Dec-Jan)
    if window > 1:
        half = window // 2
        smoothed = np.copy(normals)
        # Pad circularly
        padded = np.concatenate([normals[-half:], normals, normals[:half]], axis=0)
        for d in range(366):
            smoothed[d, :, :] = np.nanmean(
                padded[d:d + window, :, :], axis=0)
        normals = smoothed

    return normals


def _detect_events(imd_obj, event_type, output, count, norm_start, norm_end):
    """
    Core detection logic for heat waves and cold waves.

    Parameters
    ----------
    imd_obj : IMD object
    event_type : str, 'heatwave' or 'coldwave'
    output : str, 'daily' or 'annual'
    count : str, 'total', 'hw'/'cw', or 'severe'
    norm_start : int or None
    norm_end : int or None

    Returns
    -------
    IMD object (modified in-place)
    """
    import pandas as pd
    from datetime import datetime

    # --- Input validation ---
    if event_type == 'heatwave' and imd_obj.cat != 'tmax':
        raise Exception('Heat wave detection requires tmax data')
    if event_type == 'coldwave' and imd_obj.cat != 'tmin':
        raise Exception('Cold wave detection requires tmin data')

    if imd_obj.computed:
        raise Exception('Heat/cold wave detection requires daily (non-computed) data')

    valid_counts = ('total', 'hw', 'cw', 'severe')
    if count not in valid_counts:
        raise Exception("count must be one of {}".format(valid_counts))

    # --- Determine normal period ---
    data_start_yr = datetime.strptime(imd_obj.start_day, '%Y-%m-%d').year
    data_end_yr = datetime.strptime(imd_obj.end_day, '%Y-%m-%d').year
    data_years = data_end_yr - data_start_yr + 1

    if norm_start is not None and norm_end is not None:
        if norm_end - norm_start + 1 < 10:
            raise Exception('Normal period must be at least 10 years')
    elif data_years >= 30:
        norm_start = data_start_yr
        norm_end = data_end_yr
    else:
        raise Exception(
            'Data spans {} years (< 30). Provide norm_start and norm_end '
            'for the reference period (minimum 10 years)'.format(data_years))

    # --- Load region mask ---
    region_mask = _load_region_mask()

    # --- Prepare data: replace sentinel with NaN ---
    nan_hint = imd_obj.data[0, 0, 0]
    work_data = imd_obj.data.copy()
    work_data[work_data == nan_hint] = np.nan

    # --- Compute daily normals ---
    # If norm period is within loaded data, use it directly.
    # Otherwise, load the norm period data separately.
    norm_within_data = (norm_start >= data_start_yr and norm_end <= data_end_yr)

    if norm_within_data:
        norm_data = work_data
        norm_start_day = imd_obj.start_day
        norm_no_days = imd_obj.no_days
    else:
        from imdlib.core import get_data
        norm_obj = get_data(imd_obj.cat, norm_start, norm_end,
                            fn_format='yearwise')
        norm_data = norm_obj.data.copy()
        norm_data[norm_data == norm_obj.data[0, 0, 0]] = np.nan
        norm_start_day = norm_obj.start_day
        norm_no_days = norm_obj.no_days

    normals = _daily_climatology(
        norm_data, norm_start_day, norm_no_days, norm_start, norm_end)

    # --- Classify each day x cell ---
    dates = pd.date_range(imd_obj.start_day, periods=imd_obj.no_days)
    doy = dates.dayofyear.values

    n_days = imd_obj.no_days
    lon_size = imd_obj.data.shape[1]
    lat_size = imd_obj.data.shape[2]

    # Result: 0=none, 1=event, 2=severe
    result = np.zeros((n_days, lon_size, lat_size), dtype=np.float64)

    # Pre-compute region masks for vectorized operations
    plains = (region_mask == 0)
    hilly = (region_mask == 1)
    coastal = (region_mask == 2)
    ocean = (region_mask == -1)

    if event_type == 'heatwave':
        # Gate 1 thresholds
        g1_plains = 40.0
        g1_hilly = 30.0
        g1_coastal = 37.0
        # Path A thresholds (departure)
        pa_event = 4.5
        pa_severe = 6.4
        # Path B thresholds (absolute) — applies to plains AND hilly
        pb_event = 45.0
        pb_severe = 47.0

        for t in range(n_days):
            temp = work_data[t, :, :]
            normal = normals[doy[t] - 1, :, :]
            departure = temp - normal

            # Gate 1
            g1_pass = np.zeros((lon_size, lat_size), dtype=bool)
            g1_pass[plains] = temp[plains] >= g1_plains
            g1_pass[hilly] = temp[hilly] >= g1_hilly
            g1_pass[coastal] = temp[coastal] >= g1_coastal

            # Path A: departure-based (all regions)
            pa_hw = g1_pass & (departure >= pa_event) & (departure <= pa_severe)
            pa_severe_mask = g1_pass & (departure > pa_severe)

            # Path B: absolute threshold (plains + hilly only, not coastal)
            pb_applicable = plains | hilly
            pb_hw = g1_pass & pb_applicable & (temp >= pb_event) & (temp < pb_severe)
            pb_severe_mask = g1_pass & pb_applicable & (temp >= pb_severe)

            # Combine: worst category wins
            is_hw = pa_hw | pb_hw
            is_severe = pa_severe_mask | pb_severe_mask

            result[t, is_severe] = 2.0
            result[t, is_hw & ~is_severe] = 1.0

    else:  # coldwave
        # Gate 1 thresholds
        g1_plains = 10.0
        g1_hilly = 0.0
        g1_coastal = 15.0
        # Path A thresholds (departure)
        pa_event = -4.5
        pa_severe = -6.4
        # Path B thresholds (absolute) — plains ONLY
        pb_event = 4.0
        pb_severe = 2.0

        for t in range(n_days):
            temp = work_data[t, :, :]
            normal = normals[doy[t] - 1, :, :]
            departure = temp - normal

            # Gate 1
            g1_pass = np.zeros((lon_size, lat_size), dtype=bool)
            g1_pass[plains] = temp[plains] <= g1_plains
            g1_pass[hilly] = temp[hilly] <= g1_hilly
            g1_pass[coastal] = temp[coastal] <= g1_coastal

            # Path A: departure-based (all regions)
            pa_cw = g1_pass & (departure <= pa_event) & (departure > pa_severe)
            pa_severe_mask = g1_pass & (departure <= pa_severe)

            # Path B: absolute threshold (plains ONLY)
            pb_cw = g1_pass & plains & (temp <= pb_event) & (temp > pb_severe)
            pb_severe_mask = g1_pass & plains & (temp <= pb_severe)

            # Combine: worst category wins
            is_cw = pa_cw | pb_cw
            is_severe = pa_severe_mask | pb_severe_mask

            result[t, is_severe] = 2.0
            result[t, is_cw & ~is_severe] = 1.0

    # Mask ocean and land_mask
    result[:, ocean] = np.nan
    if imd_obj.land_mask is not None:
        result[:, ~imd_obj.land_mask] = np.nan

    # --- Output ---
    if output == 'daily':
        imd_obj.data = result
        imd_obj.computed = True
        imd_obj.scale = 'daily'
        return imd_obj

    elif output == 'annual':
        from imdlib.compute import bk_point
        bk_list = bk_point(imd_obj)
        n_years = bk_list.shape[0]

        annual = np.ones((n_years, lon_size, lat_size),
                         dtype=np.float64) * np.nan

        for i in range(n_years):
            if i == 0:
                year_data = result[0:bk_list[i], :, :]
            else:
                year_data = result[bk_list[i-1]:bk_list[i], :, :]

            if count == 'total':
                annual[i, :, :] = np.nansum(year_data >= 1, axis=0).astype(np.float64)
            elif count in ('hw', 'cw'):
                annual[i, :, :] = np.nansum(year_data == 1, axis=0).astype(np.float64)
            elif count == 'severe':
                annual[i, :, :] = np.nansum(year_data == 2, axis=0).astype(np.float64)

        # Re-apply masks to annual output
        annual[:, ocean] = np.nan
        if imd_obj.land_mask is not None:
            annual[:, ~imd_obj.land_mask] = np.nan

        imd_obj.data = annual
        imd_obj.computed = True
        imd_obj.scale = 'A'
        return imd_obj

    else:
        raise Exception("output must be 'daily' or 'annual'")
