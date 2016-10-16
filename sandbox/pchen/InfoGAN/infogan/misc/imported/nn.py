import warnings
import numpy as np
import tensorflow as tf
from tensorflow.python.framework import ops
from tensorflow.python.ops import control_flow_ops

import sandbox.pchen.InfoGAN.infogan.misc.imported.scopes as scopes

def concat_elu(x):
    return tf.concat(3, [tf.nn.elu(x), tf.nn.elu(-x)])

def int_shape(x):
    s = x.get_shape()
    return [int(si) for si in s]

def log_sum_exp(x):
    axis = len(x.get_shape())-1
    m = tf.reduce_max(x, axis)
    m2 = tf.reduce_max(x, axis, keep_dims=True)
    return m + tf.log(tf.reduce_sum(tf.exp(x-m2), axis))

def log_prob_from_softmax(x):
    axis = len(x.get_shape())-1
    m = tf.reduce_max(x, axis, keep_dims=True)
    return x - m - tf.log(tf.reduce_sum(tf.exp(x-m), axis, keep_dims=True))

def discretized_mix_logistic(x,l):
    xs = int_shape(x)
    ls = int_shape(l)
    nr_mix = ls[-1] // 10
    logit_probs = l[:,:,:,:nr_mix]
    l = tf.reshape(l[:,:,:,nr_mix:], xs + [nr_mix*3])
    means = l[:,:,:,:,:nr_mix]
    log_scales = tf.maximum(l[:,:,:,:,nr_mix:2*nr_mix], -7.)
    coeffs = tf.nn.tanh(l[:,:,:,:,2*nr_mix:3*nr_mix])
    x = tf.reshape(x, xs + [1]) + tf.zeros(xs + [nr_mix])
    m2 = tf.reshape(means[:,:,:,1,:] + coeffs[:, :, :, 0, :] * x[:, :, :, 0, :], [xs[0],xs[1],xs[2],1,nr_mix])
    m3 = tf.reshape(means[:, :, :, 2, :] + coeffs[:, :, :, 1, :] * x[:, :, :, 0, :] + coeffs[:, :, :, 2, :] * x[:, :, :, 1, :], [xs[0],xs[1],xs[2],1,nr_mix])
    means = tf.concat(3,[tf.reshape(means[:,:,:,0,:], [xs[0],xs[1],xs[2],1,nr_mix]), m2, m3])
    centered_x = x - means
    inv_stdv = tf.exp(-log_scales)
    plus_in = inv_stdv * (centered_x + 1./255.)
    cdf_plus = tf.nn.sigmoid(plus_in)
    min_in = inv_stdv * (centered_x - 1./255.)
    cdf_min = tf.nn.sigmoid(min_in)
    log_cdf_plus = plus_in - tf.nn.softplus(plus_in)
    log_one_minus_cdf_min = -tf.nn.softplus(min_in)
    cdf_delta = cdf_plus - cdf_min
    mid_in = inv_stdv * centered_x
    log_pdf_mid = -mid_in - log_scales - 2.*tf.nn.softplus(-mid_in)
    log_probs = tf.select(
        x < -0.999,
        log_cdf_plus,
        tf.select(
            x > 0.999,
            log_one_minus_cdf_min,
            tf.select(
                cdf_delta > 1e-3,
                tf.log(cdf_delta + 1e-7),
                log_pdf_mid - np.log(127.5)
            )
        )
    )
    log_probs = tf.reduce_sum(log_probs,3) + log_prob_from_softmax(logit_probs)
    # return tf.reduce_sum(log_sum_exp(log_probs))
    # return for each location instead
    return (log_sum_exp(log_probs))

