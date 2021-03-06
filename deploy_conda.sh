#!/bin/bash

# change the package name to the existing PyPi package you would like to build
pkg='imdlib'
echo $PWD
array=( 3.6 )
echo "Building conda package ..."
cd ~
echo $PWD
conda skeleton pypi $pkg
cd $pkg
echo $PWD
cd ~

# building conda packages
echo "========================"
echo "Building conda packages"
echo "========================"
echo $PWD
for i in "${array[@]}"
do
	conda-build --python $i $pkg
done


# convert package to other platforms
echo "========================="
echo "Cinverting conda packages"
echo "========================="
cd ~
echo $PWD
platforms=( linux-64 win-64 )
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
echo "========================="
echo "Uploading conda packages"
echo "========================="
find $HOME/miniconda/conda-bld/ -name *.tar.bz2 | while read file
do
    echo $file
    anaconda -t $ANACONDA_TOKEN upload $file
done
echo "Building conda package done!"
echo "==================================="
echo "Succeessful submission to anaconda"
echo "==================================="