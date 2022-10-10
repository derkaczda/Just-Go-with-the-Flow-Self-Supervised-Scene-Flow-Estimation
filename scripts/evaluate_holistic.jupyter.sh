#!/bin/bash

MODEL_PATH=
DATA_PATH=
NUM_POINTS=2048
BATCH_SIZE=8
RADIUS=5
LAYER=pointnet
GPU=0
NUM_FRAMES=2
MODEL="model_concat_upsa"

parentdir=$(dirname `pwd`)
python3 ${parentdir}/src/evaluate_cycle_holistic.py \
    --model ${MODEL} \
    --data ${DATA_PATH} \
    --model_path ${MODEL_PATH} \
    --kitti_dataset ${DATA_PATH} \
    --num_point ${NUM_POINTS} \
    --batch_size ${BATCH_SIZE} \
    --radius ${RADIUS} \
    --layer ${LAYER} \
    --gpu ${GPU} \
    --num_frames ${NUM_FRAMES}