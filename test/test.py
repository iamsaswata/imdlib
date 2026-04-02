import imdlib as imd
import numpy as np
import os
import unittest


def _data_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _has_data(*years):
    d = _data_dir()
    return all(os.path.isfile(os.path.join(d, 'rain', f'{y}.grd')) for y in years)


def _has_temp_data(var_type, *years):
    d = _data_dir()
    return all(os.path.isfile(os.path.join(d, var_type, f'{y}.GRD')) for y in years)


def test_read():
    if not _has_data(2018):
        return
    a = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    assert a.data.shape == (365, 135, 129)


def test_land_mask_shape_and_count():
    """land_mask should match spatial grid and exclude ocean + boundary cells."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    assert data.land_mask is not None
    assert data.land_mask.shape == (data.data.shape[1], data.data.shape[2])
    # Ocean cells (-999) should be False
    ocean_count = (data.data[0, :, :] == -999.0).sum()
    assert data.land_mask.sum() < (135 * 129 - ocean_count)
    # Boundary cells (zero rain all year) should also be False
    all_zero = (data.data == 0.0).all(axis=0) & (data.data[0, :, :] != -999.0)
    for idx in np.argwhere(all_zero):
        assert data.land_mask[idx[0], idx[1]] == False


def test_land_mask_sub_year():
    """Sub-year ranges should only mask -999, not zero-rain cells."""
    if not _has_data(2018):
        return
    full = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    sub = imd.open_data('rain', '2018-06-01', '2018-09-30', 'yearwise', _data_dir())
    # Sub-year should have MORE valid cells (boundary cells not masked)
    assert sub.land_mask.sum() > full.land_mask.sum()
    # Both should mask -999 cells
    ocean_full = (full.data[0, :, :] == -999.0).sum()
    ocean_sub = (sub.data[0, :, :] == -999.0).sum()
    assert ocean_full == ocean_sub


def test_land_mask_data_unchanged():
    """self.data should remain unchanged — raw -999 and 0.0 preserved."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    assert data.data[0, 0, 0] == -999.0


def test_compute_uses_land_mask():
    """Compute functions should produce NaN for masked cells."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    land_count = data.land_mask.sum()
    result = data.compute('cwd', 'A')
    valid = result.data[~np.isnan(result.data)]
    assert len(valid) == land_count


def test_vectorized_compute_uses_land_mask():
    """Vectorized functions (rxa) should also respect land_mask."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    land_count = data.land_mask.sum()
    result = data.compute('rxa', 'A')
    valid = result.data[~np.isnan(result.data)]
    assert len(valid) == land_count


def test_copy_preserves_land_mask():
    """copy() should produce independent deep copy of land_mask."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    copied = data.copy()
    assert np.array_equal(data.land_mask, copied.land_mask)
    # Mutating copy should not affect original
    copied.land_mask[0, 0] = not copied.land_mask[0, 0]
    assert data.land_mask[0, 0] != copied.land_mask[0, 0]


def test_cdd():
    """CDD should return valid values in range [0, 365] for land cells only."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    result = data.compute('cdd', 'A')
    valid = result.data[~np.isnan(result.data)]
    assert len(valid) == data.land_mask.sum()
    assert valid.min() >= 0
    assert valid.max() < 366


def test_climatology_shape():
    """Climatology should produce shape (12, lon, lat) regardless of input years."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    clim = data.climatology()
    assert clim.data.shape == (12, 135, 129)
    assert clim.computed == True
    assert clim.scale == 'climatology'
    # Valid cells should match land_mask for all 12 months
    for m in range(12):
        valid = np.sum(~np.isnan(clim.data[m, :, :]))
        assert valid == clim.land_mask.sum()


def test_climatology_values():
    """July climatology should be much higher than January (monsoon)."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    clim = data.climatology()
    jan_mean = np.nanmean(clim.data[0, :, :])
    jul_mean = np.nanmean(clim.data[6, :, :])
    assert jul_mean > jan_mean * 5  # monsoon July >> dry January


def test_anomaly_shape():
    """Anomaly should produce shape (N_months, lon, lat)."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    anom = data.anomaly()
    assert anom.data.shape == (12, 135, 129)  # 1 year = 12 months
    assert anom.computed == True
    assert anom.scale == 'anomaly'


def test_anomaly_self_is_zero():
    """Anomaly against own climatology should be all zeros."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    anom = data.anomaly()
    valid = anom.data[~np.isnan(anom.data)]
    assert np.allclose(valid, 0.0)


def test_anomaly_external_climatology():
    """Anomaly with external climatology should work."""
    if not _has_data(2018, 2000):
        return
    ref = imd.open_data('rain', 2000, 2000, 'yearwise', _data_dir())
    ref_clim = ref.climatology()
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    anom = data.anomaly(ref_clim)
    assert anom.data.shape == (12, 135, 129)
    # Anomaly against different year should NOT be all zeros
    valid = anom.data[~np.isnan(anom.data)]
    assert not np.allclose(valid, 0.0)


def test_climatology_computed_guard():
    """Climatology should raise on already-computed data."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    data.compute('dr', 'A')
    try:
        data.climatology()
        assert False, 'Should have raised'
    except Exception:
        pass


def test_climatology_sub_year_guard():
    """Climatology should raise on sub-year data."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', '2018-06-01', '2018-09-30', 'yearwise', _data_dir())
    try:
        data.climatology()
        assert False, 'Should have raised'
    except Exception:
        pass


