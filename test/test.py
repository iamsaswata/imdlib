import imdlib as imd
import numpy as np
import os
import unittest


def _data_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _has_data(*years):
    d = _data_dir()
    return all(os.path.isfile(os.path.join(d, 'rain', f'{y}.grd')) for y in years)


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
