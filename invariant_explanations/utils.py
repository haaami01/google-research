# coding=utf-8
# Copyright 2022 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities used for approxNN project."""

import gc
import os
import pickle
import sys

from absl import logging

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import psutil
import seaborn as sns
import tensorflow.compat.v2 as tf
from tensorflow.io import gfile
import tensorflow_datasets as tfds
from tqdm import tqdm

from invariant_explanations import config
from invariant_explanations import other

logging.set_verbosity(logging.INFO)


def create_experimental_folders():
  """Create timestamped experimental folder and subfolders.

  /../_experiments/
  |--    <timestamp>_<setup_name>
  |         `-- _models/
  |         `-- _plots/
  """
  if not gfile.exists(config.EXP_FOLDER_PATH):
    gfile.makedirs(config.EXP_FOLDER_PATH)
  if not gfile.exists(config.PLOTS_FOLDER_PATH):
    gfile.makedirs(config.PLOTS_FOLDER_PATH)
  if not gfile.exists(config.MODELS_FOLDER_PATH):
    gfile.makedirs(config.MODELS_FOLDER_PATH)


def file_handler(file_name, stream_flags):
  """Central method to handle saving and loading a file by filename.

  Args:
    file_name: the name of the file that is being saved/loaded
    stream_flags: flag to whether to read or write {wb, rb}

  Returns:
    a function handle used to stream the file bits.
  """
  return gfile.GFile(
      os.path.join(config.EXP_FOLDER_PATH, file_name),
      stream_flags,
  )


# Inspired by: https://stackoverflow.com/a/287944
class Bcolors:
  HEADER = '\033[95m'
  ENDC = '\033[0m'
  BOLD = '\033[1m'


def get_file_suffix(chkpt):
  """Defining a consistent file suffix for saving temporary files."""
  return (
      f'_@_epoch_{chkpt}'
      f'_test_acc>{config.KEEP_MODELS_ABOVE_TEST_ACCURACY}'
      f'_identical_samples_{config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS}'
  )


def print_memory_usage():
  """A debugging tool for clearning unused vairables."""
  mem_usage_mb = psutil.Process().memory_info().rss / 1024 / 1024
  logging.debug(
      '\t%smem_usage: %.4f MB%s', Bcolors.HEADER, mem_usage_mb, Bcolors.ENDC
  )


def reset_model_using_weights(model_wireframe, weights):
  """A tool to load flattened weights into model wireframe."""
  all_boundaries = {
      0: [(0, 16), (16, 160)],
      1: [(160, 176), (176, 2480)],
      2: [(2480, 2496), (2496, 4800)],
      3: [],  # GlobalAvgPool has no params
      4: [(4800, 4810), (4810, 4970)],  # FC
  }
  for layer_idx, layer_obj in enumerate(model_wireframe.layers):
    if not layer_obj.get_weights(): continue  # skip GlobalAvgPool
    layer_bias_start_idx = all_boundaries[layer_idx][0][0]
    layer_bias_stop_idx = all_boundaries[layer_idx][0][1]
    layer_weights_start_idx = all_boundaries[layer_idx][1][0]
    layer_weights_stop_idx = all_boundaries[layer_idx][1][1]
    layer_bias = np.reshape(
        weights[layer_bias_start_idx:layer_bias_stop_idx],
        layer_obj.get_weights()[1].shape,
    )  # b
    layer_weights = np.reshape(
        weights[layer_weights_start_idx:layer_weights_stop_idx],
        layer_obj.get_weights()[0].shape,
    )
    layer_obj.set_weights([layer_weights, layer_bias])
  return model_wireframe


def rounder(values, markers, use_log_rounding=False):
  """Round values in a list to the closest market from a list markers.

  Args:
    values: list of values to be rounded.
    markers: list of markers to which values are rounded.
    use_log_rounding: boolean flag where or not to apply log-rounding.

  Returns:
    List of values, rounded to the nearest values as set out in config.py.
  """
  # Inspired by: https://stackoverflow.com/a/2566508
  if use_log_rounding:
    values = np.log10(values)
    markers = np.log10(markers)
  def find_nearest(array, value):
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return array[idx]
  tmp = np.array([find_nearest(markers, val) for val in values])
  if use_log_rounding:
    return 10 ** tmp
  return tmp


