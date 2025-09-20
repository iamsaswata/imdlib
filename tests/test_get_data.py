import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import imdlib
from imdlib.core import IMDData


def _write_grd(path, values):
    np.asarray(values, dtype=np.float32).tofile(path)


def test_get_data_rain_gpm_realtime(tmp_path):
    first_day = tmp_path / "01012020.grd"
    second_day = tmp_path / "02012020.grd"

    _write_grd(first_day, np.arange(6, dtype=np.float32))
    _write_grd(second_day, np.arange(6, dtype=np.float32) + 10)

    data = imdlib.get_data(
        var="rain_gpm",
        start_date=(2020, 1, 1),
        end_date=(2020, 1, 2),
        fn_format="grd",
        download_dir=str(tmp_path),
        frequency="realtime",
        grid_shape=(2, 3),
        lat_bounds=(10.0, 11.0),
        lon_bounds=(70.0, 72.0),
    )

    assert isinstance(data, IMDData)
    xr_data = data.to_xarray()

    assert dict(xr_data.sizes) == {"time": 2, "lat": 2, "lon": 3}
    np.testing.assert_allclose(xr_data.lat.values, [10.0, 11.0])
    np.testing.assert_allclose(xr_data.lon.values, [70.0, 71.0, 72.0])

    expected = np.stack(
        [
            np.arange(6, dtype=np.float32).reshape(2, 3),
            (np.arange(6, dtype=np.float32) + 10).reshape(2, 3),
        ],
        axis=0,
    )
    np.testing.assert_allclose(xr_data["rain_gpm"].values, expected)

    assert pd.Timestamp("2020-01-01") == data.start_date
    assert pd.Timestamp("2020-01-02") == data.end_date
    assert xr_data["rain_gpm"].attrs["units"] == "mm/day"


def test_get_data_rain_gpm_monthly(tmp_path):
    june_path = tmp_path / "Rainfall25Merged_202406.nc"
    july_path = tmp_path / "Rainfall25Merged_202407.nc"

    base_coords = {"lat": [6.5, 6.75], "lon": [66.5, 66.75]}
    june = xr.Dataset(
        {"rain": (("lat", "lon"), np.full((2, 2), 5.0, dtype=np.float32))},
        coords=base_coords,
    )
    july = xr.Dataset(
        {"rain": (("lat", "lon"), np.full((2, 2), 10.0, dtype=np.float32))},
        coords=base_coords,
    )

    june.to_netcdf(june_path)
    july.to_netcdf(july_path)

    data = imdlib.get_data(
        var="rain_gpm",
        start_date=(2024, 6, 1),
        end_date=(2024, 7, 31),
        fn_format="nc",
        download_dir=str(tmp_path),
        frequency="monthly",
    )

    assert isinstance(data, IMDData)
    xr_data = data.to_xarray()

    assert xr_data.sizes["time"] == 2
    np.testing.assert_allclose(xr_data.lat.values, base_coords["lat"])
    np.testing.assert_allclose(xr_data.lon.values, base_coords["lon"])
    np.testing.assert_allclose(
        xr_data["rain_gpm"].sel(time="2024-06-01").values,
        np.full((2, 2), 5.0, dtype=np.float32),
    )
    np.testing.assert_allclose(
        xr_data["rain_gpm"].sel(time="2024-07-01").values,
        np.full((2, 2), 10.0, dtype=np.float32),
    )

    assert data.start_date == pd.Timestamp("2024-06-01")
    assert data.end_date == pd.Timestamp("2024-07-01")
    assert xr_data["rain_gpm"].attrs["units"] == "mm"
