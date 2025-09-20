# Using IMD + GPM Gauge-Merged Rainfall Data

`imdlib.get_data` now supports the `rain_gpm` variable which downloads the IMD + GPM gauge-merged rainfall grids. Depending on the `fn_format` and `frequency` parameters, the helper retrieves either the near real-time binary grids (`.grd`) or the monthly NetCDF composites (`.nc`).

## Daily real-time rainfall (binary `.grd`)

```python
import imdlib as imd

rain = imd.get_data(
    var="rain_gpm",
    start_date=(2024, 9, 1),
    end_date=(2024, 9, 5),
    fn_format="grd",
    frequency="realtime",
    download_dir="./gpm-data"
)

ds = rain.to_xarray()
print(ds)
```

The returned object is an `IMDData` wrapper around an `xarray.Dataset`. Daily grids use millimetres per day (`mm/day`) as the default unit.

## Monthly merged rainfall (NetCDF `.nc`)

```python
import imdlib as imd

merged = imd.get_data(
    var="rain_gpm",
    start_date=(2024, 6, 1),
    end_date=(2024, 8, 31),
    fn_format="nc",
    frequency="monthly",
    download_dir="./gpm-data"
)

monthly_ds = merged.to_xarray()
print(monthly_ds)
```

Monthly files are concatenated in time and returned as a single dataset with millimetres (`mm`) as the unit. Existing arguments for the legacy IMD rainfall, Tmin and Tmax downloads remain unchanged.