def process_hparams(hparams):
  """Convert columns of the hparams dataframe to appropriate datatypes.

  Args:
    hparams: a dataframe of hyperparameters.

  Returns:
    dataframe of hparams whereby values in each column are (log-)rounded to the
    nearest market values as set out in config.py.
  """

  assert isinstance(hparams, pd.core.frame.DataFrame)
  # Convert numerical columns to numerical values (s/dtype=Object/dtype=float32)
  for col in config.NUM_HPARAMS:
    hparams[col] = hparams[col].astype('float32')

  for col in config.CAT_HPARAMS:
    logging.info(('Unique values for `%s`:\t', col), hparams[col].unique())

  # Because num_hparams are sampled (log-)uniformly from some range, we first
  # bin the values by rounding to carefully selected hparam values, and then
  # use the (few) selected bins are markers for drawing out ITE plots.
  for col in config.NUM_HPARAMS:
    values = hparams[col].values
    markers = config.ALL_HPARAM_RANGES[col]
    use_log_rounding = False
    if col in {'config.l2reg', 'config.init_std', 'config.learning_rate'}:
      use_log_rounding = True

    rounded_values = rounder(values, markers, use_log_rounding)
    hparams[col] = rounded_values

  return hparams


def plot_treatment_effect_values():
  """A method to plot individual and average treatment effects."""

  assert config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS

  chkpt = 86
  file_suffix = get_file_suffix(chkpt)
  with file_handler(f'samples{file_suffix}.npy', 'rb') as f:
    samples = pickle.load(f)
  with file_handler(f'y_preds{file_suffix}.npy', 'rb') as f:
    y_preds = pickle.load(f)
  with file_handler(f'y_trues{file_suffix}.npy', 'rb') as f:
    y_trues = pickle.load(f)
  with file_handler(f'hparams{file_suffix}.npy', 'rb') as f:
    hparams = pickle.load(f)
  # Reorder columns for easier readability when debugging.
  hparams = hparams[[*config.CAT_HPARAMS, *config.NUM_HPARAMS]]

  assert (
      samples.shape[0] ==
      y_trues.shape[0] ==
      y_preds.shape[0] ==
      hparams.shape[0]
  )
  # If NUM_BASE_MODELS < NUM_MODELS_WITH_TEST_ACC_<THRESH, the line below may
  # be different from config.NUM_BASE_MODELS * config.NUM_SAMPLES_PER_BASE_MODEL
  num_base_models_times_samples = samples.shape[0]

  hparams = process_hparams(hparams)

  # For each of the desired hparams
  for col in config.ALL_HPARAMS:

    logging.info('Plotting ITE and ATE for hparam `%s`...', col)

    ite_tracker = pd.DataFrame({
        'sample_str': [],
        'x_y_trues': [],
        'x_y_preds': [],
        'hparam_col': [],
        'hparam_val': [],
    })

    tmp_y_trues = []  # Keep track for plotting; easier than indexing later.
    for x_offset_idx in range(min(
        config.NUM_SAMPLES_TO_PLOT_TE_FOR,
        config.NUM_SAMPLES_PER_BASE_MODEL
    )):

      x_indices = range(
          x_offset_idx,
          num_base_models_times_samples,
          config.NUM_SAMPLES_PER_BASE_MODEL,
      )
      x_y_preds = np.argmax(y_preds[x_indices, :], axis=1)
      x_y_trues = np.argmax(y_trues[x_indices, :], axis=1)
      x_hparams = hparams.iloc[x_indices]

      # Sanity check: irrespective of the base model,
      # X_i is shared and so should share y_true value.
      assert np.all(x_y_trues == x_y_trues[0])
      sample_str = f'x{x_offset_idx}'  # _y={x_y_trues[0]}'
      tmp_y_trues.append(x_y_trues[0])

      # For each of the unique values of this hparam
      for val in x_hparams[col].unique():

        # Filter to those samples that were predicted
        # on models trained using this unique hparam.
        condition = x_hparams[col] == val
        matching_count = condition.sum()

        # Add to ite_tracker.
        ite_tracker = ite_tracker.append(
            pd.DataFrame({
                'sample_str': [sample_str] * matching_count,
                'x_y_trues': list(x_y_trues[condition]),
                'x_y_preds': list(x_y_preds[condition]),
                'hparam_col': [col] * matching_count,
                'hparam_val': [val] * matching_count,
            }),
            ignore_index=True,
        )

    catplot = sns.catplot(
        x='sample_str',
        y='x_y_preds',
        hue='hparam_val',
        data=ite_tracker,
        kind='violin',
    )
    fig = catplot.fig
    fig.set_size_inches(18, 6)
    # For every X_i (count: config.NUM_SAMPLES_TO_PLOT_TE_FOR),
    # put a star on the plot close to where the true label is.
    # Inspired by https://stackoverflow.com/a/37518947
    for tmp_x in range(config.NUM_SAMPLES_TO_PLOT_TE_FOR):
      tmp_y = tmp_y_trues[tmp_x]
      plt.plot(tmp_x, tmp_y, color='black', marker='*', markersize=14)
    fig.suptitle(
        f'Averaged over models with '
        f'test acc > %{100 * config.KEEP_MODELS_ABOVE_TEST_ACCURACY}'
    )
    fig.savefig(
        gfile.GFile(
            os.path.join(
                config.PLOTS_FOLDER_PATH,
                f'ite_{col}.png'
            ),
            'wb',
        ),
        dpi=400,
    )

    catplot = sns.catplot(
        x='hparam_val',
        y='x_y_preds',
        data=ite_tracker,
        kind='violin',
    )
    fig = catplot.fig
    fig.set_size_inches(18, 6)
    fig.savefig(
        gfile.GFile(
            os.path.join(
                config.PLOTS_FOLDER_PATH,
                f'ate_{col}.png'
            ),
            'wb',
        ),
        dpi=400,
    )


