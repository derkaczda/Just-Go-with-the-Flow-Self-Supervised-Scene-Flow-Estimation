import argparse
import math
from datetime import datetime
import numpy as np
import tensorflow as tf
import socket
import importlib
import os
import os.path as osp
import sys
import glob
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
import pickle
import pdb
import utils.tf_util
from utils.pointnet_util import *
from tf_grouping import query_ball_point, group_point, knn_point

len_cloud = 100000

parser = argparse.ArgumentParser()
parser.add_argument('--gpu', type=int, default=3, help='GPU to use [default: GPU 0]')
parser.add_argument('--model', default='model_concat_upsa', help='Model name [default: model_concat_upsa]')
parser.add_argument('--data', default='data_preprocessing/data_processed_maxcut_35_20k_2k_8192', help='Dataset directory [default: /data_preprocessing/data_processed_maxcut_35_20k_2k_8192]')
parser.add_argument('--model_path', default='log_train/model.ckpt', help='model checkpoint file path [default: log_train/model.ckpt]')
parser.add_argument('--num_point', type=int, default=2048, help='Point Number [default: 2048]')
parser.add_argument('--batch_size', type=int, default=16, help='Batch Size during training [default: 16]')
parser.add_argument('--num_frames', type=int, default=2, help='Number of frames to run cycle')
parser.add_argument('--decay_step', type=int, default=200000, help='Decay step for lr decay [default: 200000]')
parser.add_argument('--decay_rate', type=float, default=0.7, help='Decay rate for lr decay [default: 0.7]')
parser.add_argument('--radius', type=float, default=5.0, help='Radius of flow embedding layer')
parser.add_argument('--layer', type=str, default='pointnet', help='Last layer for upconv')
parser.add_argument('--knn', action='store_true', help='knn or query ball point')
parser.add_argument('--flow', type=str, default='default', help='flow embedding module type')
parser.add_argument('--kitti_dataset', default='data_preprocessing/kitti_self_supervised_flow', help='Dataset directory [default: /data_preprocessing/kitti_self_supervised_flow]')


FLAGS = parser.parse_args()

os.environ['CUDA_VISIBLE_DEVICES'] = str(FLAGS.gpu)

BATCH_SIZE = FLAGS.batch_size
NUM_POINT = FLAGS.num_point
DATA = FLAGS.data
GPU_INDEX = FLAGS.gpu
NUM_FRAMES = FLAGS.num_frames
DECAY_STEP = FLAGS.decay_step
DECAY_RATE = FLAGS.decay_rate
RADIUS = FLAGS.radius

print (FLAGS)

MODEL = importlib.import_module(FLAGS.model) # import network module
MODEL_FILE = os.path.join(BASE_DIR, FLAGS.model+'.py')
MODEL_PATH = FLAGS.model_path
LAYER = FLAGS.layer
KNN = FLAGS.knn
FLOW_MODULE = FLAGS.flow
KITTI_DATASET = FLAGS.kitti_dataset

BN_INIT_DECAY = 0.5
BN_DECAY_DECAY_RATE = 0.5
BN_DECAY_DECAY_STEP = float(DECAY_STEP)
BN_DECAY_CLIP = 0.99


def scene_flow_EPE_np(pred, labels, mask):
    error = np.sqrt(np.sum((pred - labels)**2, 2) + 1e-20)

    gtflow_len = np.sqrt(np.sum(labels*labels, 2) + 1e-20) # B,N
    acc1 = np.sum(np.logical_or((error <= 0.05)*mask, (error/gtflow_len <= 0.05)*mask), axis=1)
    acc2 = np.sum(np.logical_or((error <= 0.1)*mask, (error/gtflow_len <= 0.1)*mask), axis=1)

    mask_sum = np.sum(mask, 1)
    acc1 = acc1[mask_sum > 0] / mask_sum[mask_sum > 0]
    acc1 = np.sum(acc1)
    acc2 = acc2[mask_sum > 0] / mask_sum[mask_sum > 0]
    acc2 = np.sum(acc2)

    EPE = np.sum(error)

#     EPE = np.sum(error * mask, 1)[mask_sum > 0] / mask_sum[mask_sum > 0]
#     EPE = np.sum(EPE)
    return EPE, acc1, acc2, error, gtflow_len

def get_bn_decay(batch):
    bn_momentum = tf.train.exponential_decay(
        BN_INIT_DECAY,
        batch * BATCH_SIZE,
        BN_DECAY_DECAY_STEP,
        BN_DECAY_DECAY_RATE,
        staircase=True)
    bn_decay = tf.minimum(BN_DECAY_CLIP, 1 - bn_momentum)
    return bn_decay

