.. _faq:

Frequently Asked Questions
==========================

What is the development status of the imdlib library?
-----------------------------------------------------

As of today (January 2021), IMDLIB is used by several people (number unknown), specifically 
by research scholars across India. We plan to continue and maintain imdlib in the coming 
future with our limited time. Imdlib is small but a useful library that we enjoyed during our 
PhD journey.

What others tools should I know about?
--------------------------------------

For handling of gridded data, the netCDF file system is universally adopted. The `xarray`_ 
is an excellent library for handling netCDF data in python. If one need parallel support,  
`Dask`_ provides advanced parallelism for analytics. But, both of them use `numpy`_ / `pandas`_ 
as the core for building their final product. In the cloud-native future, hopefully `pangeo`_ 
will emerge as the one of the most powerful tool for data analytics.

.. _xarray: http://xarray.pydata.org/en/stable/
.. _dask: https://dask.org/
.. _numpy: https://numpy.org/
.. _pandas: http://pandas.pydata.org/
.. _pangeo: https://pangeo-data.github.io/

Why developing imdlib?
----------------------

As hydrologist and climate scientist, we had to find and process the various meteorological (Indian)
variables quite often. Recently, gridded forcing datasets are made publicly available in India. From our past 
experiences we were aware how painful and tedious it is to get and process the binary gridded 
dataset for our area of interest. So, initially it was developed only for our own use. But, from 
encouragement of other fellow friends, we decided to to give it a proper shape and publish as a 
proper package.

Can imdlib process any GRD file?
--------------------------------
No. For hadling any binary dataset, one needs to know the exact dimension (both spatial and 
temporal) of the given data.Imdlib takes this information from `imdpune`_. So, if the data 
dimension doesn't match with the imdlib considered dimension, it won't be able to read or further 
process the data.

.. _imdpune: http://imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html