def load_base_model_weights_and_metrics():
  """Load base weights and metrics from CNN collections."""

  logging.info('Loading CNN Zoo weights and metrics...')
  if 'google.colab' in sys.modules:

    weights_path = (
        config.READAHEAD +
        os.path.join(config.CNS_PATH, config.DATA_DIR, 'weights.npy')
    )
    metrics_path = (
        config.READAHEAD +
        os.path.join(config.CNS_PATH, config.DATA_DIR, 'metrics.csv')
    )

    with gfile.GFile(weights_path, 'rb') as f:
      # Weights of the trained models
      base_model_weights = np.load(f)

    with gfile.GFile(metrics_path, 'rb') as f:
      # pandas DataFrame with metrics
      base_model_metrics = pd.read_csv(f, sep=',')

  else:

    base_model_weights = np.load(
        os.path.join(os.path.dirname(__file__), config.DATA_DIR, 'weights.npy')
    )
    base_model_metrics = pd.read_csv(
        os.path.join(os.path.dirname(__file__), config.DATA_DIR, 'metrics.csv'),
        sep=','
    )

  assert base_model_weights.shape[0] == base_model_metrics.values.shape[0]
  logging.info('Done.')
  return base_model_weights, base_model_metrics


def analyze_accuracies_of_base_models():
  """Plot & compare train/test accuracies of base models in CNN collection."""

  logging.info('Analyzing base model accuracies...')

  _, base_model_metrics = load_base_model_weights_and_metrics()

  accuracy_tracker = pd.DataFrame({
      'chkpt': [],
      'accuracy': [],
      'accuracy_type': [],
  })

  for chkpt in [0, 1, 2, 3, 20, 40, 60, 80, 86]:

    indices = base_model_metrics.index[
        base_model_metrics['step'] == chkpt
    ].tolist()

    for accuracy_type in ['train', 'test']:
      chkpt_list = [chkpt] * len(indices)
      accuracy_type_list = [accuracy_type] * len(indices)
      accuracy_list = base_model_metrics.iloc[
          indices
      ][f'{accuracy_type}_accuracy'].to_numpy()

      # Add to accuracy_tracker.
      accuracy_tracker = accuracy_tracker.append(
          pd.DataFrame({
              'chkpt': chkpt_list,
              'accuracy': accuracy_list,
              'accuracy_type': accuracy_type_list,
          }),
          ignore_index=True,
      )

  catplot = sns.catplot(
      x='chkpt',
      y='accuracy',
      hue='accuracy_type',
      data=accuracy_tracker,
      kind='violin',
  )
  fig = catplot.fig
  fig.set_size_inches(18, 6)
  fig.savefig(
      gfile.GFile(
          os.path.join(
              config.PLOTS_FOLDER_PATH,
              'base_model_accuracies.png',
          ),
          'wb',
      ),
      dpi=400,
  )


