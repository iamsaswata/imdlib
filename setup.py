#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:copyright: (c) 2023 by Saswata Nandi
:license: MIT, see LICENSE for more details.
"""
import os
import sys

from setuptools import setup, find_packages
from setuptools.command.install import install

# imdlib version
VERSION = "0.1.19"


def readme():
  """print long description"""
  with open('README.md') as f:
    return f.read()


setup(
    name="imdlib",
    version="0.1.19",
    author="Saswata Nandi",
    author_email="iamsaswata@yahoo.com",
    description="A tool for handling and downloading IMD gridded data",
    long_description=readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/iamsaswata/",
    license="MIT",
    packages=find_packages(),
    classifiers=[
                 "Programming Language :: Python :: 3",
                 "License :: OSI Approved :: MIT License",
                 "Operating System :: OS Independent",
                 "Development Status :: 4 - Beta",
                 "Intended Audience :: Developers",
                 "Intended Audience :: Science/Research",
                 "License :: OSI Approved :: MIT License",
                 "Operating System :: OS Independent",
                 "Topic :: Scientific/Engineering :: Hydrology",
    ],
    python_requires='>=3.0',

    keywords='imd, India, rainfall, data, hydrology, IMD, grid, grided, gridded',
    # packages=['':'cct_nn'],
    install_requires=['matplotlib',
                      'numpy',
                      'pandas',
                      'six',
                      'pandas',
                      'python-dateutil',
                      'pytz',
                      'urllib3',
                      'scipy',
                      'xarray',
                      'requests', ]
)
