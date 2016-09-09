

from rllab.envs.base import Env, Step
import numpy as np
from rllab.spaces.discrete import Discrete
from rllab.spaces.box import Box
from rllab.spaces.product import Product
from sandbox.rocky.hrl.misc.hrl_utils import using_seed
import matplotlib.pyplot as plt

# EMPTY = 0
AGENT = 0
DIVER = 1
BOMB = 2
N_OBJECT_TYPES = 3  # 4
from sandbox.rocky.hrl.envs.env_util import GridPlot


class SeaquestGridWorldEnv(Env):
    """
    A simple Seaquest-like grid world game.

    The observation is a 3D array where the last two dimensions encode the coordinate and the first dimension
    encode whether each type of objects is present in this grid.

    To score in this environment, the agent must collect all the divers while avoiding all bombs.
    """

    def __init__(self, size=10, n_bombs=None, n_divers=None, fixed_diver_slots=None, diver_pos_seed=None):
        """
        Create a new Seaquest-like grid world environment.
        :param size: Size of the grid world
        :param n_bombs: Number of bombs on the grid
        :param n_divers: Number of divers on the grid
        :param fixed_diver_slots: whether to have a fixed number of slots to sample the position of the divers from.
        :param diver_pos_seed: Seed used to sample the diver slots. Must be provided if fixed_diver_slots is set
        """
        self.grid = None
        self.size = size

        if n_bombs is None:
            n_bombs = size / 2
        if n_divers is None:
            n_divers = size / 2

        if fixed_diver_slots is not None:
            assert diver_pos_seed is not None
            with using_seed(diver_pos_seed):
                self.diver_slots = [
                    (max(1, idx / self.size), idx % self.size)
                    for idx in np.random.choice(self.size * self.size, size=fixed_diver_slots, replace=False)
                    ]
        else:
            self.diver_slots = None

        self.n_bombs = n_bombs
        self.n_divers = n_divers
        self.agent_position = None
        self.diver_positions = None
        self.bomb_positions = None

        self.diver_picked_up = None
        self.reset()
        self.fig = None

        visual_obs_space = Box(low=0, high=N_OBJECT_TYPES, shape=(N_OBJECT_TYPES, self.size, self.size))
        self._observation_space = visual_obs_space
        self._action_space = Discrete(4)

    def reset(self):
        # agent starts at top left corner
        self.agent_position = (1, 0)
        while True:
            all_pos_ids = np.random.choice(self.size * self.size, size=self.n_divers + self.n_bombs, replace=False)
            if self.diver_slots is not None:
                self.diver_positions = [
                    self.diver_slots[idx]
                    for idx in np.random.choice(len(self.diver_slots), size=self.n_divers, replace=False)
                    ]
            else:
                self.diver_positions = [
                    (max(1, idx / self.size), idx % self.size)
                    for idx in all_pos_ids[:self.n_divers]
                    ]
            self.bomb_positions = [
                (max(1, idx / self.size), idx % self.size)
                for idx in all_pos_ids[self.n_divers:]
                ]
            # ensure that there's a path from the agent to the diver
            if self.feasible():
                break
        self.diver_picked_up = [False] * self.n_divers
        return self.get_current_obs()

    def feasible(self):
        # perform floodfill from the diver position
        if self.agent_position in self.bomb_positions:
            return False
        visited = np.zeros((self.size, self.size))
        visited[self.agent_position] = 1
        cur = self.agent_position
        queue = [cur]
        incs = np.array([[0, 1], [0, -1], [1, 0], [-1, 0]])
        while len(queue) > 0:
            node = queue.pop()
            visited[node] = True
            for inc in incs:
                next = tuple(np.clip(np.array(node) + inc, [1, 0], [self.size - 1, self.size - 1]))
                if next not in self.bomb_positions and not visited[next]:
                    queue.append(next)
        return all(visited[pos] for pos in self.diver_positions)

    def get_current_obs(self):
        grid = np.zeros((N_OBJECT_TYPES, self.size, self.size), dtype='uint8')
        grid[(AGENT,) + self.agent_position] = 1
        for diver_pos, picked_up in zip(self.diver_positions, self.diver_picked_up):
            if not picked_up:
                grid[(DIVER,) + diver_pos] = 1
        for bomb_position in self.bomb_positions:
            grid[(BOMB,) + bomb_position] = 1
        # if self.guided_observation:
        #     return (grid, self.agent_position + (int(self.diver_picked_up),))
        return grid

    def step(self, action):
        coords = np.array(self.agent_position)
        increments = np.array([[0, -1], [1, 0], [0, 1], [-1, 0]])
        self.agent_position = tuple(np.clip(
            coords + increments[action],
            [0, 0],
            [self.size - 1, self.size - 1]
        ))
        if self.agent_position in self.bomb_positions:
            return Step(observation=self.get_current_obs(), reward=-1, done=True)
        if self.agent_position in self.diver_positions:
            diver_idx = self.diver_positions.index(self.agent_position)
            self.diver_picked_up[diver_idx] = True
        if self.agent_position[0] == 0:
            if all(self.diver_picked_up):
                return Step(observation=self.get_current_obs(), reward=1, done=True)
            else:
                return Step(observation=self.get_current_obs(), reward=0, done=True)
        return Step(observation=self.get_current_obs(), reward=0, done=False)

    @staticmethod
    def action_from_direction(d):
        """
        Return the action corresponding to the given direction. This is a helper method for debugging and testing
        purposes.
        :return: the action index corresponding to the given direction
        """
        return dict(
            left=0,
            down=1,
            right=2,
            up=3
        )[d]

    def render(self):
        if self.fig is None:
            self.fig = GridPlot(self.size)
            plt.ion()
        self.fig.reset_grid()
        self.fig.color_grid(self.agent_position[0], self.agent_position[1], 'g')
        self.fig.add_text(self.agent_position[0], self.agent_position[1], 'Agent')
        for diver_pos, picked_up in zip(self.diver_positions, self.diver_picked_up):
            if not picked_up:
                self.fig.color_grid(diver_pos[0], diver_pos[1], 'b')
                self.fig.add_text(diver_pos[0], diver_pos[1], 'Diver')
        for bomb_position in self.bomb_positions:
            self.fig.color_grid(bomb_position[0], bomb_position[1], 'r')
            self.fig.add_text(bomb_position[0], bomb_position[1], 'Bomb')
        plt.show()
        plt.pause(0.01)

    def action_from_key(self, key):
        if key in ['left', 'down', 'right', 'up']:
            return self.action_from_direction(key)
        return None

    @property
    def matplotlib_figure(self):
        return self.fig.figure

    @property
    def action_space(self):
        return self._action_space

    @property
    def observation_space(self):
        return self._observation_space

    def action_from_keys(self):
        pass