def extract_new_covariates_and_targets(random_seed, model, dataset_info,
                                       covariates_setting, base_model_weights,
                                       base_model_metrics):
  """Extract new dataset from the weights and metrics of the CNN collection.

  The new dataset is used to train a meta-model, on covariates and targets
  corresponding to X,H-->Y and X,W@epoch-->Y.

  Args:
    random_seed: the random seed used for reproducibility of results
    model: the wireframe of the CNN model in the zoo.
    dataset_info: a tfds dataset info with dimnesionality and number of samples.
    covariates_setting: a dictionary specifying the checkpoint at which to
                        extract data from the saved weights/metrics in the zoo.
    base_model_weights: the weights of the base models in the CNN zoo.
    base_model_metrics: the metrics of the base models in the CNN zoo.

  Returns:
    samples: samples used to train each base model; instance of np.ndarray.
    y_preds: the predicted target of samples on each base-model;
             instance of np.ndarray.
    y_trues: the true target of samples; instance of np.ndarray.
    hparams: the hparams used to train the base model; instance of pd.DataFrame.
    weights_chkpt: flattened weights of each base model at chkpt epoch;
                   instance of np.ndarray.
    weights_final: flattened weights of each base model at final epoch;
                   instance of np.ndarray
    metrics: the metrics (train and test accuracy, etc.) of each base model;
             instance of pd.DataFrame
  """

  np.random.RandomState(random_seed)

  assert base_model_weights.shape[0] == base_model_metrics.shape[0]
  if not config.run_on_test_data:
    if config.dataset == 'mnist':
      assert base_model_weights.shape[0] == 269973
    elif config.dataset == 'fashion_mnist':
      assert base_model_weights.shape[0] == 270000
    elif config.dataset == 'cifar10':
      assert base_model_weights.shape[0] == 270000
    elif config.dataset == 'svhn_cropped':
      assert base_model_weights.shape[0] == 269892

  logging.info('\tConstructing new dataset...')

  ############################################################################
  # Weights contains 270,000 rows (30K hparam settings @ 9 checkpoints).
  # Filter to relevant rows; then do a 50/50 tr/te split (see page 5, col 1)
  # from this paper: https://arxiv.org/pdf/2002.11448.pdf .
  ############################################################################

  # Filter to the appropriate rows for the weights at checkpoint `chkpt`.
  weights_chkpt_indices = base_model_metrics.index[
      base_model_metrics['step'] == covariates_setting['chkpt']
  ].tolist()
  # Also keep track of the rows for the weights at the final checkpoint, 86.
  weights_final_indices = base_model_metrics.index[
      base_model_metrics['step'] == 86
  ].tolist()
  # Rows in metrics file repeat for 9 rows (to match 9 checkpoint epoch).
  # Therefore, sample every other 9th row; IMPORTANT: any sequence would yield
  # the same hparams, but make sure you get the correct rows for tr/te acc!!!
  metrics_indices = weights_final_indices  # NOT weights_chkpt_indices, b/c we
                                           # filter (to keep) based on the final
                                           # performance

  # It's more reasonable to predict the targets for the final epoch, NOT at
  # chkpt epoch. Right? Yes. Because we aim for the meta-model to take in
  # X,H or X,W@epoch and give Y_pred at end of training, w/o needing to train.

  assert (
      len(metrics_indices) ==
      len(weights_chkpt_indices) ==
      len(weights_final_indices)
  )

  # IMPORTANT: indices is used for metrics, weights_chkpt, and weights_final;
  #            shuffling the order should be done consistently on all 3 arrays
  permuted_indices = np.random.permutation(range(len(metrics_indices)))
  metrics_indices = np.array(metrics_indices)[permuted_indices]
  weights_chkpt_indices = np.array(weights_chkpt_indices)[permuted_indices]
  weights_final_indices = np.array(weights_final_indices)[permuted_indices]

  base_model_metrics = base_model_metrics.iloc[metrics_indices]
  base_model_weights_chkpt = base_model_weights[weights_chkpt_indices, :]
  base_model_weights_final = base_model_weights[weights_final_indices, :]

  # Further filter the weights to those that yield good accuracy
  # and limit selection to only NUM_BASE_MODELS models.
  filtered_indices = np.where(
      base_model_metrics['test_accuracy'] >
      config.KEEP_MODELS_ABOVE_TEST_ACCURACY
  )[0][:config.NUM_BASE_MODELS]
  base_model_weights_chkpt = base_model_weights_chkpt[filtered_indices]
  base_model_weights_final = base_model_weights_final[filtered_indices]
  base_model_metrics = base_model_metrics.iloc[filtered_indices]
  local_num_base_models = base_model_metrics.shape[0]  # update this value

  ############################################################################
  # Construct and fill arrays of appropriate size for covariates and targets.
  ############################################################################

  size_x = np.prod(dataset_info['data_shape'])
  size_y = dataset_info['num_classes']

  num_new_samples = config.NUM_SAMPLES_PER_BASE_MODEL * local_num_base_models
  samples = np.zeros((num_new_samples, size_x))
  y_preds = np.zeros((num_new_samples, size_y))
  y_trues = np.zeros((num_new_samples, size_y))

  tmp = (
      f'[INFO] For each base model, construct network, load base_model_weights,'
      f'then get predictions for {config.NUM_SAMPLES_PER_BASE_MODEL} samples'
  )
  if config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS:
    tmp += '(on the same samples).'
  else:
    tmp += '(on different samples... to cover a wider distribution).'
  logging.info(tmp)

  # BUG IN COMMENTED CODE BELOW, ORDERING IS DIFFERENT FOR SAMPLES/TRUE LABELS
  # all_img_samples = np.concatenate([x for x, y in data_tr], axis=0)
  # all_img_y_trues = np.concatenate([y for x, y in data_tr], axis=0)
  all_img_samples, all_img_y_trues = tfds.as_numpy(
      tfds.load(
          config.dataset,
          split='train',
          batch_size=-1,
          as_supervised=True,
      )
  )
  all_img_y_trues = tf.keras.utils.to_categorical(
      all_img_y_trues,
      num_classes=dataset_info['num_classes'],
  )

  # IMPORTANT: w/o the processing below, the meta-model learning drops by >%50.
  all_img_samples = all_img_samples.astype(np.float32)
  all_img_samples /= 255
  min_out = -1.0
  max_out = 1.0
  all_img_samples = min_out + all_img_samples * (max_out - min_out)

  num_train_samples = all_img_samples.shape[0]

  if config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS:
    # Select identical samples in such a way that they are class-balanced.
    num_classes = other.get_dataset_info(config.dataset)['num_classes']
    num_samples_per_class = [len(x) for x in np.array_split(
        np.arange(config.NUM_SAMPLES_PER_BASE_MODEL),
        num_classes,
    )]
    all_rand_indices = np.array([], dtype='int64')  # int64 b/c it stores idx
    for class_idx in range(num_classes):
      class_specific_indices = np.argwhere(
          np.argmax(all_img_y_trues, axis=1) == class_idx
      ).flatten()
      rand_indices = np.random.choice(
          class_specific_indices,
          size=num_samples_per_class[class_idx],
          replace=False,
      )
      all_rand_indices = np.hstack((all_rand_indices, rand_indices))
    batch_img_samples = all_img_samples[all_rand_indices, :, :, :]
    batch_img_y_trues = all_img_y_trues[all_rand_indices, :]

  for idx in tqdm(range(local_num_base_models)):

    model = reset_model_using_weights(model, base_model_weights_final[idx, :])

    if not config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS:
      rand_indices = np.random.choice(
          num_train_samples,
          size=config.NUM_SAMPLES_PER_BASE_MODEL,
          replace=False,
      )
      batch_img_samples = all_img_samples[rand_indices, :, :, :]
      batch_img_y_trues = all_img_y_trues[rand_indices, :]
    predictions = model.predict_on_batch(batch_img_samples)
    # We use `predict_on_batch` instead of `predict` to avoid a memory leak; see
    # github.com/keras-team/keras/issues/13118#issuecomment-541688220

    new_instances_range = range(
        idx * config.NUM_SAMPLES_PER_BASE_MODEL,
        (idx + 1) * config.NUM_SAMPLES_PER_BASE_MODEL
    )

    samples[new_instances_range, :] = np.reshape(
        batch_img_samples,
        (batch_img_samples.shape[0], -1),
    )  # collapse all image dims
    y_preds[new_instances_range, :] = predictions
    y_trues[new_instances_range, :] = batch_img_y_trues

  # weights_chkpt, weights_final, hparams, and metrics are global properties of
  # a model shared for all instances predicted on each model; apply np.repeat
  # outside of loop for efficiency.
  weights_chkpt = np.repeat(
      base_model_weights_chkpt,
      config.NUM_SAMPLES_PER_BASE_MODEL,
      axis=0,
  )
  weights_final = np.repeat(
      base_model_weights_final,
      config.NUM_SAMPLES_PER_BASE_MODEL,
      axis=0,
  )

  hparams = pd.DataFrame(
      np.repeat(
          base_model_metrics[config.ALL_HPARAMS].values,
          config.NUM_SAMPLES_PER_BASE_MODEL,
          axis=0,
      )
  )
  hparams.columns = base_model_metrics[config.ALL_HPARAMS].columns

  metrics = pd.DataFrame(
      np.repeat(
          base_model_metrics[config.ALL_METRICS].values,
          config.NUM_SAMPLES_PER_BASE_MODEL,
          axis=0,
      )
  )
  metrics.columns = base_model_metrics[config.ALL_METRICS].columns

  logging.info('Done.')

  assert isinstance(samples, np.ndarray)
  assert isinstance(y_preds, np.ndarray)
  assert isinstance(y_trues, np.ndarray)
  assert isinstance(hparams, pd.core.frame.DataFrame)
  assert isinstance(weights_chkpt, np.ndarray)
  assert isinstance(weights_final, np.ndarray)
  assert isinstance(metrics, pd.core.frame.DataFrame)
  assert (
      samples.shape[0] ==
      y_preds.shape[0] ==
      y_trues.shape[0] ==
      hparams.shape[0] ==
      weights_chkpt.shape[0] ==
      weights_final.shape[0] ==
      metrics.shape[0]
  )
  return samples, y_preds, y_trues, hparams, weights_chkpt, weights_final, metrics


