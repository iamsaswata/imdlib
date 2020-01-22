#!/bin/bash

# change the package name to the existing PyPi package you would like to build
pkg='imdlib'
echo $PWD
array=( 3.7 )
echo "Building conda package ..."
cd ~
echo $PWD
conda skeleton pypi $pkg
cd $pkg
echo $PWD
wget https://conda.io/docs/_downloads/build1.sh
wget https://conda.io/docs/_downloads/bld.bat
cd ~
# building conda packages
echo $PWD
for i in "${array[@]}"
do
	conda-build --python $i $pkg
done
# convert package to other platforms
cd ~
echo $PWD
platforms=( linux-64 )
find $HOME/miniconda/conda-bld/linux-64/ -name *.tar.bz2 | while read file
do
    echo $file
    #conda convert --platform all $file  -o $HOME/conda-bld/
    for platform in "${platforms[@]}"
    do
       conda convert --platform $platform $file  -o $HOME/miniconda/conda-bld/
    done    
done
# upload packages to conda
# find $HOME/conda-bld/ -name *.tar.bz2 | while read file
# do
#     echo $file
#     anaconda upload $file
# done
# echo "Building conda package done!"