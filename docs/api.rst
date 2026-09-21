API reference
=============

Download and read data
----------------------

.. autosummary::
   :toctree: reference

   imdlib.get_data
   imdlib.open_data
   imdlib.get_real_data
   imdlib.open_real_data

IMD data object
---------------

.. autosummary::
   :toctree: reference
   :template: autosummary/imd-class.rst

   imdlib.IMD

Convert and export
------------------

.. autosummary::
   :toctree: reference

   imdlib.IMD.get_xarray
   imdlib.IMD.to_csv
   imdlib.IMD.to_netcdf
   imdlib.IMD.to_geotiff

Process gridded data
--------------------

.. autosummary::
   :toctree: reference

   imdlib.IMD.copy
   imdlib.IMD.clip
   imdlib.IMD.remap
   imdlib.IMD.fill_na
   imdlib.IMD.spatial_mean
   imdlib.IMD.climatology
   imdlib.IMD.anomaly

Climate indices
---------------

.. autosummary::
   :toctree: reference

   imdlib.IMD.compute
   imdlib.compute.cdd
   imdlib.compute.cwd
   imdlib.compute.d64
   imdlib.compute.dr
   imdlib.compute.dtr_anu
   imdlib.compute.mnadt_anu
   imdlib.compute.mxadt
   imdlib.compute.pci
   imdlib.compute.rtwd
   imdlib.compute.rx5d
   imdlib.compute.rxa
   imdlib.compute.sdii

Drought indices
---------------

.. autosummary::
   :toctree: reference

   imdlib.drought.spi
   imdlib.drought.spei

Extreme temperature events
--------------------------

.. autosummary::
   :toctree: reference

   imdlib.IMD.heatwave
   imdlib.IMD.coldwave

Trend analysis
--------------

.. autosummary::
   :toctree: reference

   imdlib.compute.anu_trend
   imdlib.compute.mmk_hr
   imdlib.compute.sen_percerntage
   imdlib.compute.sens_slope
   imdlib.compute.spr

Date and grid utilities
-----------------------

.. autosummary::
   :toctree: reference

   imdlib.util.LeapYear
   imdlib.util.get_filename
   imdlib.util.get_filename_realtime
   imdlib.util.get_lat_lon
   imdlib.util.parse_date_input
   imdlib.util.total_days
   imdlib.compute.bk_point
   imdlib.compute.bk_point_month