def process_and_resave_cnn_zoo_data(random_seed, model_wireframe,
                                    covariates_settings):
  """Load weights and matrices from CNN zoo dataset to process for new training.

  Upon loading the data from the CNN zoo, this method feeds the corresponding
  weights and matrices for each epoch (designated in config.py) into the
  `extract_new_covariates_and_targets` method to process the data according for
  the training of the meta-model. The resulting covaritates, targets, and meta
  information is then saved (to be loaded later for meta-model training).

  Args:
    random_seed: the random seed used for reproducibility of results
    model_wireframe: the tf model graph whose weights are then populated from
                     the save weights in the CNN zoo
    covariates_settings: a dictionary specifying the checkpoints at which to
                         extract data from the saved weights/metrics in the zoo
  """

  base_model_weights, base_model_metrics = load_base_model_weights_and_metrics()

  for covariates_setting in covariates_settings:

    chkpt = int(covariates_setting['chkpt'])
    file_suffix = get_file_suffix(chkpt)
    logging.info(
        'Extracting new covariates and targets for chkpt %s @ test acc > %.2f',
        covariates_setting['chkpt'],
        config.KEEP_MODELS_ABOVE_TEST_ACCURACY,
    )

    samples, y_preds, y_trues, hparams, w_chkpt, w_final, metrics = extract_new_covariates_and_targets(
        random_seed,
        model_wireframe,
        other.get_dataset_info(config.dataset),
        covariates_setting,
        base_model_weights,
        base_model_metrics,
    )

    with file_handler(f'samples{file_suffix}.npy', 'wb') as f:
      pickle.dump(samples, f, protocol=4)
    with file_handler(f'y_preds{file_suffix}.npy', 'wb') as f:
      pickle.dump(y_preds, f, protocol=4)
    with file_handler(f'y_trues{file_suffix}.npy', 'wb') as f:
      pickle.dump(y_trues, f, protocol=4)
    with file_handler(f'hparams{file_suffix}.npy', 'wb') as f:
      pickle.dump(hparams, f, protocol=4)
    with file_handler(f'w_chkpt{file_suffix}.npy', 'wb') as f:
      pickle.dump(w_chkpt, f, protocol=4)
    with file_handler(f'w_final{file_suffix}.npy', 'wb') as f:
      pickle.dump(w_final, f, protocol=4)
    with file_handler(f'metrics{file_suffix}.npy', 'wb') as f:
      pickle.dump(metrics, f, protocol=4)

    del samples, y_preds, y_trues, hparams, w_chkpt, w_final, metrics
    logging.info('\tdone.')


