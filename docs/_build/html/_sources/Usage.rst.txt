Downloading
===========

IMDLIB is capable of downloading gridded rainfall and temperature data (min and max). Here is an example of downloading rainfall dataset from 2010 to 2018.

.. code-block:: python

    import imdlib as imd

    # Downloading 8 years of rainfall data for India
    start_yr = 2010
    end_yr = 2018
    variable = 'rain' # other options are ('tmin'/ 'tmax')
    imd.get_data(variable, start_yr, end_yr, fn_format='yearwise')

Output
######

.. code-block:: python

    Downloading: rain for year 2010
    Downloading: rain for year 2011
    Downloading: rain for year 2012
    Downloading: rain for year 2013
    Downloading: rain for year 2014
    Downloading: rain for year 2015
    Downloading: rain for year 2016
    Downloading: rain for year 2017
    Downloading: rain for year 2018
    Download Successful !!!

The output is saved in the current working directory. If you want to save the files to a different directory, then you can use following code:

.. code-block:: python
    import imdlib as imd

    # Downloading 8 years of rainfall data for India
    start_yr = 2010
    end_yr = 2018
    variable = 'rain' # other options are ('tmin'/ 'tmax')
    file_dir = (r'C:\Users\imdlib\Desktop\\') #Path to save the files
    imd.get_data(variable, start_yr, end_yr, fn_format='yearwise', file_dir=file_dir)

Reading IMD datasets
====================

One major purposes of IMDLIB is to process IMD’s gridded dataset. The original data is available in ``grd`` file format. IMDLIB can read ``grd`` file in ``xarray`` and will create a ``IMD class object``.

.. code-block:: python
    import imdlib as imd

    # Downloading 8 years of rainfall data for India
    start_yr = 2010
    end_yr = 2018
    variable = 'rain' # other options are ('tmin'/ 'tmax')
    file_dir = (r'C:\Users\imdlib\Desktop\\') #Path to save the files
    data = imd.open_data(variable, start_yr, end_yr,'yearwise', file_dir)
    data

Output
######

``<imdlib.core.IMD at 0x13e5b3753c8>``

- ``file_dir`` should refer to top-lev directory. It should contain 3 sub-directories ``rain``, ``tmin``, and ``tmax``.

- if ``file_dir`` exist, but no subdirectory, it will try to find the files in ``file_dir``. But be careful if you are using ``file_format = ‘yearwise’``, as it will not differentiate between ``2018.grd`` for rainfall and ``2018.grd`` for tmin.

- if ``file_dir`` is not given, it will look for the associate subdirectories and files from the current directory.

Processing
==========

Plotting
========

Saving
======
