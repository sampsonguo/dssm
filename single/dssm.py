import pickle
import random
import time
import sys
import numpy as np
import tensorflow as tf
from numpy import array
from scipy.sparse import coo_matrix

flags = tf.app.flags
FLAGS = flags.FLAGS

# model_log_dir
flags.DEFINE_string('summaries_dir', '/tmp/dssm-400-120-relu', 'Summaries directory')

# Learning_rate
flags.DEFINE_float('learning_rate', 0.1, 'Initial learning rate.')

# Max_steps
flags.DEFINE_integer('max_steps', 900000, 'Number of steps to run trainer.')

# Epoch Steps???
flags.DEFINE_integer('epoch_steps', 18000, "Number of steps in one epoch.")

# pickle???
flags.DEFINE_integer('pack_size', 2, "Number of batches in one pickle pack.")

# GPU or Not
flags.DEFINE_bool('gpu', 1, "Enable GPU or not")

# Timestamp
start = time.time()

# Train Data: Doc & Query
doc_train_data = None
query_train_data = None

# Row, Col, Data -> doc_train_data
row  = array([0,0,1,1,1,0,0])
col  = array([0,1,1,1,1,0,0])
data = array([1,1,1,1,1,1,1])
doc_train_data = coo_matrix((data,(row,col)), shape=(2,49284)).tocsr()
print(doc_train_data.shape)
print(type(doc_train_data))
query_train_data = doc_train_data

# doc_data same as query_data
query_test_data = query_train_data
doc_test_data = doc_train_data

print(doc_train_data)

# Load data?
def load_train_data(pack_idx):
    global doc_train_data, query_train_data
    start = time.time()
    end = time.time()
    print ("\nTrain data %d/9 is loaded in %.2fs" % (pack_idx, end - start))

end = time.time()
print("Loading data from HDD to memory: %.2fs" % (end - start))

# Trigram
TRIGRAM_D = 49284

# Neg
NEG = 50
BS = 2

L1_N = 400
L2_N = 120

# Input Shape
query_in_shape = np.array([BS, TRIGRAM_D], np.int64)
doc_in_shape = np.array([BS, TRIGRAM_D], np.int64)

print("query in shape is %s" % query_in_shape.shape)

# Summaries for tf board for multi variables
def variable_summaries(var, name):
    """Attach a lot of summaries to a Tensor."""
    with tf.name_scope('summaries'):
        mean = tf.reduce_mean(var)
        tf.summary.scalar('mean/' + name, mean)
        with tf.name_scope('stddev'):
            stddev = tf.sqrt(tf.reduce_sum(tf.square(var - mean)))
        tf.summary.scalar('sttdev/' + name, stddev)
        tf.summary.scalar('max/' + name, tf.reduce_max(var))
        tf.summary.scalar('min/' + name, tf.reduce_min(var))
        tf.summary.histogram(name, var)

# Input Format
with tf.name_scope('input'):
    # Shape [BS, TRIGRAM_D].
    query_batch = tf.sparse_placeholder(tf.float32, shape=query_in_shape, name='QueryBatch')
    # Shape [BS, TRIGRAM_D]
    doc_batch = tf.sparse_placeholder(tf.float32, shape=doc_in_shape, name='DocBatch')

# L1 Layer
with tf.name_scope('L1'):
    # Norm
    l1_par_range = np.sqrt(6.0 / (TRIGRAM_D + L1_N))
    # Random Init weights1
    weight1 = tf.Variable(tf.random_uniform([TRIGRAM_D, L1_N], -l1_par_range, l1_par_range))
    # Random Init Bias1
    bias1 = tf.Variable(tf.random_uniform([L1_N], -l1_par_range, l1_par_range))
    variable_summaries(weight1, 'L1_weights')
    variable_summaries(bias1, 'L1_biases')

    # Query_Batch * Weight + Bias
    # query_l1 = tf.matmul(tf.to_float(query_batch),weight1)+bias1
    query_l1 = tf.sparse_tensor_dense_matmul(query_batch, weight1) + bias1

    # Doc_Batch * Weight + Bias, Same Weight
    # doc_l1 = tf.matmul(tf.to_float(doc_batch),weight1)+bias1
    doc_l1 = tf.sparse_tensor_dense_matmul(doc_batch, weight1) + bias1

    # Relu Activation
    query_l1_out = tf.nn.relu(query_l1)
    doc_l1_out = tf.nn.relu(doc_l1)

# L2 Layer
with tf.name_scope('L2'):
   
    # Weight Init, xavier_initializer
    l2_par_range = np.sqrt(6.0 / (L1_N + L2_N))


    # Xavier Initializer
    weight2 = tf.Variable(tf.random_uniform([L1_N, L2_N], -l2_par_range, l2_par_range))
    bias2 = tf.Variable(tf.random_uniform([L2_N], -l2_par_range, l2_par_range))
    variable_summaries(weight2, 'L2_weights')
    variable_summaries(bias2, 'L2_biases')

    # Qeury * Weight
    query_l2 = tf.matmul(query_l1_out, weight2) + bias2
    doc_l2 = tf.matmul(doc_l1_out, weight2) + bias2
    query_y = tf.nn.relu(query_l2)
    doc_y = tf.nn.relu(doc_l2)

# FD rotate
with tf.name_scope('FD_rotate'):
    # Rotate FD+ to produce 50 FD-
    temp = tf.tile(doc_y, [1, 1])

    # ?
    for i in range(NEG):
        rand = int((random.random() + i) * BS / NEG)
        doc_y = tf.concat([doc_y,
                           tf.slice(temp, [rand, 0], [BS - rand, -1]),
                           tf.slice(temp, [0, 0], [rand, -1])], 0)

