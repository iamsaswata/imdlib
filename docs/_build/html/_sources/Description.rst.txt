Description
===========

IMDLIB is a python package to download and handle binary gridded data from the India Meteorological Department (IMD). For more information about the IMD datasets, check the following link: `IMD Pune`_.

.. _IMD Pune: https://imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html

Installation
============

IMDLIB is tested for both Windows and Linux platforms with 64-bit architecture.

We recommend using ‘Conda’  to install IMDLIB.

.. code-block:: bash

    conda install -c iamsaswata imdlib

Installation using pip:

.. code-block:: bash

    pip install imdlib

Installation from source/development version:

.. code-block:: bash

    pip install git+https://github.com/iamsaswata/imdlib.git

Dependency
------------
You need to have a python version 3.5 or higher for using IMDLIB. If you install IMDLIB from the source, the following plugins should be pre-installed.

.. list-table::
   :widths: 20 20
   :header-rows: 1

   * - Packages
     - version
   * - certifi
     - [2019.11.28]
   * - matplotlib
     - [3.1.3]
   * - numpy
     - [1.18.1]
   * - pandas
     - [0.25.3]          
   * - python-dateutil
     - [ 2.8.1 ]
   * - pytest
     - [5.3.4]
   * - pytz
     - [ 2019.3]
   * - urllib3
     - [1.25.0]
   * - scipy
     - [1.4.1]
   * - six
     - [1.14.0]
   * - xarray
     - [0.14.1] 
   * - rioxarray (optional)
     - [0.1.1]     
