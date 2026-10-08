"""
Tests for reading (open_data), the land mask, indices, climatology, anomaly,
heat/cold waves, SPI and SPEI.

Synthetic data (test_load.year_bytes and small IMD objects); no network.
One test uses a real IMD file and is skipped unless IMDLIB_TEST_CACHE is set
(see test_regions_real.py).
"""
import os
import shutil

import numpy as np
import pandas as pd
import pytest

import imdlib as imd
from imdlib.core import IMD
from imdlib.extreme import _load_region_mask
from test_load import days_in, year_bytes
from test_load import isolated  # noqa: F401  (autouse: isolated cache, no network)
from test_regions import LAT, LON

# Synthetic rain files (test_load.grd_bytes): ocean cells (-999) at
# lon 0-4 x lat 0-4, and a boundary cell (10, 10) with zero rain every day
OCEAN = (slice(0, 5), slice(0, 5))
BOUNDARY = (10, 10)


###############################################################################
# Helpers
###############################################################################

def write_years(folder, var, years, content=year_bytes):
    """Yearwise files as open_data(..., 'yearwise', folder) reads them."""
    ext = '.grd' if var == 'rain' else '.GRD'
    path = folder / var
    path.mkdir(parents=True, exist_ok=True)
    for year in years:
        (path / '{}{}'.format(year, ext)).write_bytes(content(var, year))
    return str(folder)


def open_rain(tmp_path, start, end, years, content=year_bytes):
    folder = write_years(tmp_path / 'yearwise', 'rain', years, content)
    return imd.open_data('rain', start, end, 'yearwise', folder)


def with_monsoon(var, year):
    """Synthetic rain with 20 times more rain in July."""
    arr = np.frombuffer(year_bytes(var, year), '<f4').reshape(days_in(year), 129, 135).copy()
    doy = pd.date_range('{}-01-01'.format(year), periods=days_in(year))
    july = np.asarray(doy.month == 7)
    arr[july] = np.where(arr[july] > 0, arr[july] * 20, arr[july])
    return arr.tobytes()


def monthly_sums(data):
    """Monthly rain totals computed directly (missing value -999 left out)."""
    v = np.where(data.data == -999.0, np.nan, data.data)
    months = pd.date_range(data.start_day, periods=data.no_days).to_period('M')
    out = np.array([np.nansum(v[np.asarray(months == m)], axis=0) for m in months.unique()])
    out[:, ~data.land_mask] = np.nan
    return out


def daily_obj(cat, start_yr, end_yr, grid, seed=0, values=None):
    """Eager IMD object of whole years on (a part of) an IMD grid."""
    lon, lat = grid
    start, end = '{}-01-01'.format(start_yr), '{}-12-31'.format(end_yr)
    days = len(pd.date_range(start, end))
    rng = np.random.default_rng(seed)
    if values is None:
        data = rng.gamma(0.5, 5.0, (days, len(lon), len(lat)))
    else:
        data = values(rng, pd.date_range(start, end), (days, len(lon), len(lat)))
    return IMD(data, cat, start, end, days, np.array(lat), np.array(lon),
               np.ones((len(lon), len(lat)), bool))


T100 = (LON['t100'], LAT['t100'])
RAIN_BOX = (LON['r025'][20:25], LAT['r025'][20:25])      # inside the temperature grid


def temperature(cat, start_yr, end_yr, base, events=(), seed=0):
    """
    tmax or tmin on the 1.0 degree grid: ``base`` plus small noise, the
    temperature missing value (99.9) in the corner (3 x 3 cells), and
    ``events``: (year, lon index, lat index, change) added on 10-14 May.
    """
    def values(rng, dates, shape):
        v = base + 0.1 * rng.standard_normal(shape)
        for year, i, j, change in events:
            days = (dates.year == year) & (dates.month == 5) & (dates.day >= 10) & \
                (dates.day <= 14)
            v[np.asarray(days), i, j] += change
        v[:, :3, :3] = 99.9
        return v
    data = daily_obj(cat, start_yr, end_yr, T100, seed, values)
    data.land_mask = data.data[0] != 99.9
    return data


def event_days(start='1991-01-01'):
    """Indices of the event days of 2020 in data starting on ``start``."""
    return np.asarray((pd.date_range('2020-05-10', '2020-05-14') - pd.Timestamp(start)).days)


