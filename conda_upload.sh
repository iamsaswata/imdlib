# Only need to change these two variables
PKG_NAME=imdlib
USER=iamsaswata

OS=$TRAVIS_OS_NAME-64
mkdir ~/conda-bld
conda config --set anaconda_upload no
export CONDA_BLD_PATH=~/conda-bld
export VERSION=0.0.5
conda build .
anaconda -t $ANACONDA_TOKEN upload -u $USER -l nightly $CONDA_BLD_PATH/$OS/$PKG_NAME-$VERSION.tar.bz2 --force
