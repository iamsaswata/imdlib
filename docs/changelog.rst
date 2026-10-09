Changelog History
=================


v0.3.1 (08 October 2026)
------------------------

* ``load()`` downloads several files at a time (``parallel=``, default 4), which is much faster.

* Real-time days missing at IMD between available days are NaN, with a warning, instead of an error.

* A download blocked by a web browser, or an empty reply for an older year, is no longer reported as data not published yet.


v0.3.0 (07 October 2026)
------------------------

**Breaking changes**

* ``clip()`` now returns the clipped data and no longer changes the original object: write ``data = data.clip(...)``.

* ``shape`` returns the size of the data, e.g. ``(366, 31, 31)``, instead of printing it. In a script, write ``print(data.shape)``.

**New**

* Added ``region()``: area-weighted means over named states, districts, river basins and sub-basins of India, or over the polygons of a shapefile, and the value at a city, as a ``pandas.DataFrame``. ``imdlib.regions.search()``, ``list()`` and ``info()`` find region names and show the sources.

* ``clip()`` accepts region names (``state=``, ``district=``, ``basin=``, ``subbasin=``) as well as a shapefile. Cells partly inside the region are kept with their fraction (``cell_fraction``). ``spatial_mean()`` uses these fractions, so it equals ``region()`` for the same region. ``get_xarray()`` and ``to_netcdf()`` include ``cell_fraction``.

* ``load()`` reads the files when the data is first used: it returns at once, and ``region()`` and ``clip()`` read only the cells they need.

**Fixes**

* Missing values are recognised by the IMD values (-999 for rain, 99.9 for temperature, none for GPM rain) instead of the value of the corner cell. This applies to ``spatial_mean()``, ``get_xarray()``, the land mask, the climate indices, ``climatology()``, ``anomaly()``, ``fill_na()``, ``remap()``, ``heatwave()`` and ``coldwave()``. Results for data that is not clipped are unchanged, except:

  * GPM rain: -999 is no longer left out;

  * real-time and computed data: valid zero values are no longer turned into missing values, e.g. ``remap()`` or ``fill_na()`` after ``compute('dr', 'A')``.

* ``fill_na()``:

  * finds the temperature cells it does not fill (the Andaman and Nicobar area and the row at 7.5°N) by their coordinates, so it is correct on the 0.5° real-time grid, where results change;

  * fills GPM rain everywhere;

  * no longer runs forever when a day has no values.

* ``to_geotiff()`` uses NaN as its nodata value, so dry days in clipped rain are kept. Very small clipped areas are now correctly placed on the map.

* ``compute('dtr', ...)`` and ``anomaly(climatology=...)`` raise an error if the two datasets are not on the same cells. SPEI raises an error for clipped ``tmax`` or ``tmin``.


v0.2.0 (06 October 2026)
------------------------

* Added ``load()``, which downloads IMD data into a local cache, checks each file and returns an IMD object. It supports archive and real-time data (``source='realtime'``, including ``rain_gpm``), offline use, retries and download progress.

* Added ``imdlib.cache`` to show, clear, relocate and unlock the local data cache.

* Fixed ``get_data()`` and ``get_real_data()`` saving empty or wrong-size files. An empty reply from IMD (period not published yet) now raises ``DataNotAvailableError``; an incomplete download raises ``DownloadError``.

* Faster reading of ``.grd`` files in ``open_data()`` and ``open_real_data()`` (results are unchanged).

* ``heatwave()`` and ``coldwave()`` now read a normal period outside the loaded data with ``load()``, so its files go into the cache instead of the current working directory.

v0.1.22 (21 September 2026)
---------------------------

* Added SPI and SPEI drought indices.

* Added heat wave and cold wave detection with daily classifications and annual day counts.

* Added Consecutive Dry Days (CDD) climate index.

* Added monthly climatology and anomaly computation.

* Added area-weighted spatial averaging using ``spatial_mean()``.

* Added support for date inputs and reading partial-year periods.

* Improved downloads by skipping existing files and corrected download-directory handling.

* Improved land masking for grid-cell calculations.

* Fixed variable metadata for computed outputs.

* Fixed ``compute()`` to restore metadata after a failed calculation.

* Updated API documentation, examples, references, and the paper citation.

* Fixed Read the Docs configuration and removed tracked build files.

* Updated package requirements to Python 3.10 or newer and pandas 2.2 or newer.

v0.1.21 (04 December 2025)
--------------------------

* Fixed latitude/longitude validation and test parameter ordering.

* Removed duplicate code and improved code quality.

* Added real-time data functions to the API documentation.

v0.1.20 (24 February 2024)
--------------------------

* Fixed clipping with multipolygon shapefiles.

* Fixed GeoTIFF conversion for climate indices.

* Corrected typos.

v0.1.17 (15 May 2023)
---------------------

* Added climate-index computation.

* Added missing-data filling, resolution changes, shapefile clipping, and copying of IMD objects.

v0.1.15 (06 January 2023)
--------------------------

* Added support for real-time daily IMD rainfall (0.25\ :sup:`o`\) and temperature data (0.5\ :sup:`o`\)

v0.1.14 (11 October 2022)
--------------------------

* Updated download url (https://imdpune.gov.in/Clim_Pred_LRF_New >> https://imdpune.gov.in/cmpg/Griddata)

* added module for standard version check (e.g. imdlib.__version__)

v0.1.11 (12 March 2021)
--------------------------

* Updated docs

* Fixed missing libraries for conda upload


v0.1.10 (27 Feburary 2021)
--------------------------

* Removed requests as dependency.

* Added API references.

* Improvement in the docs.

* Updated `to_csv` to save file with time information and header.


v0.1.9 (30 December 2020)
-------------------------

* Added CF-1.7 conventions for the NetCDF output.

* Added support for converting data into GeoTIFF format.

* Improvement in the return process the imd.get_data() functionality

* Updated link for the new https based IMD data portal 