def cells_of_type(kind, n):
    """First ``n`` cells (lon, lat) of a terrain type of the heat/cold wave mask
    (0 plains, 1 hilly, 2 coastal), outside the 99.9 corner."""
    mask = _load_region_mask()
    cells = [(int(i), int(j)) for i, j in np.argwhere(mask == kind) if i >= 3 or j >= 3]
    assert len(cells) >= n
    return cells[:n]


###############################################################################
# Reading and the land mask
###############################################################################

def test_read(tmp_path):
    a = open_rain(tmp_path, 2018, 2018, [2018])
    assert a.data.shape == (365, 135, 129)
    raw = np.frombuffer(year_bytes('rain', 2018), '<f4').reshape(365, 129, 135)
    assert np.array_equal(a.data, raw.transpose(0, 2, 1))


def test_land_mask_shape_and_count(tmp_path):
    """land_mask should match spatial grid and exclude ocean + boundary cells."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    assert data.land_mask is not None
    assert data.land_mask.shape == (data.data.shape[1], data.data.shape[2])
    # Ocean cells (-999) should be False
    ocean = data.data[0, :, :] == -999.0
    assert ocean.sum() == 25 and not data.land_mask[ocean].any()
    # Boundary cells (zero rain all year) should also be False
    all_zero = (data.data == 0.0).all(axis=0) & ~ocean
    assert np.argwhere(all_zero).tolist() == [list(BOUNDARY)]
    assert not data.land_mask[BOUNDARY]
    assert data.land_mask.sum() == 135 * 129 - 25 - 1


def test_land_mask_sub_year(tmp_path):
    """Sub-year ranges should only mask -999, not zero-rain cells."""
    full = open_rain(tmp_path, 2018, 2018, [2018])
    sub = imd.open_data('rain', '2018-06-01', '2018-09-30', 'yearwise',
                        str(tmp_path / 'yearwise'))
    # Sub-year has more valid cells: the boundary cell is not masked
    assert sub.land_mask.sum() == full.land_mask.sum() + 1
    assert sub.land_mask[BOUNDARY] and not full.land_mask[BOUNDARY]
    # Both mask the -999 cells
    assert not sub.land_mask[OCEAN].any() and not full.land_mask[OCEAN].any()
    assert np.array_equal(sub.land_mask, full.data[0] != -999.0)


def test_land_mask_data_unchanged(tmp_path):
    """self.data should remain unchanged: raw -999 and 0.0 preserved."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    assert data.data[0, 0, 0] == -999.0
    assert (data.data[:, OCEAN[0], OCEAN[1]] == -999.0).all()
    assert (data.data[:, BOUNDARY[0], BOUNDARY[1]] == 0.0).all()


def test_compute_uses_land_mask(tmp_path):
    """Compute functions should produce NaN for masked cells."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    mask = data.land_mask.copy()
    result = data.compute('cwd', 'A')
    assert np.array_equal(~np.isnan(result.data[0]), mask)


def test_vectorized_compute_uses_land_mask(tmp_path):
    """Vectorized functions (rxa) should also respect land_mask."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    mask = data.land_mask.copy()
    rx1 = data.data.max(axis=0)
    result = data.compute('rxa', 'A')
    assert np.array_equal(~np.isnan(result.data[0]), mask)
    assert np.array_equal(result.data[0][mask], rx1[mask])


def test_copy_preserves_land_mask(tmp_path):
    """copy() should produce independent deep copy of land_mask."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    copied = data.copy()
    assert np.array_equal(data.land_mask, copied.land_mask)
    # Mutating copy should not affect original
    copied.land_mask[0, 0] = not copied.land_mask[0, 0]
    assert data.land_mask[0, 0] != copied.land_mask[0, 0]


def test_cdd(tmp_path):
    """CDD should return valid values in range [0, 365] for land cells only."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    mask = data.land_mask.copy()
    # A known dry spell: 40 days without rain (< 1 mm) in one cell
    data.data[100:140, 60, 60] = 0.0
    data.data[[99, 140], 60, 60] = 5.0
    result = data.compute('cdd', 'A')
    assert np.array_equal(~np.isnan(result.data[0]), mask)
    valid = result.data[~np.isnan(result.data)]
    assert valid.min() >= 0
    assert valid.max() < 366
    assert result.data[0, 60, 60] == 40


