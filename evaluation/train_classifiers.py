import sys
sys.path.append(".")

import argparse
import datetime
import numpy as np
import os
import shutil
import torch

from brainpedia.brainpedia import Brainpedia
from models.classifier import Classifier
from torch.autograd import Variable
from utils.plot import Plot


parser = argparse.ArgumentParser(description="Train classifiers on real and synthetic data.")
parser.add_argument('data_dir', help='the directory containing real fMRI data')
parser.add_argument('augmented_data_dir', help='the directory containing synthetic fMRI data')
parser.add_argument('output_dir', help='the directory to save evaluation results')
args = parser.parse_args()

# ========== HOUSEKEEPING ==========
CUDA = torch.cuda.is_available()
if CUDA:
    print("Using GPU optimizations!")

np.random.seed(1)
torch.manual_seed(1)
if CUDA:
    torch.cuda.manual_seed(1)

# ========== HYPERPARAMETERS ==========
shutil.rmtree(args.output_dir, ignore_errors=True)
os.makedirs(args.output_dir)

DOWNSAMPLE_SCALE = 0.25
TRAINING_STEPS = 200000
MODEL_DIMENSIONALITY = 64
BATCH_SIZE = 16
VISUALIZATION_INTERVAL = 1000
NOISE_SAMPLE_LENGTH = 128

description_f = open(args.output_dir + '/classifier_training_config.txt', 'w')
description_f.write('DATE: {0}\n\n'.format(datetime.datetime.now().strftime('%b-%d-%I%M%p-%G')))
description_f.write('DOWNSAMPLE_SCALE: {0}\n'.format(DOWNSAMPLE_SCALE))
description_f.write('TRAINING_STEPS: {0}\n'.format(TRAINING_STEPS))
description_f.write('BATCH_SIZE: {0}\n'.format(BATCH_SIZE))
description_f.write('MODEL_DIMENSIONALITY: {0}\n'.format(MODEL_DIMENSIONALITY))
description_f.write('VISUALIZATION_INTERVAL: {0}\n'.format(VISUALIZATION_INTERVAL))
description_f.write('NOISE_SAMPLE_LENGTH: {0}\n'.format(NOISE_SAMPLE_LENGTH))
description_f.close()

# ========== DATA ==========
# Real data:
brainpedia = Brainpedia(data_dirs=[args.data_dir],
                        cache_dir='data/real_data_cache/',
                        scale=DOWNSAMPLE_SCALE)
train_brain_data, train_brain_data_tags, test_brain_data, test_brain_data_tags = brainpedia.train_test_split()
test_brain_data = Variable(torch.Tensor(test_brain_data))

# Augmented data:
augmented_brainpedia = Brainpedia(data_dirs=[args.data_dir, args.augmented_data_dir],
                                  cache_dir='data/augmented_data_cache/',
                                  scale=DOWNSAMPLE_SCALE)
augmented_all_brain_data, augmented_all_brain_data_tags = augmented_brainpedia.all_data()

# TODO: Remove test data from augmented brain data set.
# TODO: Determine if both datasets need to be computed with the same mask.

# Build real data generator:
train_generator = brainpedia.batch_generator(train_brain_data, train_brain_data_tags, BATCH_SIZE, CUDA)
brain_data_shape, brain_data_tag_shape = brainpedia.sample_shapes()

# Build augmented data generator:
augmented_train_generator = augmented_brainpedia.batch_generator(augmented_all_brain_data, augmented_all_brain_data_tags, BATCH_SIZE, CUDA)
augmented_brain_data_shape, augmented_brain_data_tag_shape = augmented_brainpedia.sample_shapes()

# ========== MODELS ==========
nn_classifier = Classifier(dimensionality=MODEL_DIMENSIONALITY,
                           num_classes=brain_data_tag_shape[0],
                           cudaEnabled=CUDA)
augmented_nn_classifier = Classifier(dimensionality=MODEL_DIMENSIONALITY,
                                     num_classes=augmented_brain_data_tag_shape[0],
                                     cudaEnabled=CUDA)

