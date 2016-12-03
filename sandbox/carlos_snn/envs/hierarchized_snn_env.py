import numpy as np
from sandbox.carlos_snn.envs.mujoco.maze.maze_env import MazeEnv
from sandbox.carlos_snn.envs.mujoco.maze.fast_maze_env import FastMazeEnv
from rllab.envs.normalized_env import NormalizedEnv

from rllab import spaces
from rllab.core.serializable import Serializable
from rllab.envs.proxy_env import ProxyEnv
from rllab.spaces.box import Box
from rllab.misc.overrides import overrides
from rllab.envs.base import Step
from rllab.misc import tensor_utils

from sandbox.carlos_snn.sampler.utils import rollout as rollout_noEnvReset  # different rollout! (no reset!)
from sandbox.carlos_snn.old_my_snn.hier_snn_mlp_policy import GaussianMLPPolicy_snn_hier

import joblib
import json
from rllab import config
import os


class HierarchizedSnnEnv(ProxyEnv, Serializable):
    def __init__(
            self,
            env,
            time_steps_agg=1,
            discrete_actions=True,
            pkl_path=None,
            json_path=None,
            npz_path=None,
            animate=False,
    ):
        Serializable.quick_init(self, locals())
        ProxyEnv.__init__(self, env)
        self.time_steps_agg = time_steps_agg
        self.discrete_actions = discrete_actions
        self.animate = animate
        if json_path:
            self.data = json.load(open(os.path.join(config.PROJECT_PATH, json_path), 'r'))
            self.low_policy_latent_dim = self.data['json_args']['policy']['latent_dim']
        elif pkl_path:
            pkl_path = os.path.join(config.PROJECT_PATH, pkl_path)
            self.data = joblib.load(pkl_path)
            self.low_policy_latent_dim = self.data['policy'].latent_dim
        else:
            raise Exception("No path to file given")

        # I need to define a new hier-policy that will cope with that!
        self.low_policy = GaussianMLPPolicy_snn_hier(
            env_spec=env.spec,
            env=env,
            pkl_path=pkl_path,
            json_path=json_path,
            npz_path=npz_path,
            trainable_snn=False,
            external_latent=True,
        )

    @property
    @overrides
    def action_space(self):
        lat_dim = self.low_policy_latent_dim
        if self.discrete_actions:
            return spaces.Discrete(lat_dim)  # the action is now just a selection
        else:
            ub = 1e6 * np.ones(lat_dim)
            return spaces.Box(-1 * ub, ub)

    @overrides
    def step(self, action):
        action = self.action_space.flatten(action)
        with self.low_policy.fix_latent(action):
            # print("From hier_snn_env --> the hier action is prefixed latent: {}".format(self.low_policy.pre_fix_latent))
            if isinstance(self.wrapped_env, FastMazeEnv):
                with self.wrapped_env.blank_maze():
                    frac_path = rollout_noEnvReset(self.wrapped_env, self.low_policy, max_path_length=self.time_steps_agg,
                                                   animated=self.animate, speedup=1000)
                next_obs = self.wrapped_env.get_current_obs()
            elif isinstance(self.wrapped_env, NormalizedEnv) and isinstance(self.wrapped_env.wrapped_env, FastMazeEnv):
                with self.wrapped_env.wrapped_env.blank_maze():
                    frac_path = rollout_noEnvReset(self.wrapped_env, self.low_policy, max_path_length=self.time_steps_agg,
                                                   animated=self.animate, speedup=1000)
                next_obs = self.wrapped_env.wrapped_env.get_current_obs()
            else:
                frac_path = rollout_noEnvReset(self.wrapped_env, self.low_policy, max_path_length=self.time_steps_agg,
                                               animated=self.animate, speedup=1000)
                next_obs = frac_path['observations'][-1]

            reward = np.sum(frac_path['rewards'])
            done = self.time_steps_agg > len(
                frac_path['observations'])  # if the rollout was not maximal it was "done"!`
            # it would be better to add an extra flagg to this rollout to check if it was done in the last step
            last_agent_info = dict((k, val[-1]) for k, val in frac_path['agent_infos'].items())
            last_env_info = dict((k, val[-1]) for k, val in frac_path['env_infos'].items())
        # print("finished step of {}, with cummulated reward of: {}".format(len(frac_path['observations']), reward))
        # print("Next obs (com): {}, rew: {}, last_env_info: {}, last_agent_info: {}".format(last_env_info, reward, last_env_info,
        #                                                                              last_agent_info))
        if done:
            # print("\n ########## \n ***** done!! *****")
            # if done I need to PAD the tensor so there is no mismatch! Pad with what? with the last elem!
            # I need to pad first the env_infos!!
            frac_path['env_infos'] = tensor_utils.pad_tensor_dict(frac_path['env_infos'], self.time_steps_agg)
            full_path = tensor_utils.pad_tensor_dict(frac_path, self.time_steps_agg, mode='last')
            # you might be padding the rewards!!! Error!!!
            actual_path_length = len(frac_path['rewards'])
            full_path['rewards'][actual_path_length:] = 0.
            # do the same for the maze_rewards
            if 'env_infos' in full_path.keys() and 'maze_rewards' in full_path['env_infos']:
                full_path['env_infos']['maze_rewards'][actual_path_length:] = 0.
        else:
            full_path = frac_path

        return Step(next_obs, reward, done,
                    last_env_info=last_env_info, last_agent_info=last_agent_info, full_path=full_path)
        # the last kwargs will all go to env_info, so path['env_info']['full_path'] gives a dict with the full path!

    @overrides
    def log_diagnostics(self, paths, *args, **kwargs):
        ## to use the visualization I need to append all paths!
        ## and also I need the paths to have the "agent_infos" key including the latent!!
        expanded_paths = [tensor_utils.flatten_first_axis_tensor_dict(path['env_infos']['full_path']) for path in paths]
        self.wrapped_env.log_diagnostics(expanded_paths, *args, **kwargs)

    def __str__(self):
        return "Hierarchized: %s" % self._wrapped_env


hierarchize_snn = HierarchizedSnnEnv