###############################################################################
# Climatology and anomaly
###############################################################################

def test_climatology_shape(tmp_path):
    """Climatology should produce shape (12, lon, lat) regardless of input years."""
    for years in ([2018], [2019, 2020]):
        data = open_rain(tmp_path / str(len(years)), years[0], years[-1], years)
        clim = data.climatology()
        assert clim.data.shape == (12, 135, 129)
        assert clim.computed is True
        assert clim.scale == 'climatology'
        # Valid cells should match land_mask for all 12 months
        for m in range(12):
            assert np.array_equal(~np.isnan(clim.data[m]), clim.land_mask)


def test_climatology_values(tmp_path):
    """Monthly totals averaged over the years; a July monsoon is seen."""
    data = open_rain(tmp_path, 2019, 2020, [2019, 2020], content=with_monsoon)
    sums = monthly_sums(data)
    clim = data.climatology()
    assert np.allclose(clim.data, (sums[:12] + sums[12:]) / 2, equal_nan=True, rtol=1e-12)
    jan_mean = np.nanmean(clim.data[0, :, :])
    jul_mean = np.nanmean(clim.data[6, :, :])
    assert jul_mean > jan_mean * 5


@pytest.fixture(scope='module')
def real_rain(tmp_path_factory):
    cache = os.environ.get('IMDLIB_TEST_CACHE')
    src = os.path.join(cache or '', 'archive', 'rain', '2025.grd')
    if not cache or not os.path.isfile(src):
        pytest.skip('IMDLIB_TEST_CACHE is not set')
    root = tmp_path_factory.mktemp('real')
    (root / 'archive' / 'rain').mkdir(parents=True)
    shutil.copyfile(src, root / 'archive' / 'rain' / '2025.grd')
    return imd.load('rain', 2025, cache_dir=root, offline=True)


@pytest.mark.slow
def test_climatology_values_real_data(real_rain):
    """July climatology should be much higher than January (monsoon), on real data."""
    data = real_rain.copy()
    # Real files have ocean (-999) and boundary cells (zero rain all year)
    assert (data.data[0] == -999.0).any()
    assert ((data.data == 0.0).all(axis=0) & data.land_mask).sum() == 0
    clim = data.climatology()
    assert np.array_equal(~np.isnan(clim.data[0]), clim.land_mask)
    jan_mean = np.nanmean(clim.data[0, :, :])
    jul_mean = np.nanmean(clim.data[6, :, :])
    assert jul_mean > jan_mean * 5  # monsoon July >> dry January


def test_anomaly_shape(tmp_path):
    """Anomaly should produce shape (N_months, lon, lat)."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    anom = data.anomaly()
    assert anom.data.shape == (12, 135, 129)  # 1 year = 12 months
    assert anom.computed is True
    assert anom.scale == 'anomaly'


def test_anomaly_self_is_zero(tmp_path):
    """Anomaly against own climatology should be all zeros (one year), or the
    departure from the mean of the two years."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    anom = data.anomaly()
    valid = anom.data[~np.isnan(anom.data)]
    assert len(valid) == 12 * data.land_mask.sum()
    assert np.allclose(valid, 0.0)
    two = open_rain(tmp_path / 'two', 2019, 2020, [2019, 2020])
    sums = monthly_sums(two)
    anom = two.anomaly()
    assert np.allclose(anom.data[:12], (sums[:12] - sums[12:]) / 2, equal_nan=True)


def test_anomaly_external_climatology(tmp_path):
    """Anomaly with external climatology should work."""
    write_years(tmp_path / 'yearwise', 'rain', [2000, 2018])
    folder = str(tmp_path / 'yearwise')
    ref = imd.open_data('rain', 2000, 2000, 'yearwise', folder)
    ref_sums = monthly_sums(ref)
    ref_clim = ref.climatology()
    data = imd.open_data('rain', 2018, 2018, 'yearwise', folder)
    sums = monthly_sums(data)
    anom = data.anomaly(ref_clim)
    assert anom.data.shape == (12, 135, 129)
    # Anomaly against a different year is not zero: the difference of the totals
    valid = anom.data[~np.isnan(anom.data)]
    assert not np.allclose(valid, 0.0)
    assert np.allclose(anom.data, sums - ref_sums[:12], equal_nan=True)


