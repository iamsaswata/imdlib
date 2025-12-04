#!/bin/bash
echo "========================"
echo "========================"
echo "Showing home directory"
echo $HOME
ls
echo "========================"

echo "========================"
echo "Downaloading miniconda"
wget https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh
ls
echo "========================"

echo "========================"
echo "========================"
echo "Setting up miniconda"
chmod +x miniconda.sh
bash miniconda.sh -b -p $HOME/miniconda
export PATH="$HOME/miniconda/bin:$PATH"
echo "========================"


echo "========================"
echo "========================"
echo "Update miniconda"
conda install -y -c conda-forge python=3.10
conda config --set always_yes true --set changeps1 no
conda install -y conda-build
conda install -y anaconda-client
echo "========================"

echo "========================"
echo "========================"
echo "install requirements.txt"
pip install -r requirements.txt
echo "========================"


echo "========================"
echo "======================"
# change the package name to the existing PyPi package you would like to build
pkg='imdlib'
echo $PWD
array=( 3.10 )
echo "Building conda package ..."
cd ~
echo $PWD
conda skeleton pypi $pkg --python-version 3.6
cd $pkg
echo $PWD
cd ~
echo "======================"


echo "========================"
echo "======================"
echo "Building conda packages"

echo $PWD
for i in "${array[@]}"
do
    # CHANGE 1: Added --pkg-format 2
    # This forces the output to be .tar.bz2, which is required for 'conda convert' to work
    conda-build --python $i $pkg --pkg-format 2
done
echo "========================"



# convert package to other platforms
echo "========================="
echo "Converting conda packages"
echo "========================="
cd ~
echo $PWD
platforms=( win-64 )

# CHANGE 2: Look for *.tar.bz2 (since we forced the build format above)
find $HOME/miniconda/conda-bld/linux-64/ -name "*.tar.bz2" | while read file
do
    echo "Found file to convert: $file"
    for platform in "${platforms[@]}"
    do
       conda convert --platform $platform $file -o $HOME/miniconda/conda-bld/
    done    
done
echo "========================="
echo "Converting finished"
echo "========================="


echo "========================="
echo "Uploading conda packages"
echo "========================="

# CHANGE 3: Upload all .tar.bz2 files (This includes the original Linux build and the converted Windows build)
find $HOME/miniconda/conda-bld/ -name "*.tar.bz2" | while read file
do
    echo "Uploading $file"
    anaconda -t $ANACONDA_TOKEN upload $file --user iamsaswata --skip-existing
done

echo "Building conda package done!"
echo "==================================="
echo "Succeessful submission to anaconda"
echo "==================================="