def sample_from_discretized_mix_logistic(l,nr_mix):
    ls = int_shape(l)
    xs = ls[:-1] + [3]
    logit_probs = l[:, :, :, :nr_mix]
    l = tf.reshape(l[:, :, :, nr_mix:], xs + [nr_mix*3])
    sel = tf.one_hot(tf.argmax(logit_probs - tf.log(-tf.log(tf.random_uniform(logit_probs.get_shape(), minval=1e-5, maxval=1. - 1e-5))), 3), depth=nr_mix, dtype=tf.float32) # sample from softmax
    sel = tf.reshape(sel, xs[:-1] + [1,nr_mix])
    means = tf.reduce_sum(l[:,:,:,:,:nr_mix]*sel,4)
    log_scales = tf.maximum(tf.reduce_sum(l[:,:,:,:,nr_mix:2*nr_mix]*sel,4), -7.)
    coeffs = tf.reduce_sum(tf.nn.tanh(l[:,:,:,:,2*nr_mix:3*nr_mix])*sel,4)
    u = tf.random_uniform(means.get_shape(), minval=1e-5, maxval=1. - 1e-5)
    x = means + tf.exp(log_scales)*(tf.log(u) - tf.log(1. - u))
    x0 = tf.minimum(tf.maximum(x[:,:,:,0], -1.), 1.)
    x1 = tf.minimum(tf.maximum(x[:,:,:,1] + coeffs[:,:,:,0]*x0, -1.), 1.)
    x2 = tf.minimum(tf.maximum(x[:,:,:,2] + coeffs[:,:,:,1]*x0 + coeffs[:,:,:,2]*x1, -1.), 1.)
    return tf.concat(3,[tf.reshape(x0,xs[:-1]+[1]), tf.reshape(x1,xs[:-1]+[1]), tf.reshape(x2,xs[:-1]+[1])])

def get_var_maybe_avg(var_name, ema, **kwargs):
    v = tf.get_variable(var_name, **kwargs)
    if ema is not None:
        v = ema.average(v)
    return v

def get_vars_maybe_avg(var_names, ema, **kwargs):
    vars = []
    for vn in var_names:
        vars.append(get_var_maybe_avg(vn, ema, **kwargs))
    return vars

def adamax_updates(params, cost_or_grads, lr=0.001, mom1=0.9, mom2=0.999):
    updates = []
    if type(cost_or_grads) is not list:
        grads = tf.gradients(cost_or_grads, params)
    else:
        grads = cost_or_grads
    for p, g in zip(params, grads):
        mg = tf.Variable(tf.zeros(p.get_shape()), p.name + '_adamax_mg')
        if mom1>0:
            v = tf.Variable(tf.zeros(p.get_shape()), p.name + '_adamax_v')
            v_t = mom1*v + (1. - mom1)*g
            updates.append(v.assign(v_t))
        else:
            v_t = g
        mg_t = tf.maximum(mom2*mg + 1e-8, tf.abs(g))
        g_t = v_t / mg_t
        p_t = p - lr * g_t
        updates.append(mg.assign(mg_t))
        updates.append(p.assign(p_t))
    return control_flow_ops.group(*updates)

def adam_updates(params, cost_or_grads, lr=0.001, mom1=0.9, mom2=0.999):
    updates = []
    if type(cost_or_grads) is not list:
        grads = tf.gradients(cost_or_grads, params)
    else:
        grads = cost_or_grads
    t = tf.Variable(1., 'adam_t')
    for p, g in zip(params, grads):
        mg = tf.Variable(tf.zeros(p.get_shape()), p.name + '_adam_mg')
        if mom1>0:
            v = tf.Variable(tf.zeros(p.get_shape()), p.name + '_adam_v')
            v_t = mom1*v + (1. - mom1)*g
            v_hat = v_t / (1. - tf.pow(mom1,t))
            updates.append(v.assign(v_t))
        else:
            v_hat = g
        mg_t = mom2*mg + (1. - mom2)*tf.square(g)
        mg_hat = mg_t / (1. - tf.pow(mom2,t))
        g_t = v_hat / tf.sqrt(mg_hat + 1e-8)
        p_t = p - lr * g_t
        updates.append(mg.assign(mg_t))
        updates.append(p.assign(p_t))
    updates.append(t.assign_add(1))
    return control_flow_ops.group(*updates)

def get_name(layer_name, counters):
    if not layer_name in counters:
        counters[layer_name] = 0
    name = layer_name + '_' + str(counters[layer_name])
    counters[layer_name] += 1
    return name