with tf.Graph().as_default():
    with tf.device('/gpu:'+str(GPU_INDEX)):
        pointclouds_pl, labels_pl, masks_pl = MODEL.placeholder_inputs(None, NUM_POINT)

        is_training_pl = tf.placeholder(tf.bool, shape=())

        batch = tf.Variable(0)  # batch = 0
        bn_decay = get_bn_decay(batch)    # bn_decay = 0.5
        print("--- Get model and loss")
        # Get model and loss
        pred, end_points = MODEL.get_model(RADIUS, LAYER, pointclouds_pl, is_training_pl, bn_decay=bn_decay,
                knn=KNN, flow_module=FLOW_MODULE)

        saver = tf.train.Saver()

    # Create a session
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    config.log_device_placement = False
    sess = tf.Session(config=config)

    saver.restore(sess, MODEL_PATH)

    ops = {'pointclouds_pl': pointclouds_pl,
           'label': labels_pl,
           'is_training_pl': is_training_pl,
           'pred': pred,
           'end_points': end_points}
    is_training = False

#     all_kitti = glob.glob('/home/gaurav/himangi/flownet3d_research/cvpr/after_cvpr/rebuttal/kitti_final_points_estimate/*.npz')
    all_kitti = glob.glob(os.path.join(KITTI_DATASET, '*.npz'))

    result_save_path = osp.join('/'.join(MODEL_PATH.split('/')[:-1]), 'eval_results')
    print(result_save_path)
    os.makedirs(result_save_path, exist_ok=True)

    print(f"all kitti {all_kitti}")
    l_error_all = []
    l_gt_all = []
    l_dist_center = []
    l_query_ball = []
    l_dist_count_all = []

    num_frame = 40
    epe_total = 0
    epe_count = 0
    batch_count = 0
    sample_count = 0

    all_pred = []
    all_label = []
    all_points = []

    for ki in all_kitti:
#         try:
        x = np.load(ki, allow_pickle=True)[()]

        batch_label = []
        batch_data = []
        batch_mask = []

        print(x)
        ref_pc = x['pos1'][:, :3]
        print(f"ref_pc length {ref_pc.shape} pos2 length {x['pos2'].shape}")
        ref_center = np.mean(ref_pc, 0)
        min_point_len = min(ref_pc.shape[0], x['pos2'].shape[0])
        len_cloud = (min_point_len // 2048) * 2048
        random_indices = np.random.permutation(min_point_len)[:len_cloud]
        pos1 = x['pos1'][random_indices]
        pos2 = x['pos2'][random_indices]
        for i in range(0, len_cloud, 2048):
            if i+2048 < len(pos1) and i+2048 < len(pos2):

                pc1 = pos1[i:i+2048, :3]
                pc2 = pos2[i:i+2048, :3]
                gt = np.zeros((2048,3)) #x['gt'][i:i+2048, :3]

                pc1 = pc1 - ref_center
                pc2 = pc2 - ref_center

                batch_data.append(np.concatenate([np.concatenate([pc1,
                                                                  pc2], axis=0),
                                                  np.zeros((4096, 3))], axis=1)) # 4096, 6


                batch_label.append(gt)

        batch_data = np.array(batch_data) # 10 x 4096 x 6
        print(batch_data.shape)

        batch_label = np.array(batch_label)

        feed_dict = {ops['pointclouds_pl']: batch_data,
                     ops['is_training_pl']: is_training,}

        pred_val, end_points_val = sess.run([ops['pred'], ops['end_points']], feed_dict=feed_dict)
        epe, acc1, acc2, error, gt_label = scene_flow_EPE_np(pred_val, batch_label,
                                        np.ones(pred_val.shape, dtype=np.int32)[:,:,0])

        epe_total += epe
        sample_count += batch_data.shape[0]*(batch_data.shape[1]/2)
        batch_count += batch_data.shape[0]

        all_pred.append(pred_val)
        all_points.append(batch_data)
        all_label.append(batch_label)

        np.save(
            osp.join(result_save_path, f"{ki.split('/')[-1]}_allpred"),
            pred_val,
            allow_pickle=True
        )
        np.save(
            osp.join(result_save_path, f"{ki.split('/')[-1]}_allpoints"),
            batch_data,
            allow_pickle=True
        )
        np.save(
            osp.join(result_save_path, f"{ki.split('/')[-1]}_alllabel"),
            batch_label,
            allow_pickle=True
        )


    all_pred = np.array(all_pred)
    all_points = np.array(all_points)
    all_label = np.array(all_label)

    print (all_pred.shape, all_points.shape, all_label.shape)

    print('Num batches {} Average EPE {}'.format(sample_count,epe_total/sample_count))
    print ('eval mean EPE 3D: %f' % (epe_total / sample_count))

