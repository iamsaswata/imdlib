.. _faq:

Frequently Asked Questions
==========================

Why IMDLIB?
-----------

The contemporary hydroclimatic studies require a huge data handling. Recently, the high-resolution gridded meteorological datasets, i.e., Rainfall (0.25° x 0.25°), Maximum and Minimum Temperature (1° x 1°) at daily timescale are made open-access. This gridded dataset for India was prepared considering measurements from well-spread gauge stations over the Indian land region after expanded quality controls. However, extraction of data for our area of interest often consumes substantial time and effort. IMDLIB resolves this problem by providing an easy and user-friendly way to extract continuous gridded data.

Can imdlib process any GRD file?
--------------------------------
No. For hadling any binary dataset, one needs to know the exact dimension (both spatial and 
temporal) of the given data. IMDLIB takes this information from `imdpune`_. So, if the data 
dimension doesn't match with that of IMDLIB, it won't be able to read or further 
process them.

.. _imdpune: http://imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html

What is the development status of the imdlib library?
-----------------------------------------------------

As of today (March 2021), IMDLIB is used by a large mass of people, specifically the M.Tech, Ph.D. and postdoctoral scholars across India. We would continue and maintain IMDLIB in the future as well. IMDLIB is a useful library that we enjoyed building during our Ph.D. journey.

What others tools should I know about?
--------------------------------------

For handling of gridded data, the netCDF file system is universally adopted. The `xarray`_ 
is an excellent library for handling netCDF data in python. If one needs parallel support,  
`Dask`_ provides advanced parallelism for analytics. But, both of them use `numpy`_ / `pandas`_ 
as the core for building their final product. Hopefully, `pangeo`_ will emerge as one of the most powerful tools for data analytics in the cloud-native future.

.. _xarray: http://xarray.pydata.org/en/stable/
.. _dask: https://dask.org/
.. _numpy: https://numpy.org/
.. _pandas: http://pandas.pydata.org/
.. _pangeo: https://pangeo-data.github.io/



