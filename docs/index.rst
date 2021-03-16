.. imdlib documentation master file, created by
   sphinx-quickstart on Wed Dec 30 16:00:48 2021.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

IMDLIB - a Python library for IMD gridded data
==============================================

IMDLIB is a python package to download and handle binary gridded data from the India Meteorological
Department (IMD). For more information about the IMD datasets, the link of 
`IMD Pune`_ may be referred. It heavily employes the `xarray`_ for processing the datasets.

.. _IMD Pune: https://imdpune.gov.in/Clim_Pred_LRF_New/Grided_Data_Download.html
.. _xarray: http://xarray.pydata.org/en/stable/

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   Description

.. toctree::
   :maxdepth: 1
   :caption: Usage

   Usage

.. toctree::
   :maxdepth: 1
   :caption: Reference
   
   faq
   changelog
   cf-conventions
   api

Citation
--------

If you are using imdlib and would like to cite it in academic publication, we recommend to use the zenodo DOI:

    .. image:: https://zenodo.org/badge/235463327.svg
       :target: https://doi.org/10.5281/zenodo.4405233

About
-----

:License:
    MIT License

:Status:
    .. image:: https://badge.fury.io/py/imdlib.svg
        :target: https://badge.fury.io/py/imdlib
    .. image:: https://anaconda.org/iamsaswata/imdlib/badges/version.svg
        :target: https://anaconda.org/iamsaswata/imdlib
    .. image:: https://zenodo.org/badge/235463327.svg
        :target: https://doi.org/10.5281/zenodo.4405233       

:Documentation:
    .. image:: https://readthedocs.org/projects/imdlib/badge/?version=stable
        :target: http://imdlib.readthedocs.io/?badge=stable

:Contact:
    * `Saswata Nandi <https://saswatanandi.github.io/>`__
    * `Pratiman Patel <https://pratiman-91.github.io/>`__
    * `Sabyasachi Swain <https://scholar.google.co.in/citations?user=5l3ePBUAAAAJ/>`__

.. meta::
   :description: IMDLIB is a python package library to download IMD gridded data
   :keywords: imdlib, IMDLIB, imd, IMD, python, India, gridded rainfall, gridded temperature
