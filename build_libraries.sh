#!/bin/bash

home_dir=$(pwd)

cd src/tf_ops/3d_interpolation
rm tf_interpolate_so.so
make

cd ${home_dir}/src/tf_ops/grouping
rm tf_grouping_so.so
make

cd ${home_dir}/src/tf_ops/sampling
rm tf_sampling_so.so
make