# Cosine Sim
with tf.name_scope('Cosine_Similarity'):
    # Cosine similarity
    query_norm = tf.tile(tf.sqrt(tf.reduce_sum(tf.square(query_y), 1, True)), [NEG + 1, 1])
    doc_norm = tf.sqrt(tf.reduce_sum(tf.square(doc_y), 1, True))

    prod = tf.reduce_sum(tf.multiply(tf.tile(query_y, [NEG + 1, 1]), doc_y), 1, True)
    norm_prod = tf.multiply(query_norm, doc_norm)

    # x/y elementwise
    cos_sim_raw = tf.truediv(prod, norm_prod)
    cos_sim = tf.transpose(tf.reshape(tf.transpose(cos_sim_raw), [NEG + 1, BS])) * 20

# Loss Calulation
with tf.name_scope('Loss'):
    # Train Loss
    prob = tf.nn.softmax((cos_sim))
    hit_prob = tf.slice(prob, [0, 0], [-1, 1])
    loss = -tf.reduce_sum(tf.log(hit_prob)) / BS
    tf.summary.scalar('loss', loss)

with tf.name_scope('Training'):
    # Optimizer
    train_step = tf.train.GradientDescentOptimizer(FLAGS.learning_rate).minimize(loss)

merged = tf.summary.merge_all()

# Test Data
with tf.name_scope('Test'):
    average_loss = tf.placeholder(tf.float32)
    loss_summary = tf.summary.scalar('average_loss', average_loss)

# Batch Data
def pull_batch(query_data, doc_data, batch_idx):
    # start = time.time()
    query_in = query_data[batch_idx * BS:(batch_idx + 1) * BS, :]
    doc_in = doc_data[batch_idx * BS:(batch_idx + 1) * BS, :]
    query_in = query_in.tocoo()
    doc_in = doc_in.tocoo()

    query_in = tf.SparseTensorValue(
        np.transpose([np.array(query_in.row, dtype=np.int64), np.array(query_in.col, dtype=np.int64)]),
        np.array(query_in.data, dtype=np.float),
        np.array(query_in.shape, dtype=np.int64))

    doc_in = tf.SparseTensorValue(
        np.transpose([np.array(doc_in.row, dtype=np.int64), np.array(doc_in.col, dtype=np.int64)]),
        np.array(doc_in.data, dtype=np.float),
        np.array(doc_in.shape, dtype=np.int64))

    # end = time.time()
    # print("Pull_batch time: %f" % (end - start))

    return query_in, doc_in


def feed_dict(Train, batch_idx):
    """Make a TensorFlow feed_dict: maps data onto Tensor placeholders."""
    if Train:
        query_in, doc_in = pull_batch(query_train_data, doc_train_data, batch_idx)
    else:
        query_in, doc_in = pull_batch(query_test_data, doc_test_data, batch_idx)
    print("=====")
    print(query_in)
    print(doc_in)
    return {query_batch: query_in, doc_batch: doc_in}


config = tf.ConfigProto()  # log_device_placement=True)
config.gpu_options.allow_growth = True

# Entrance
with tf.Session(config=config) as sess:

    print("Here 1 -------------------")
    sess.run(tf.global_variables_initializer())
    train_writer = tf.summary.FileWriter(FLAGS.summaries_dir + '/train', sess.graph)
    test_writer = tf.summary.FileWriter(FLAGS.summaries_dir + '/test', sess.graph)

    # Actual execution
    start = time.time()
    
    for step in range(FLAGS.max_steps):
        batch_idx = step % FLAGS.epoch_steps
        if batch_idx % FLAGS.pack_size == 0:
            load_train_data(batch_idx / FLAGS.pack_size + 1)

        # Print the Progress
        #if batch_idx % (FLAGS.pack_size / 64) == 0:
        #    progress = 100.0 * batch_idx / FLAGS.epoch_steps
        #    sys.stdout.write("\r%.2f%% Epoch" % progress)
        #    sys.stdout.flush()

        # Run
        print("here 2 -----")
        print("batch idx is %s" % batch_idx)
        print("pack size is %d" % FLAGS.pack_size)
        sess.run(train_step, feed_dict=feed_dict(True, batch_idx % FLAGS.pack_size))

        # Batch Size
        if batch_idx == FLAGS.epoch_steps - 1:
            end = time.time()
            epoch_loss = 0
            for i in range(FLAGS.pack_size):
                loss_v = sess.run(loss, feed_dict=feed_dict(True, i))
                epoch_loss += loss_v

            epoch_loss /= FLAGS.pack_size
            train_loss = sess.run(loss_summary, feed_dict={average_loss: epoch_loss})
            train_writer.add_summary(train_loss, step + 1)

            print ("\nEpoch #%-5d | Train Loss: %-4.3f | PureTrainTime: %-3.3fs" %
                    (step / FLAGS.epoch_steps, epoch_loss, end - start))

            epoch_loss = 0
            for i in range(FLAGS.pack_size):
                loss_v = sess.run(loss, feed_dict=feed_dict(False, i))
                epoch_loss += loss_v

            epoch_loss /= FLAGS.pack_size

            test_loss = sess.run(loss_summary, feed_dict={average_loss: epoch_loss})
            test_writer.add_summary(test_loss, step + 1)

            start = time.time()
            print ("Epoch #%-5d | Test  Loss: %-4.3f | Calc_LossTime: %-3.3fs" %
                   (step / FLAGS.epoch_steps, epoch_loss, start - end))