def train_meta_model_and_evaluate_results(random_seed, samples, auxvals,
                                          targets, chkpt, train_fraction):
  """Train a meta-model given covariates and targets.

  Args:
    random_seed: the random seed used for reproducibility of results
    samples: samples used to train meta-model; instance of np.ndarray.
    auxvals: additional covariates used to train meta-model (hparams: instance
             of pd.DataFrame; OR weights: instance of np.ndarray)
    targets: the predicted target of samples on each base-model;
             instance of np.ndarray.
    chkpt: the checkpoint of base-model weights used to train the meta-model;
           used for logging and filename for saving training results.
    train_fraction: the fraction of the overall meta-model training set to use.

  Returns:
    train_results: train set results; a tuple with (loss, accuracy) information
    test_results: test set results; a tuple with (loss, accuracy) information
  """

  np.random.RandomState(random_seed)

  logging.debug(
      '%s[Train meta-model @ checkpoint %d on %.3f fraction of train data]%s',
      Bcolors.BOLD, chkpt, train_fraction, Bcolors.ENDC
  )

  if isinstance(auxvals, pd.DataFrame):  # for hparams
    auxvals = auxvals.to_numpy()

  assert isinstance(samples, np.ndarray)
  assert isinstance(auxvals, np.ndarray)
  assert isinstance(targets, np.ndarray)

  # Configuration options
  num_features = samples.shape[1] + auxvals.shape[1]
  num_classes = other.get_dataset_info(config.dataset)['num_classes']

  # Set the input shape.
  input_shape = (num_features,)

  # Split into train/test indices.
  permuted_indices = np.random.permutation(range(len(samples)))
  train_indices = permuted_indices[:int(train_fraction * len(permuted_indices))]
  test_indices = permuted_indices[int(train_fraction * len(permuted_indices)):]

  # Create the meta model architecture.
  model = tf.keras.Sequential()
  model.add(tf.keras.layers.Dense(
      500,
      input_shape=input_shape,
      activation='relu',
  ))
  model.add(tf.keras.layers.Dense(100, activation='relu'))
  model.add(tf.keras.layers.Dense(num_classes, activation='softmax'))

  # Configure the model and start training.
  model.compile(
      loss='categorical_crossentropy',
      optimizer='adam',
      metrics=['accuracy'],
  )

  print_memory_usage()
  logging.info('Preparing data generators...')
  train_covariates = np.concatenate([
      samples[train_indices, :],
      auxvals[train_indices, :],
  ], axis=1)
  test_covariates = np.concatenate([
      samples[test_indices, :],
      auxvals[test_indices, :],
  ], axis=1)
  train_targets = targets[train_indices, :]
  test_targets = targets[test_indices, :]
  print_memory_usage()

  logging.info('Commencing training...')
  print_memory_usage()
  model.fit(
      train_covariates,
      train_targets,
      epochs=config.META_MODEL_EPOCHS,
      batch_size=config.META_MODEL_BATCH_SIZE,
      verbose=0,
      validation_split=0.1)
  logging.info('Training finished.')
  print_memory_usage()
  logging.info('Saving model.')
  model_file_name = (
      'model_weights'
      f'_min_acc_{config.KEEP_MODELS_ABOVE_TEST_ACCURACY}'
      f'_chkpt_{chkpt}'
      f'_train_fraction_{train_fraction}'
  )
  model.save(os.path.join(config.MODELS_FOLDER_PATH, model_file_name))
  print_memory_usage()
  logging.info('Evaluating on train/test sets... ')
  print_memory_usage()
  logging.info('\tEvaluate train set] ...')
  train_results = model.evaluate(train_covariates, train_targets, verbose=1)
  logging.info('\tEvaluate test set] ...')
  test_results = model.evaluate(test_covariates, test_targets, verbose=1)
  logging.info(
      'Train acc/loss: %%%.3f / %.3f', train_results[1] * 100, train_results[0]
  )
  logging.info(
      'Test acc/loss: %%%.3f / %.3f', test_results[1] * 100, test_results[0]
  )
  print_memory_usage()
  logging.debug('Deleting files of size:')
  logging.debug(
      '\ttrain_covariates: %.4f MB, ',
      sys.getsizeof(train_covariates) / 1024 / 1024,
  )
  logging.debug(
      '\ttest_covariates: %.4f MB, ',
      sys.getsizeof(test_covariates) / 1024 / 1024,
  )
  logging.debug(
      '\ttrain_targets: %.4f MB, ', sys.getsizeof(train_targets) / 1024 / 1024
  )
  logging.debug(
      '\ttest_targets: %.4f MB, ', sys.getsizeof(test_targets) / 1024 / 1024
  )
  del train_covariates, test_covariates, train_targets, test_targets
  print_memory_usage()
  logging.debug('Collecting garbage...')
  gc.collect()  # still needed to clear some other unused objects
  print_memory_usage()

  return train_results, test_results