def test_bk_point_month_leap_year():
    """bk_point_month should handle leap years correctly."""
    if not _has_data(2000):
        return
    from imdlib.compute import bk_point_month
    # 2000 is a leap year
    data = imd.open_data('rain', 2000, 2000, 'yearwise', _data_dir())
    bk = bk_point_month(data)
    assert bk[-1] == 366  # leap year total days
    assert bk[1] == 60    # Jan(31) + Feb(29) = 60


def test_heatwave_daily():
    """Heatwave daily output should have same time dimension as input."""
    if not _has_temp_data('tmax', *range(1991, 2021)):
        return
    data = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    hw = data.heatwave(output='daily')
    assert hw.data.shape == (10958, 31, 31)
    valid = hw.data[~np.isnan(hw.data)]
    # Values should only be 0, 1, or 2
    assert set(np.unique(valid)).issubset({0.0, 1.0, 2.0})


def test_heatwave_annual_counts():
    """Annual HW counts: total should equal hw + severe."""
    if not _has_temp_data('tmax', *range(1991, 2021)):
        return
    d1 = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    total = d1.heatwave(output='annual', count='total')
    d2 = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    hw_only = d2.heatwave(output='annual', count='hw')
    d3 = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    severe = d3.heatwave(output='annual', count='severe')
    # total = hw + severe (where not NaN)
    mask = ~np.isnan(total.data)
    assert np.allclose(total.data[mask], hw_only.data[mask] + severe.data[mask])


def test_coldwave_daily():
    """Coldwave daily output should have correct shape and values."""
    if not _has_temp_data('tmin', *range(1991, 2021)):
        return
    data = imd.open_data('tmin', 1991, 2020, 'yearwise', _data_dir())
    cw = data.coldwave(output='daily')
    assert cw.data.shape == (10958, 31, 31)
    valid = cw.data[~np.isnan(cw.data)]
    assert set(np.unique(valid)).issubset({0.0, 1.0, 2.0})


def test_heatwave_wrong_variable():
    """Heatwave should raise on non-tmax data."""
    if not _has_temp_data('tmin', *range(1991, 2021)):
        return
    data = imd.open_data('tmin', 1991, 2020, 'yearwise', _data_dir())
    try:
        data.heatwave()
        assert False, 'Should have raised'
    except Exception:
        pass


def test_heatwave_short_data_no_norm():
    """Heatwave should raise on short data without norm period."""
    if not _has_temp_data('tmax', *range(2015, 2021)):
        return
    data = imd.open_data('tmax', 2015, 2020, 'yearwise', _data_dir())
    try:
        data.heatwave()
        assert False, 'Should have raised'
    except Exception:
        pass


def test_spi_shape_and_stats():
    """SPI should produce monthly output close to N(0,1)."""
    if not _has_data(*range(1991, 2021)):
        return
    data = imd.open_data('rain', 1991, 2020, 'yearwise', _data_dir())
    spi = data.compute('spi', 'M', timescale=3)
    assert spi.data.shape == (360, 135, 129)
    valid = spi.data[~np.isnan(spi.data)]
    assert len(valid) > 0
    assert abs(valid.mean()) < 0.1
    assert abs(valid.std() - 1.0) < 0.1


def test_spi_first_months_nan():
    """First (timescale-1) months should be NaN."""
    if not _has_data(*range(1991, 2021)):
        return
    data = imd.open_data('rain', 1991, 2020, 'yearwise', _data_dir())
    spi = data.compute('spi', 'M', timescale=12)
    assert np.all(np.isnan(spi.data[:11, :, :]))


def test_spi_wrong_variable():
    """SPI should raise on non-rainfall data."""
    if not _has_temp_data('tmax', *range(1991, 2021)):
        return
    data = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    try:
        data.compute('spi', 'M', timescale=3)
        assert False, 'Should have raised'
    except Exception:
        pass


def test_spi_monthly_scale_protection():
    """Other indices should not work on monthly scale."""
    if not _has_data(2018):
        return
    data = imd.open_data('rain', 2018, 2018, 'yearwise', _data_dir())
    try:
        data.compute('dr', 'M')
        assert False, 'Should have raised'
    except Exception:
        pass


def test_spei_shape_and_stats():
    """SPEI should produce monthly output close to N(0,1)."""
    if not (_has_data(*range(1991, 2021)) and
            _has_temp_data('tmax', *range(1991, 2021)) and
            _has_temp_data('tmin', *range(1991, 2021))):
        return
    rain = imd.open_data('rain', 1991, 2020, 'yearwise', _data_dir())
    tmax = imd.open_data('tmax', 1991, 2020, 'yearwise', _data_dir())
    tmin = imd.open_data('tmin', 1991, 2020, 'yearwise', _data_dir())
    spei = rain.compute('spei', 'M', timescale=3, tmax=tmax, tmin=tmin)
    assert spei.data.shape == (360, 135, 129)
    valid = spei.data[~np.isnan(spei.data)]
    assert len(valid) > 0
    assert abs(valid.mean()) < 0.1
    assert abs(valid.std() - 1.0) < 0.1