def test_climatology_computed_guard(tmp_path):
    """Climatology should raise on already-computed data."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    data.compute('rxa', 'A')
    with pytest.raises(Exception, match=r"Climatology requires daily \(non-computed\) data"):
        data.climatology()


def test_climatology_sub_year_guard(tmp_path):
    """Climatology should raise on sub-year data."""
    write_years(tmp_path / 'yearwise', 'rain', [2018])
    data = imd.open_data('rain', '2018-06-01', '2018-09-30', 'yearwise',
                         str(tmp_path / 'yearwise'))
    with pytest.raises(Exception, match="Climatology requires at least one full year of data"):
        data.climatology()


def test_bk_point_month_leap_year():
    """bk_point_month should handle leap years correctly."""
    from imdlib.compute import bk_point_month
    # 2000 is a leap year
    bk = bk_point_month(daily_obj('tmax', 2000, 2000, T100))
    assert bk[-1] == 366  # leap year total days
    assert bk[1] == 60    # Jan(31) + Feb(29) = 60
    bk = bk_point_month(daily_obj('tmax', 2019, 2020, T100))
    assert len(bk) == 24
    assert (bk[1], bk[11], bk[13], bk[-1]) == (59, 365, 365 + 60, 365 + 366)


###############################################################################
# Heat and cold waves
###############################################################################

def test_heatwave_daily():
    """Heatwave daily output should have same time dimension as input."""
    hw_cells = cells_of_type(0, 2) + cells_of_type(1, 2) + cells_of_type(2, 2)
    severe_cells = cells_of_type(0, 3)[2:] + cells_of_type(1, 3)[2:] + cells_of_type(2, 3)[2:]
    events = [(2020, i, j, 5.0) for i, j in hw_cells] + \
        [(2020, i, j, 8.0) for i, j in severe_cells]
    data = temperature('tmax', 1991, 2020, 37.0, events)
    hw = data.heatwave(output='daily')
    assert hw.data.shape == (10958, 31, 31)
    valid = hw.data[~np.isnan(hw.data)]
    # Values should only be 0, 1, or 2
    assert set(np.unique(valid)) == {0.0, 1.0, 2.0}
    # Exactly the days and cells of the events
    days = event_days()
    assert (np.sum(hw.data == 1), np.sum(hw.data == 2)) == (5 * len(hw_cells),
                                                           5 * len(severe_cells))
    for (i, j), cls in [(c, 1) for c in hw_cells] + [(c, 2) for c in severe_cells]:
        assert (hw.data[days, i, j] == cls).all()
    # Ocean of the terrain mask and missing cells: NaN
    assert np.isnan(hw.data[:, _load_region_mask() == -1]).all()
    assert np.isnan(hw.data[:, 0, 0]).all()


def test_heatwave_annual_counts():
    """Annual HW counts: total should equal hw + severe."""
    (a, b), (c, d) = cells_of_type(0, 2), cells_of_type(2, 2)
    events = [(2020, *a, 8.0), (2020, *b, 5.0), (2015, *c, 8.0), (2020, *d, 5.0)]
    data = temperature('tmax', 2011, 2020, 37.0, events)
    out = {}
    for count in ('total', 'hw', 'severe'):
        out[count] = data.copy().heatwave(output='annual', count=count,
                                          norm_start=2011, norm_end=2020).data
    total, hw_only, severe = out['total'], out['hw'], out['severe']
    assert total.shape == (10, 31, 31)
    # total = hw + severe (where not NaN)
    mask = ~np.isnan(total)
    assert np.allclose(total[mask], hw_only[mask] + severe[mask])
    assert np.array_equal(np.isnan(hw_only), ~mask) and np.array_equal(np.isnan(severe), ~mask)
    # The events: 2015 is row 4, 2020 row 9
    assert (hw_only[9][a], severe[9][a]) == (0, 5)
    assert (hw_only[9][b], severe[9][b]) == (5, 0)
    assert (hw_only[4][c], severe[4][c]) == (0, 5)
    assert (hw_only[9][d], severe[9][d]) == (5, 0)
    assert total.sum(where=mask) == 20


def test_coldwave_daily():
    """Coldwave daily output should have correct shape and values."""
    cw_cells = cells_of_type(0, 2) + cells_of_type(2, 2)
    severe_cells = cells_of_type(0, 3)[2:] + cells_of_type(2, 3)[2:]
    events = [(2020, i, j, -5.0) for i, j in cw_cells] + \
        [(2020, i, j, -8.0) for i, j in severe_cells]
    data = temperature('tmin', 1991, 2020, 12.0, events)
    cw = data.coldwave(output='daily')
    assert cw.data.shape == (10958, 31, 31)
    valid = cw.data[~np.isnan(cw.data)]
    assert set(np.unique(valid)) == {0.0, 1.0, 2.0}
    days = event_days()
    assert (np.sum(cw.data == 1), np.sum(cw.data == 2)) == (5 * len(cw_cells),
                                                           5 * len(severe_cells))
    for (i, j), cls in [(c, 1) for c in cw_cells] + [(c, 2) for c in severe_cells]:
        assert (cw.data[days, i, j] == cls).all()


def test_heatwave_wrong_variable():
    """Heatwave should raise on non-tmax data."""
    data = temperature('tmin', 1991, 2020, 12.0)
    with pytest.raises(Exception, match="Heat wave detection requires tmax data"):
        data.heatwave()


def test_heatwave_short_data_no_norm():
    """Heatwave should raise on short data without norm period."""
    data = temperature('tmax', 2015, 2020, 37.0)
    with pytest.raises(Exception, match=r"Data spans 6 years \(< 30\)\. Provide norm_start "
                                        r"and norm_end"):
        data.heatwave()


###############################################################################
# SPI and SPEI
###############################################################################

def rain_box(start_yr, end_yr, seed=0):
    """Daily rain on 5 x 5 cells of the 0.25 degree grid, two cells
    masked."""
    data = daily_obj('rain', start_yr, end_yr, RAIN_BOX, seed)
    data.land_mask[0, :2] = False
    return data


def test_spi_shape_and_stats():
    """SPI should produce monthly output close to N(0,1)."""
    data = rain_box(1991, 2020)
    spi = data.compute('spi', 'M', timescale=3)
    assert spi.data.shape == (360, 5, 5)
    assert np.isnan(spi.data[:, 0, :2]).all()
    valid = spi.data[~np.isnan(spi.data)]
    assert len(valid) == (360 - 2) * 23
    assert abs(valid.mean()) < 0.1
    assert abs(valid.std() - 1.0) < 0.1


def test_spi_first_months_nan():
    """First (timescale-1) months should be NaN."""
    data = rain_box(1991, 2020)
    spi = data.compute('spi', 'M', timescale=12)
    assert np.all(np.isnan(spi.data[:11, :, :]))
    assert np.isfinite(spi.data[11:, 1:, :]).all()


def test_spi_wrong_variable():
    """SPI should raise on non-rainfall data."""
    data = temperature('tmax', 1991, 2020, 37.0)
    with pytest.raises(Exception, match="SPI requires rainfall data"):
        data.compute('spi', 'M', timescale=3)


def test_spi_monthly_scale_protection(tmp_path):
    """Other indices should not work on monthly scale."""
    data = open_rain(tmp_path, 2018, 2018, [2018])
    with pytest.raises(Exception, match="dr method is not available for monthly scale"):
        data.compute('dr', 'M')


def test_spei_shape_and_stats():
    """SPEI should produce monthly output close to N(0,1)."""
    def seasonal(base):
        def values(rng, dates, shape):
            season = 4 * np.sin(2 * np.pi * (np.asarray(dates.dayofyear) - 100) / 365.25)
            return base + season[:, None, None] + rng.standard_normal(shape)
        return values
    rain = rain_box(1991, 2020)
    # Temperature on 1.0 degree cells around the rain cells (PET is remapped to them)
    box = (LON['t100'][2:8], LAT['t100'][2:8])
    tmax = daily_obj('tmax', 1991, 2020, box, 1, seasonal(33.0))
    tmin = daily_obj('tmin', 1991, 2020, box, 2, seasonal(21.0))
    spei = rain.compute('spei', 'M', timescale=3, tmax=tmax, tmin=tmin)
    assert spei.data.shape == (360, 5, 5)
    assert np.isnan(spei.data[:, 0, :2]).all()
    valid = spei.data[~np.isnan(spei.data)]
    assert len(valid) == (360 - 2) * 23
    assert abs(valid.mean()) < 0.1
    assert abs(valid.std() - 1.0) < 0.1