@scopes.add_arg_scope
def dense(x, num_units, nonlinearity=None, init_scale=1., counters={}, init=False, ema=None, **kwargs):
    name = get_name('dense', counters)
    with tf.variable_scope(name):
        if init:
            # data based initialization of parameters
            V = tf.get_variable('V', [int(x.get_shape()[1]),num_units], tf.float32, tf.random_normal_initializer(0, 0.05), trainable=True)
            V_norm = tf.nn.l2_normalize(V.initialized_value(), [0])
            x_init = tf.matmul(x, V_norm)
            m_init, v_init = tf.nn.moments(x_init, [0])
            scale_init = init_scale/tf.sqrt(v_init + 1e-10)
            g = tf.get_variable('g', dtype=tf.float32, initializer=scale_init, trainable=True)
            b = tf.get_variable('b', dtype=tf.float32, initializer=-m_init*scale_init, trainable=True)
            x_init = tf.reshape(scale_init,[1,num_units])*(x_init-tf.reshape(m_init,[1,num_units]))
            if nonlinearity is not None:
                x_init = nonlinearity(x_init)
            return x_init

        else:
            V,g,b = get_vars_maybe_avg(['V','g','b'], ema)
            tf.assert_variables_initialized([V,g,b])

            # use weight normalization (Salimans & Kingma, 2016)
            x = tf.matmul(x, V)
            scaler = g/tf.sqrt(tf.reduce_sum(tf.square(V),[0]))
            x = tf.reshape(scaler,[1,num_units])*x + tf.reshape(b,[1,num_units])

            # apply nonlinearity
            if nonlinearity is not None:
                x = nonlinearity(x)
            return x

@scopes.add_arg_scope
def conv2d(x, num_filters, filter_size=[3,3], stride=[1,1], pad='SAME', nonlinearity=None, init_scale=1., counters={}, init=False, ema=None, **kwargs):
    name = get_name('conv2d', counters)
    with tf.variable_scope(name):
        if init:
            # data based initialization of parameters
            V = tf.get_variable('V', filter_size+[int(x.get_shape()[-1]),num_filters], tf.float32, tf.random_normal_initializer(0, 0.05), trainable=True)
            V_norm = tf.nn.l2_normalize(V.initialized_value(), [0,1,2])
            x_init = tf.nn.conv2d(x, V_norm, [1]+stride+[1], pad)
            m_init, v_init = tf.nn.moments(x_init, [0,1,2])
            scale_init = init_scale/tf.sqrt(v_init + 1e-8)
            g = tf.get_variable('g', dtype=tf.float32, initializer=scale_init, trainable=True)
            b = tf.get_variable('b', dtype=tf.float32, initializer=-m_init*scale_init, trainable=True)
            x_init = tf.reshape(scale_init,[1,1,1,num_filters])*(x_init-tf.reshape(m_init,[1,1,1,num_filters]))
            if nonlinearity is not None:
                x_init = nonlinearity(x_init)
            return x_init

        else:
            V, g, b = get_vars_maybe_avg(['V', 'g', 'b'], ema)
            tf.assert_variables_initialized([V,g,b])

            # use weight normalization (Salimans & Kingma, 2016)
            W = tf.reshape(g,[1,1,1,num_filters])*tf.nn.l2_normalize(V,[0,1,2])

            # calculate convolutional layer output
            x = tf.nn.bias_add(tf.nn.conv2d(x, W, [1]+stride+[1], pad), b)

            # apply nonlinearity
            if nonlinearity is not None:
                x = nonlinearity(x)
            return x

@scopes.add_arg_scope
def deconv2d(x, num_filters, filter_size=[3,3], stride=[1,1], pad='SAME', nonlinearity=None, init_scale=1., counters={}, init=False, ema=None, **kwargs):
    name = get_name('deconv2d', counters)
    xs = int_shape(x)
    if pad=='SAME':
        target_shape = [xs[0], xs[1]*stride[0], xs[2]*stride[1], num_filters]
    else:
        target_shape = [xs[0], xs[1]*stride[0] + filter_size[0]-1, xs[2]*stride[1] + filter_size[1]-1, num_filters]
    with tf.variable_scope(name):
        if init:
            # data based initialization of parameters
            V = tf.get_variable('V', filter_size+[num_filters,int(x.get_shape()[-1])], tf.float32, tf.random_normal_initializer(0, 0.05), trainable=True)
            V_norm = tf.nn.l2_normalize(V.initialized_value(), [0,1,3])
            x_init = tf.nn.conv2d_transpose(x, V_norm, target_shape, [1]+stride+[1], padding=pad)
            m_init, v_init = tf.nn.moments(x_init, [0,1,2])
            scale_init = init_scale/tf.sqrt(v_init + 1e-8)
            g = tf.get_variable('g', dtype=tf.float32, initializer=scale_init, trainable=True)
            b = tf.get_variable('b', dtype=tf.float32, initializer=-m_init*scale_init, trainable=True)
            x_init = tf.reshape(scale_init,[1,1,1,num_filters])*(x_init-tf.reshape(m_init,[1,1,1,num_filters]))
            if nonlinearity is not None:
                x_init = nonlinearity(x_init)
            return x_init

        else:
            V, g, b = get_vars_maybe_avg(['V', 'g', 'b'], ema)
            tf.assert_variables_initialized([V,g,b])

            # use weight normalization (Salimans & Kingma, 2016)
            W = tf.reshape(g,[1,1,num_filters,1])*tf.nn.l2_normalize(V,[0,1,3])

            # calculate convolutional layer output
            x = tf.nn.conv2d_transpose(x, W, target_shape, [1]+stride+[1], padding=pad)
            x = tf.nn.bias_add(x, b)

            # apply nonlinearity
            if nonlinearity is not None:
                x = nonlinearity(x)
            return x

