Description
===========

IMDLIB is a python package to download and handle binary grided data from India Meteorological Department (IMD). For more information about the IMD datasets, follow the following link: http://imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html

Installation
============

IMDLIB is tested for both Windows and Linux platforms with 64-bit architecture only.

The recommended way to install IMDLIB is using conda.

.. code-block:: bash

    conda install -c iamsaswata imdlib

Installation using pip.

.. code-block:: bash

    pip install imdlib

Installation from source/development version.

.. code-block:: bash

    pip install git+https://github.com/iamsaswata/imdlib.git

Dependencies
------------
IMDLIB is currently built with the following plugins. You need to have a python version >= 3.5 and install the below dependencies before installing IMDLIB from source.

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
   * - requests
     - [2.23.0]
   * - scipy
     - [1.4.1]
   * - six
     - [1.14.0]
   * - xarray
     - [0.14.1] 
   * - rioxarray (optional)
     - [0.1.1]     