def train_meta_model_over_different_setups(random_seed):
  """Train many meta-models over various training setups.

  The primary purpose of this method is to train a series of meta-models that
  can predict the predictions of underlying base-models trained on different
  hparam settings. Essentially, the aim of the meta-model is to emulate the
  post-training predictions of an entire class of base-models, without needing
  to fully train the base-models. Therefore, the covariates used in the training
  of the meta-model are a combination of either X, H, i.e., samples and hparams,
  or X, W_@_epoch, i.e., samples and weights of the base-model @ epoch < 86. The
  final epoch in this CNN zoo is set to 86. The targets of the meta-model are
  always set to be the predictions of the meta model at epoch 86.

  Besides training the meta-model on different covariate combinations, we also
  iterate over different splits of instances in the train/test sets. The total
  number of instances in the meta-model training set is the product of the
  number of base models and the number of samples (images) per base model. From
  this product, a train_fraction fraction of them are chosen to comprise the
  train set and the remainder are used for evaluation.

  config.py keeps track of all setups on which the meta-model is trained. After
  the training of each setup, the train and test accuracy are saved to file to
  be processed and displayed later in aggregate.

  Args:
      random_seed: the random seed used for reproducibility of results
  """
  assert not config.USE_IDENTICAL_SAMPLES_OVER_BASE_MODELS
  all_results = pd.DataFrame({
      'chkpt': [],
      'train_fraction': [],
      'train_accuracy': [],
      'test_accuracy': [],
  })

  # Train a meta-model on the following covariates and targets:
  # Covariates:
  #   X_@_86: samples at epoch 86.
  #   H_@_-1: hparams (epoch -1 means before training; hparams remain constant).
  # Target:
  #   Y_@_86: targets at epoch 86.
  chkpt = 86
  file_suffix = get_file_suffix(chkpt)
  with file_handler(f'samples{file_suffix}.npy', 'rb') as f:
    samples = pickle.load(f)
  with file_handler(f'y_preds{file_suffix}.npy', 'rb') as f:
    y_preds = pickle.load(f)
  with file_handler(f'hparams{file_suffix}.npy', 'rb') as f:
    hparams = pickle.load(f)

  # Convert numerical columns to float values (s/dtype=Object/dtype=float32).
  for col in config.NUM_HPARAMS:
    hparams[col] = hparams[col].astype('float32')

  # Convert categorical columns to numerical values for training meta_model.
  for hparam in config.CAT_HPARAMS:
    hparams[hparam] = pd.Categorical(hparams[hparam]).codes
  hparams = hparams.to_numpy().astype('float32')

  for train_fraction in config.TRAIN_FRACTIONS:

    chkpt = -1
    train_results, test_results = train_meta_model_and_evaluate_results(
        random_seed,
        samples,
        hparams,
        y_preds,
        chkpt,
        train_fraction,
    )

    all_results = all_results.append(
        {
            'chkpt': chkpt,  # hparams
            'train_fraction': train_fraction,
            'train_accuracy': train_results[1],
            'test_accuracy': test_results[1],
        },
        ignore_index=True)

    with file_handler('all_results.npy', 'wb') as f:
      pickle.dump(all_results, f, protocol=4)

  # Save memory; if this fails, use DataGenerator; source:
  # https://stanford.edu/~shervine/blog/keras-how-to-generate-data-on-the-fly
  del samples, y_preds, hparams

  # Train a meta-model on the following covariates and targets:
  # Covariates:
  #   X_@_i:  samples at epoch i.
  #   W_@_i:  weights at epoch i.
  # Target:
  #   Y_@_86: targets at epoch 86. (Remindere: we want to predict final network
  #                                 performance from intermediary weights.)
  for covariates_setting in config.COVARIATES_SETTINGS:

    chkpt = int(covariates_setting['chkpt'])
    file_suffix = get_file_suffix(chkpt)
    with file_handler(f'samples{file_suffix}.npy', 'rb') as f:
      samples = pickle.load(f)
    with file_handler(f'y_preds{file_suffix}.npy', 'rb') as f:
      y_preds = pickle.load(f)
    with file_handler(f'w_chkpt{file_suffix}.npy', 'rb') as f:
      w_chkpt = pickle.load(f)
    with file_handler(f'w_final{file_suffix}.npy', 'rb') as f:
      w_final = pickle.load(f)

    # Sanity check: make sure the random permutations
    # performed on the various saved files are similar.
    # Do NOT use w_chkpt below; y_pred is computed/saved using w_final.
    m = reset_model_using_weights(
        other.get_model_wireframe(config.dataset),
        w_final[0],
    )
    s = samples[0].reshape(
        (1,) + other.get_dataset_info(config.dataset)['data_shape']
    )
    y = y_preds[0]
    assert np.allclose(m.predict_on_batch(s)[0], y, rtol=1e-2)

    for train_fraction in config.TRAIN_FRACTIONS:

      train_results, test_results = train_meta_model_and_evaluate_results(
          random_seed,
          samples,
          w_chkpt,
          y_preds,
          chkpt,
          train_fraction,
      )

      all_results = all_results.append(
          {
              'chkpt': covariates_setting['chkpt'],
              'train_fraction': train_fraction,
              'train_accuracy': train_results[1],
              'test_accuracy': test_results[1],
          },
          ignore_index=True)
      with file_handler('all_results.npy', 'wb') as f:
        pickle.dump(all_results, f, protocol=4)

    # Save memory; if this fails, use DataGenerator; source:
    # https://stanford.edu/~shervine/blog/keras-how-to-generate-data-on-the-fly
    del samples, y_preds, w_chkpt, w_final


def save_heat_map_of_meta_model_results():
  """Plot and save a heatmap of results of training meta-models."""
  with file_handler('all_results.npy', 'rb') as f:
    all_results = pickle.load(f)
  train_results = all_results.pivot('train_fraction', 'chkpt', 'train_accuracy')
  test_results = all_results.pivot('train_fraction', 'chkpt', 'test_accuracy')

  fig, (ax1, ax2) = plt.subplots(1, 2, sharey=True, figsize=(18, 6))
  sns.heatmap(train_results, annot=True, fmt='.2f', ax=ax1)
  sns.heatmap(test_results, annot=True, fmt='.2f', ax=ax2)
  ax1.set_title(
      'train_results (smaller train_fraction = more overfit = higher perf)'
  )
  ax2.set_title(
      'test_results (larger train_fraction = less overfit = higher perf)'
  )
  plt.tight_layout()
  fig.savefig(
      gfile.GFile(
          os.path.join(
              config.PLOTS_FOLDER_PATH,
              'heatmap_results_for_meta_model.png',
          ),
          'wb',
      ),
      dpi=400,
  )