def compute_accuracy(nn_classifier, augmented_nn_classifier):
    total_tests = len(test_brain_data_tags)

    # Generate predictions on test set:
    nn_classifier_predictions = nn_classifier.forward(test_brain_data)
    augmented_nn_classifier_predictions = augmented_nn_classifier.forward(test_brain_data)
    random_guesses = np.array(test_brain_data_tags).copy()
    np.random.shuffle(random_guesses)

    # Count number of correct predictions:
    num_nn_classifier_correct = 0
    num_augmented_nn_classifier_correct = 0
    num_rand_guesses_correct = 0
    num_same_guesses = 0

    for i in range(total_tests):
        truth = brainpedia.decode_label(test_brain_data_tags[i])
        nn_prediction = brainpedia.decode_label(nn_classifier_predictions[i].data)
        augmented_nn_prediction = augmented_brainpedia.decode_label(augmented_nn_classifier_predictions[i].data)
        random_prediction = brainpedia.decode_label(random_guesses[i])

        if nn_prediction == truth:
           num_nn_classifier_correct += 1
        if augmented_nn_prediction == truth:
           num_augmented_nn_classifier_correct += 1
        if random_prediction == truth:
            num_rand_guesses_correct += 1
        if nn_prediction == augmented_nn_prediction:
            num_same_guesses += 1

    # Compute accuracy:
    nn_accuracy = float(num_nn_classifier_correct)/float(total_tests)
    nn_augmented_accuracy = float(num_augmented_nn_classifier_correct)/float(total_tests)
    random_accuracy = float(num_rand_guesses_correct)/float(total_tests)
    fraction_same_guesses = float(num_same_guesses)/float(total_tests)

    return nn_accuracy, nn_augmented_accuracy, random_accuracy, fraction_same_guesses

# ========== TRAINING ===========
nn_classifier_loss_per_vis_interval = []
augmented_nn_classifier_loss_per_vis_interval = []

nn_classifier_acc_per_vis_interval = []
augmented_nn_classifier_acc_per_vis_interval = []

running_nn_classifier_loss = 0.0
running_augmented_nn_classifier_loss = 0.0

for training_step in range(1, TRAINING_STEPS + 1):
    print("BATCH: [{0}/{1}]\r".format(training_step % VISUALIZATION_INTERVAL, VISUALIZATION_INTERVAL), end='')

    # Retrieve [REAL] brain image data batch:
    brain_img_data_batch, labels_batch = next(train_generator)
    brain_img_data_batch = Variable(brain_img_data_batch)
    labels_batch = Variable(labels_batch)

    # Retrieve [REAL + SYNTHETIC] brain image data batch:
    augmented_brain_img_data_batch, augmented_labels_batch = next(augmented_train_generator)
    augmented_brain_img_data_batch = Variable(augmented_brain_img_data_batch)
    augmented_labels_batch = Variable(augmented_labels_batch)

    # Train classifiers:
    nn_classifier_loss = nn_classifier.train(brain_img_data_batch, labels_batch)
    nn_classifier_augmented_loss = augmented_nn_classifier.train(augmented_brain_img_data_batch, augmented_labels_batch)

    running_nn_classifier_loss += nn_classifier_loss.data[0]
    running_augmented_nn_classifier_loss += nn_classifier_augmented_loss.data[0]

    # Visualization:
    if training_step % VISUALIZATION_INTERVAL == 0:
        # Compute accuracy stats:
        nn_accuracy, nn_augmented_accuracy, random_accuracy, fraction_same_guesses = compute_accuracy(nn_classifier, augmented_nn_classifier)
        nn_classifier_acc_per_vis_interval.append(nn_accuracy)
        augmented_nn_classifier_acc_per_vis_interval.append(nn_augmented_accuracy)

        # Logging:
        print("===== TRAINING STEP {0} / {1} =====".format(training_step, TRAINING_STEPS))
        print("NN CLASSIFIER LOSS:            {0}".format(running_nn_classifier_loss))
        print("NN AUGMENTED CLASSIFIER LOSS:  {0}".format(running_augmented_nn_classifier_loss))
        print("NN CLASSIFIER ACCURACY: {0:.2f}%".format(100.0 * nn_accuracy))
        print("NN AUGMENTED CLASSIFIER ACCURACY: {0:.2f}%".format(100.0 * nn_augmented_accuracy))
        print("RANDOM CLASSIFIER ACCURACY: {0:.2f}%".format(100.0 * random_accuracy))
        print("PERCENT SAME GUESSES: {0:.2f}%\n".format(100.0 * fraction_same_guesses))

        # Loss histories
        nn_classifier_loss_per_vis_interval.append(running_nn_classifier_loss)
        augmented_nn_classifier_loss_per_vis_interval.append(running_augmented_nn_classifier_loss)
        running_nn_classifier_loss = 0.0
        running_augmented_nn_classifier_loss = 0.0

        Plot.plot_histories([nn_classifier_loss_per_vis_interval, augmented_nn_classifier_loss_per_vis_interval],
                            ['[REAL] Loss', '[REAL+SYNTHETIC] Loss'],
                            "{0}/loss_history".format(args.output_dir))
        Plot.plot_histories([nn_classifier_acc_per_vis_interval, augmented_nn_classifier_acc_per_vis_interval],
                            ['[REAL] Accuracy', '[REAL+SYNTHETIC] Accuracy'],
                            "{0}/accuracy_history".format(args.output_dir))

        # Save model at checkpoint
        torch.save(nn_classifier.state_dict(), "{0}/nn_classifier".format(args.output_dir))
        torch.save(augmented_nn_classifier.state_dict(), "{0}/augmented_nn_classifier".format(args.output_dir))
