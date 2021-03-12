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
conda install -y -c anaconda python=3.6
conda config --set always_yes true --set changeps1 no
conda update -q conda
conda install -y conda-build
conda install -y anaconda-client
echo "========================"

echo "========================"
echo "========================"
echo "install requirements.tt"
pip install -r requirements.txt
echo "========================"


echo "========================"
echo "======================"
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
echo "======================"


echo "========================"
echo "======================"
echo "Building conda packages"

echo $PWD
for i in "${array[@]}"
do
	conda-build --python $i $pkg
done
echo "========================"



# convert package to other platforms
echo "========================="
echo "Converting conda packages"
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
echo "========================="
echo "Converting finished"
echo "========================="


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