@scopes.add_arg_scope
def nin(x, num_units, **kwargs):
    s = int_shape(x)
    x = tf.reshape(x, [np.prod(s[:-1]),s[-1]])
    x = dense(x, num_units, **kwargs)
    return tf.reshape(x, s[:-1]+[num_units])

@scopes.add_arg_scope
def resnet(x, nonlinearity=concat_elu, conv=conv2d, **kwargs):
    num_filters = int(x.get_shape()[-1])
    c1 = conv(nonlinearity(x), num_filters, nonlinearity=nonlinearity, **kwargs)
    c2 = nin(c1, num_filters, nonlinearity=None, init_scale=0.1, **kwargs)
    return x+c2

@scopes.add_arg_scope
def gated_resnet(x, nonlinearity=concat_elu, conv=conv2d, **kwargs):
    num_filters = int(x.get_shape()[-1])
    c1 = conv(nonlinearity(x), num_filters, nonlinearity=nonlinearity, **kwargs)
    c2 = nin(c1, num_filters*2, nonlinearity=None, init_scale=0.1, **kwargs)
    c3 = c2[:,:,:,:num_filters] * tf.nn.sigmoid(c2[:,:,:,num_filters:])
    return x+c3

@scopes.add_arg_scope
def gated_resnet(x, nonlinearity=concat_elu, conv=conv2d, **kwargs):
    num_filters = int(x.get_shape()[-1])
    c1 = conv(nonlinearity(x), num_filters, nonlinearity=nonlinearity, **kwargs)
    c2 = nin(c1, num_filters*2, nonlinearity=None, init_scale=0.1, **kwargs)
    c3 = c2[:,:,:,:num_filters] * tf.nn.sigmoid(c2[:,:,:,num_filters:])
    return x+c3

@scopes.add_arg_scope
def aux_gated_resnet(x, u, nonlinearity=concat_elu, conv=conv2d, **kwargs):
    num_filters = int(x.get_shape()[-1])
    c1 = conv(nonlinearity(x), num_filters, nonlinearity=None, **kwargs) + nin(nonlinearity(u), num_filters, nonlinearity=None, **kwargs)
    c2 = nin(nonlinearity(c1), num_filters*2, nonlinearity=None, init_scale=0.1, **kwargs)
    c3 = c2[:,:,:,:num_filters] * tf.nn.sigmoid(c2[:,:,:,num_filters:])
    return x+c3

def down_shift(x):
    xs = int_shape(x)
    return tf.concat(1,[tf.zeros([xs[0],1,xs[2],xs[3]]), x[:,:xs[1]-1,:,:]])

def right_shift(x):
    xs = int_shape(x)
    return tf.concat(2,[tf.zeros([xs[0],xs[1],1,xs[3]]), x[:,:,:xs[2]-1,:]])

