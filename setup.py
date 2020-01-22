#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
:copyright: (c) 2020 by Saswata Nandi
:license: MIT, see LICENSE for more details.
"""
import os
import sys

from setuptools import setup, find_packages
from setuptools.command.install import install

# imdlib version
VERSION = "0.0.4"

def readme():
    """print long description"""
    with open('README.md') as f:
        return f.read()

setup(
    name="imdlib",
    version="0.0.4",
    author="Saswata Nandi",
    author_email="iamsaswata@yahoo.com",
    description="A tool for handling IMD gridded data",
    long_description=readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/iamsaswata/",
    license="MIT",
    packages=find_packages(),
    classifiers=[
                 "Programming Language :: Python :: 3",
                 "License :: OSI Approved :: MIT License",
                 "Operating System :: OS Independent",
                  "Development Status :: 0.0.4 - initial",
                  "Intended Audience :: Developers",
                  "Intended Audience :: Hydrologist",
                  "License :: OSI Approved :: MIT License",
                  "Operating System :: OS Independent",
                  "Topic :: Software Development :: Build Tools",
                  "Topic :: Software Development :: Libraries :: Python Modules",
                  "Topic :: Internet",                 
                ],
    python_requires='>=3.0',

    keywords='imd, India, rainfall, IMD, grid, grided, gridded',
    # packages=['':'cct_nn'],
    install_requires=['numpy',
                      'pandas',
                      'six',
                      'pandas',
                      'python-dateutil',
                      'pytz',
                      'xarray', ]   
)
