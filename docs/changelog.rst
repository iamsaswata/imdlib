Changelog History
=================


Unreleased
----------

* Added ``load()``, which downloads IMD data into a local cache, checks each file and returns an IMD object. It supports archive and real-time data (``source='realtime'``), offline use, retries and download progress.

* Added ``imdlib.cache`` to show, clear, relocate and unlock the local data cache.

* Fixed ``get_data()`` and ``get_real_data()`` saving empty or incomplete files. An empty reply from IMD (period not published yet) now raises a clear error.

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