@scopes.add_arg_scope
def down_shifted_conv2d(x, num_filters, filter_size=[2,3], stride=[1,1], **kwargs):
    x = tf.pad(x, [[0,0],[filter_size[0]-1,0], [(filter_size[1]-1)//2,(filter_size[1]-1)//2],[0,0]])
    return conv2d(x, num_filters, filter_size=filter_size, pad='VALID', stride=stride, **kwargs)

@scopes.add_arg_scope
def down_shifted_deconv2d(x, num_filters, filter_size=[2,3], stride=[1,1], **kwargs):
    x = deconv2d(x, num_filters, filter_size=filter_size, pad='VALID', stride=stride, **kwargs)
    xs = int_shape(x)
    return x[:,:(xs[1]-filter_size[0]+1),(filter_size[1]-1)//2:(xs[2]-(filter_size[1]-1)//2),:]

@scopes.add_arg_scope
def down_right_shifted_conv2d(x, num_filters, filter_size=[2,2], stride=[1,1], **kwargs):
    x = tf.pad(x, [[0,0],[filter_size[0]-1, 0], [filter_size[1]-1, 0],[0,0]])
    return conv2d(x, num_filters, filter_size=filter_size, pad='VALID', stride=stride, **kwargs)

@scopes.add_arg_scope
def down_right_shifted_deconv2d(x, num_filters, filter_size=[2,2], stride=[1,1], **kwargs):
    x = deconv2d(x, num_filters, filter_size=filter_size, pad='VALID', stride=stride, **kwargs)
    xs = int_shape(x)
    return x[:,:(xs[1]-filter_size[0]+1):,:(xs[2]-filter_size[1]+1),:]

@scopes.add_arg_scope
def lstm(x, state=None, num_units=None, num_out=None, nonlinearity=tf.tanh, counters={}, init=False, ema=None, **kwargs):
    if state is not None:
        num_units = int(state[0].get_shape()[1])
        num_out = int(state[1].get_shape()[1])
    xs = int_shape(x)
    if len(xs)==3:
        batch_size = xs[0]
        num_steps = xs[1]
        num_input = xs[2]
        process_single_step = False
    else:
        batch_size = xs[0]
        num_input = xs[1]
        process_single_step = True

    # get params
    name = get_name('lstm', counters)
    with tf.variable_scope(name):
        initial_lstm_c = get_var_maybe_avg('initial_lstm_c', ema, dtype=tf.float32, initializer=tf.zeros(num_units), trainable=True)
        initial_lstm_h = get_var_maybe_avg('initial_lstm_h', ema, dtype=tf.float32, initializer=tf.zeros(num_out), trainable=True)
        V = get_var_maybe_avg('V', ema, shape=[num_input + num_out, 4 * num_units], dtype=tf.float32, initializer=tf.random_normal_initializer(0, 0.05), trainable=True)
        g = get_var_maybe_avg('g', ema, dtype=tf.float32, initializer=tf.ones(4*num_units), trainable=True)
        b = get_var_maybe_avg('b', ema, dtype=tf.float32, initializer=tf.zeros(4*num_units), trainable=True)
        if init:
            initial_lstm_c = initial_lstm_c.initialized_value()
            initial_lstm_h = initial_lstm_h.initialized_value()
            V = V.initialized_value()
            g = g.initialized_value()
            b = b.initialized_value()
        scaler = g / tf.sqrt(tf.reduce_sum(tf.square(V), [0]))

    # get state
    if state is None:
        state = (tf.reshape(initial_lstm_c, [1, num_units]) + tf.zeros([batch_size,num_units]),
                 tf.reshape(initial_lstm_h, [1, num_out]) + tf.zeros([batch_size, num_out]))

    # single step of LSTM
    lstm_c, lstm_h = state
    if process_single_step:
        x_combined = tf.concat(1, [x,lstm_h])
        y = tf.matmul(x_combined, V)
        y = tf.reshape(scaler,[1,4*num_units])*y + tf.reshape(b,[1,4*num_units])
        i, f, o, pc = tf.split(1, 4, y)
        lstm_c = tf.nn.sigmoid(f)*lstm_c + tf.nn.sigmoid(i)*nonlinearity(pc)
        lstm_h = tf.nn.sigmoid(o)*nonlinearity(lstm_c)

        return lstm_c, lstm_h

    else:
        lstm_h_list = []
        W = V * tf.reshape(scaler,[1,4*num_units])
        W_x = W[:num_input,:]
        W_h = W[num_input:,:]
        x_times_W = tf.reshape(tf.matmul(tf.reshape(x,[xs[0]*xs[1],xs[2]]),W_x) + tf.reshape(b, [1, 4 * num_units]), [xs[0], xs[1], 4*num_units])

        for t in range(num_steps):
            h_times_W = tf.matmul(lstm_h, W_h)
            i, f, o, pc = tf.split(1, 4, h_times_W + x_times_W[:,t,:])
            lstm_c = tf.nn.sigmoid(f) * lstm_c + tf.nn.sigmoid(i) * nonlinearity(pc)
            lstm_h = tf.nn.sigmoid(o) * lstm_c
            lstm_h_list.append(tf.reshape(lstm_h,[batch_size,1,num_out]))

        return tf.concat(1,lstm_h_list)

