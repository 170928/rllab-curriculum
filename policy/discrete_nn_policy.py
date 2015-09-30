import theano
import theano.tensor as T
import lasagne.layers as L
import numpy as np
from misc.tensor_utils import flatten_tensors, unflatten_tensors
from .base import DiscretePolicy


class DiscreteNNPolicy(DiscretePolicy):

    def __init__(self, observation_shape, action_dims, input_var):
        super(DiscreteNNPolicy, self).__init__(
            observation_shape, action_dims,
            input_var
            )
        self._network_outputs = self.new_network_outputs(
            observation_shape,
            action_dims,
            self.input_var
            )
        self._probs_vars = map(L.get_output, self._network_outputs)
        self._probs_func = theano.function(
            [self.input_var],
            T.concatenate(self.probs_vars, axis=1),
            allow_input_downcast=True
        )
        self._params = L.get_all_params(
            L.concat(self._network_outputs),
            trainable=True
        )
        self._param_shapes = map(
            lambda x: x.get_value(borrow=True).shape,
            self._params
        )
        self._param_dtypes = map(
            lambda x: x.get_value(borrow=True).dtype,
            self._params
        )

    def compute_action_probs(self, states):
        action_probs = self._probs_func(states)
        indices = np.cumsum(self.action_dims)[:-1]
        return np.split(action_probs, indices, axis=1)

    def get_param_values(self):
        return flatten_tensors(map(
            lambda x: x.get_value(borrow=True), self._params
        ))

    def set_param_values(self, flattened_params):
        param_values = unflatten_tensors(flattened_params, self._param_shapes)
        for param, dtype, value in zip(
                self._params,
                self._param_dtypes,
                param_values
                ):
            param.set_value(value.astype(dtype))

    @property
    def params(self):
        return self._params

    @property
    def probs_vars(self):
        return self._probs_vars

    # new_network_outputs should return a list of Lasagne layers, each of which
    # outputs a tensor of normalized action probabilities
    def new_network_outputs(self, observation_shape, action_dims, input_var):
        raise NotImplementedError
