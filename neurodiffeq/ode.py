import matplotlib
import matplotlib.pyplot as plt
import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

from .networks import FCNN
from copy import deepcopy


def _network_output(net, ts, ith_unit):
    nn_output = net(ts)
    if ith_unit is not None:
        return nn_output[:, ith_unit].reshape(-1, 1)
    else:
        return nn_output


def _trial_solution(single_net, nets, ts, conditions):
    if single_net:  # using a single net
        us = [
            con.enforce(single_net, ts)
            for con in conditions
        ]
    else:  # using multiple nets
        us = [
            con.enforce(net, ts)
            for con, net in zip(conditions, nets)
        ]
    return us


class Condition:

    def __init__(self):
        self.ith_unit = None

    def set_impose_on(self, ith_unit):
        self.ith_unit = ith_unit


class NoCondition(Condition):
    """An condition class that does not impose any initial/boundary conditions
    """

    def __init__(self):
        super().__init__()

    def enforce(self, net, t):
        r"""Return the raw input of neural network.

        .. note::
            `enforce` is meant to be called by the function `solve` and `solve_system`.
        """
        return _network_output(net, t, self.ith_unit)


class IVP(Condition):
    """An initial value problem.
        For Dirichlet condition, we are solving :math:`x(t)` given :math:`x(t)\\bigg|_{t = t_0} = x_0`.
        For Neumann condition, we are solving :math:`x(t)` given :math:`\\displaystyle\\frac{\\partial x}{\\partial t}\\bigg|_{t = t_0} = x_0'`.

    :param t_0: The initial time.
    :type t_0: float
    :param x_0: The initial value of :math:x. :math:`x(t)\\bigg|_{t = t_0} = x_0`.
    :type x_0: float
    :param x_0_prime: The inital derivative of :math:`x` wrt :math:`t`. :math:`\\displaystyle\\frac{\\partial x}{\\partial t}\\bigg|_{t = t_0} = x_0'`, defaults to None.
    :type x_0_prime: float, optional
    """
    def __init__(self, t_0, x_0, x_0_prime=None):
        """Initializer method
        """
        super().__init__()
        self.t_0, self.x_0, self.x_0_prime = t_0, x_0, x_0_prime

    def enforce(self, net, t):
        r"""Enforce the output of a neural network to satisfy the initial condition.

        :param net: The neural network that approximates the ODE.
        :type net: `torch.nn.Module`
        :param t: The points where the neural network output is evaluated.
        :type t: `torch.tensor`
        :return: The modified output which now satisfies the initial condition.
        :rtype: `torch.tensor`

        .. note::
            `enforce` is meant to be called by the function `solve` and `solve_system`.
        """
        x = _network_output(net, t, self.ith_unit)
        if self.x_0_prime:
            return self.x_0 + (t-self.t_0)*self.x_0_prime + ( (1-torch.exp(-t+self.t_0))**2 )*x
        else:
            return self.x_0 + (1-torch.exp(-t+self.t_0))*x


class DirichletBVP(Condition):
    """A two-point Dirichlet boundary condition.
        We are solving :math:`x(t)` given :math:`x(t)\\bigg|_{t = t_0} = x_0` and :math:`x(t)\\bigg|_{t = t_1} = x_1`.

    :param t_0: The initial time.
    :type t_0: float
    :param t_1: The final time.
    :type t_1: float
    :param x_0: The initial value of :math:x. :math:`x(t)\\bigg|_{t = t_0} = x_0`.
    :type x_0: float
    :param x_1: The initial value of :math:x. :math:`x(t)\\bigg|_{t = t_1} = x_1`.
    :type x_1: float
    """
    def __init__(self, t_0, x_0, t_1, x_1):
        """Initializer method
        """
        super().__init__()
        self.t_0, self.x_0, self.t_1, self.x_1 = t_0, x_0, t_1, x_1

    def enforce(self, net, t):
        r"""Enforce the output of a neural network to satisfy the boundary condition.

        :param net: The neural network that approximates the ODE.
        :type net: `torch.nn.Module`
        :param t: The points where the neural network output is evaluated.
        :type t: `torch.tensor`
        :return: The modified output which now satisfies the boundary condition.
        :rtype: `torch.tensor`


        .. note::
            `enforce` is meant to be called by the function `solve` and `solve_system`.
        """
        x = _network_output(net, t, self.ith_unit)
        t_tilde = (t-self.t_0) / (self.t_1-self.t_0)
        return self.x_0*(1-t_tilde) + self.x_1*t_tilde + (1-torch.exp((1-t_tilde)*t_tilde))*x


class ExampleGenerator:
    """An example generator for generating 1-D training points.

    :param size: The number of points to generate each time `get_examples` is called.
    :type size: int
    :param t_min: The lower bound of the 1-D points generated, defaults to 0.0.
    :type t_min: float, optional
    :param t_max: The upper boound of the 1-D points generated, defaults to 1.0.
    :type t_max: float, optional
    :param method: The distribution of the 1-D points generated.
        If set to 'uniform', the points will be drew from a uniform distribution Unif(t_min, t_max).
        If set to 'equally-spaced', the points will be fixed to a set of linearly-spaced points that go from t_min to t_max.
        If set to 'equally-spaced-noisy', a normal noise will be added to the previously mentioned set of points.
        If set to 'log-spaced', the points will be fixed to a set of log-spaced points that go from t_min to t_max.
        If set to 'log-spaced-noisy', a normal noise will be added to the previously mentioned set of points, defaults to 'uniform'.
    :type method: str, optional
    :raises ValueError: When provided with an unknown method.
    """
    def __init__(self, size, t_min=0.0, t_max=1.0, method='uniform', noise_std=None):
        r"""Initializer method

        .. note::
            A instance method `get_examples` is dynamically created to generate 1-D training points. It will be called by the function `solve` and `solve_system`.
        """
        self.size = size
        self.t_min, self.t_max = t_min, t_max
        if method == 'uniform':
            self.examples = torch.zeros(self.size, requires_grad=True)
            self.get_examples = lambda: self.examples + torch.rand(self.size)*(self.t_max-self.t_min) + self.t_min
        elif method == 'equally-spaced':
            self.examples = torch.linspace(self.t_min, self.t_max, self.size, requires_grad=True)
            self.get_examples = lambda: self.examples
        elif method == 'equally-spaced-noisy':
            self.examples = torch.linspace(self.t_min, self.t_max, self.size, requires_grad=True)
            if noise_std:
                self.noise_std = noise_std
            else:
                self.noise_std  = ( (t_max-t_min)/size ) / 4.0
            self.get_examples = lambda: torch.normal(mean=self.examples, std=self.noise_std)
        elif method == 'log-spaced':
            self.examples = torch.logspace(self.t_min, self.t_max, self.size, requires_grad=True)
            self.get_examples = lambda: self.examples
        elif method == 'log-spaced-noisy':
            self.examples = torch.logspace(self.t_min, self.t_max, self.size, requires_grad=True)
            if noise_std:
                self.noise_std = noise_std
            else:
                self.noise_std = ((t_max - t_min) / size) / 4.0
            self.get_examples = lambda: torch.normal(mean=self.examples, std=self.noise_std)
        else:
            raise ValueError(f'Unknown method: {method}')


class Monitor:
    """A monitor for checking the status of the neural network during training.

    :param t_min: The lower bound of time domain that we want to monitor.
    :type t_min: float
    :param t_max: The upper bound of time domain that we want to monitor.
    :type t_max: float
    :param check_every: The frequency of checking the neural network represented by the number of epochs between two checks, defaults to 100.
    :type check_every: int, optional
    """
    def __init__(self, t_min, t_max, check_every=100):
        """Initializer method
        """
        self.using_non_gui_backend = matplotlib.get_backend() is 'agg'
        self.check_every = check_every
        self.fig = plt.figure(figsize=(30, 8))
        self.ax1 = self.fig.add_subplot(131)
        self.ax2 = self.fig.add_subplot(132)
        self.ax3 = self.fig.add_subplot(133)
        # input for plotting
        self.ts_plt = np.linspace(t_min, t_max, 100)
        # input for neural network
        self.ts_ann = torch.linspace(t_min, t_max, 100, requires_grad=True).reshape((-1, 1))

    def check(self, single_net, nets, conditions, history):
        r"""Draw 2 plots: One shows the shape of the current solution. The other shows the history training loss and validation loss.

        :param nets: The neural networks that approximates the ODE (system).
        :type nets: list[`torch.nn.Module`]
        :param conditions: The initial/boundary conditions of the ODE (system).
        :type conditions: list[`neurodiffeq.ode.IVP` or `neurodiffeq.ode.DirichletBVP` or `neurodiffeq.ode.NoCondition`]
        :param history: The history of training loss and validation loss. The 'train' entry is a list of training loss and 'valid' entry is a list of validation loss.
        :type history: dict['train': list[float], 'valid': list[float]]

        .. note::
            `check` is meant to be called by the function `solve` and `solve_system`.
        """
        us = _trial_solution(single_net, nets, self.ts_ann, conditions)
        us = [u.detach().cpu().numpy() for u in us]

        self.ax1.clear()
        for i, u in enumerate(us):
            self.ax1.plot(self.ts_plt, u, label=f'variable {i}')
        self.ax1.legend()
        self.ax1.set_title('solutions')

        self.ax2.clear()
        self.ax2.plot(history['train_loss'], label='training loss')
        self.ax2.plot(history['valid_loss'], label='validation loss')
        self.ax2.set_title('loss during training')
        self.ax2.set_ylabel('loss')
        self.ax2.set_xlabel('epochs')
        self.ax2.set_yscale('log')
        self.ax2.legend()

        self.ax3.clear()
        for metric_name, metric_values in history.items():
            if metric_name == 'train_loss' or metric_name == 'valid_loss':
                continue
            self.ax3.plot(metric_values, label=metric_name)
        self.ax3.set_title('metrics during training')
        self.ax3.set_ylabel('metrics')
        self.ax3.set_xlabel('epochs')
        self.ax3.set_yscale('log')
        # if there's not custom metrics, then there won't be any labels in this axis
        if len(history) > 2:
            self.ax3.legend()

        self.fig.canvas.draw()
        if not self.using_non_gui_backend:
            plt.pause(0.05)


def solve(
        ode, condition, t_min=None, t_max=None,
        net=None, train_generator=None, shuffle=True, valid_generator=None,
        optimizer=None, criterion=None, additional_loss_term=None, metrics=None, batch_size=16,
        max_epochs=1000,
        monitor=None, return_internal=False,
        return_best=False
):
    """Train a neural network to solve an ODE.

    :param ode: The ODE to solve. If the ODE is :math:`F(x, t) = 0` where :math:`x` is the dependent variable and :math:`t` is the independent variable,
        then `ode` should be a function that maps :math:`(x, t)` to :math:`F(x, t)`.
    :type ode: function
    :param condition: The initial/boundary condition.
    :type condition: `neurodiffeq.ode.IVP` or `neurodiffeq.ode.DirichletBVP` or `neurodiffeq.ode.NoCondition`
    :param net: The neural network used to approximate the solution, defaults to None.
    :type net: `torch.nn.Module`, optional
    :param t_min: The lower bound of the domain (t) on which the ODE is solved, only needed when train_generator or valid_generator are not specified, defaults to None
    :type t_min: float
    :param t_max: The upper bound of the domain (t) on which the ODE is solved, only needed when train_generator or valid_generator are not specified, defaults to None
    :type t_max: float
    :param train_generator: The example generator to generate 1-D training points, default to None.
    :type train_generator: `neurodiffeq.ode.ExampleGenerator`, optional
    :param shuffle: Whether to shuffle the training examples every epoch, defaults to True.
    :type shuffle: bool, optional
    :param valid_generator: The example generator to generate 1-D validation points, default to None.
    :type valid_generator: `neurodiffeq.ode.ExampleGenerator`, optional
    :param optimizer: The optimization method to use for training, defaults to None.
    :type optimizer: `torch.optim.Optimizer`, optional
    :param criterion: The loss function to use for training, defaults to None.
    :type criterion: `torch.nn.modules.loss._Loss`, optional
    :param additional_loss_term: Extra terms to add to the loss function besides the part specified by `criterion`. The input of `additional_loss_term` should be the same as `ode`
    :type additional_loss_term: function
    :param metrics: Metrics to keep track of during training. The metrics should be passed as a dictionary where the keys are the names of the metrics, and the values are the corresponding function.
        The input functions should be the same as `ode` and the output should be a numeric value. The metrics are evaluated on both the training set and validation set.
    :type metrics: dict[string, function]
    :param batch_size: The size of the mini-batch to use, defaults to 16.
    :type batch_size: int, optional
    :param max_epochs: The maximum number of epochs to train, defaults to 1000.
    :type max_epochs: int, optional
    :param monitor: The monitor to check the status of nerual network during training, defaults to None.
    :type monitor: `neurodiffeq.ode.Monitor`, optional
    :param return_internal: Whether to return the nets, conditions, training generator, validation generator, optimizer and loss function, defaults to False.
    :type return_internal: bool, optional
    :param return_best: Whether to return the nets that achieved the lowest validation loss, defaults to False.
    :type return_best: bool, optional
    :return: The solution of the ODE. The history of training loss and validation loss.
        Optionally, the nets, conditions, training generator, validation generator, optimizer and loss function.
    :rtype: tuple[`neurodiffeq.ode.Solution`, dict]; or tuple[`neurodiffeq.ode.Solution`, dict, dict]
    """
    nets = None if not net else [net]
    return solve_system(
        ode_system=lambda x, t: [ode(x, t)], conditions=[condition],
        t_min=t_min, t_max=t_max, nets=nets,
        train_generator=train_generator, shuffle=shuffle, valid_generator=valid_generator,
        optimizer=optimizer, criterion=criterion, additional_loss_term=additional_loss_term, metrics=metrics, batch_size=batch_size,
        max_epochs=max_epochs, monitor=monitor, return_internal=return_internal,
        return_best=return_best
    )


def solve_system(
        ode_system, conditions, t_min, t_max,
        single_net=None, nets=None, train_generator=None, shuffle=True, valid_generator=None,
        optimizer=None, criterion=None, additional_loss_term=None, metrics=None, batch_size=16,
        max_epochs=1000,
        monitor=None, return_internal=False,
        return_best=False,
):
    """Train a neural network to solve an ODE.

    :param ode_system: The ODE system to solve. If the ODE system consists of equations :math:`F_i(x_1, x_2, ..., x_n, t) = 0` where :math:`x_i` is the dependent i-th variable and :math:`t` is the independent variable,
        then `ode_system` should be a function that maps :math:`(x_1, x_2, ..., x_n, t)` to a list where the i-th entry is :math:`F_i(x_1, x_2, ..., x_n, t)`.
    :type ode_system: function
    :param conditions: The initial/boundary conditions. The ith entry of the conditions is the condition that :math:`x_i` should satisfy.
    :type conditions: list[`neurodiffeq.ode.IVP` or `neurodiffeq.ode.DirichletBVP` or `neurodiffeq.ode.NoCondition`]
    :param t_min: The lower bound of the domain (t) on which the ODE is solved, only needed when train_generator or valid_generator are not specified, defaults to None
    :type t_min: float
    :param t_max: The upper bound of the domain (t) on which the ODE is solved, only needed when train_generator or valid_generator are not specified, defaults to None
    :type t_max: float
    :param single_net: The single neural network used to approximate the solution. Only one of `single_net` and `nets` should be specified, defaults to None
    :param single_net: `torch.nn.Module`, optional
    :param nets: The neural networks used to approximate the solution, defaults to None.
    :type nets: list[`torch.nn.Module`], optional
    :param train_generator: The example generator to generate 1-D training points, default to None.
    :type train_generator: `neurodiffeq.ode.ExampleGenerator`, optional
    :param shuffle: Whether to shuffle the training examples every epoch, defaults to True.
    :type shuffle: bool, optional
    :param valid_generator: The example generator to generate 1-D validation points, default to None.
    :type valid_generator: `neurodiffeq.ode.ExampleGenerator`, optional
    :param optimizer: The optimization method to use for training, defaults to None.
    :type optimizer: `torch.optim.Optimizer`, optional
    :param criterion: The loss function to use for training, defaults to None and sum of square of the output of `ode_system` will be used.
    :type criterion: `torch.nn.modules.loss._Loss`, optional
    :param additional_loss_term: Extra terms to add to the loss function besides the part specified by `criterion`. The input of `additional_loss_term` should be the same as `ode_system`
    :type additional_loss_term: function
    :param metrics: Metrics to keep track of during training. The metrics should be passed as a dictionary where the keys are the names of the metrics, and the values are the corresponding function.
        The input functions should be the same as `ode_system` and the output should be a numeric value. The metrics are evaluated on both the training set and validation set.
    :type metrics: dict[string, function]
    :param batch_size: The size of the mini-batch to use, defaults to 16.
    :type batch_size: int, optional
    :param max_epochs: The maximum number of epochs to train, defaults to 1000.
    :type max_epochs: int, optional
    :param monitor: The monitor to check the status of nerual network during training, defaults to None.
    :type monitor: `neurodiffeq.ode.Monitor`, optional
    :param return_internal: Whether to return the nets, conditions, training generator, validation generator, optimizer and loss function, defaults to False.
    :type return_internal: bool, optional
    :param return_best: Whether to return the nets that achieved the lowest validation loss, defaults to False.
    :type return_best: bool, optional
    :return: The solution of the ODE. The history of training loss and validation loss.
        Optionally, the nets, conditions, training generator, validation generator, optimizer and loss function.
    :rtype: tuple[`neurodiffeq.ode.Solution`, dict]; or tuple[`neurodiffeq.ode.Solution`, dict, dict]
    """

    ########################################### subroutines ###########################################
    def train(train_generator, net, nets, ode_system, conditions, criterion, additional_loss_term, shuffle, optimizer):
        train_examples_t = train_generator.get_examples()
        train_examples_t = train_examples_t.reshape((-1, 1))
        n_examples_train = train_generator.size
        idx = np.random.permutation(n_examples_train) if shuffle else np.arange(n_examples_train)

        batch_start, batch_end = 0, batch_size
        while batch_start < n_examples_train:
            if batch_end > n_examples_train:
                batch_end = n_examples_train
            batch_idx = idx[batch_start:batch_end]
            ts = train_examples_t[batch_idx]

            train_loss_batch = calculate_loss(ts, net, nets, ode_system, conditions, criterion, additional_loss_term)

            optimizer.zero_grad()
            train_loss_batch.backward()
            optimizer.step()

            batch_start += batch_size
            batch_end += batch_size

        train_loss_epoch = calculate_loss(train_examples_t, net, nets, ode_system, conditions, criterion, additional_loss_term)
        train_loss_epoch = train_loss_epoch.item()
        
        train_metrics_epoch = calculate_metrics(train_examples_t, net, nets, conditions, metrics)
        return train_loss_epoch, train_metrics_epoch

    def valid(valid_generator, net, nets, ode_system, conditions, criterion, additional_loss_term):
        valid_examples_t = valid_generator.get_examples()
        valid_examples_t = valid_examples_t.reshape((-1, 1))
        valid_loss_epoch = calculate_loss(valid_examples_t, net, nets, ode_system, conditions, criterion, additional_loss_term)
        valid_loss_epoch = valid_loss_epoch.item()

        valid_metrics_epoch = calculate_metrics(valid_examples_t, net, nets, conditions, metrics)
        return valid_loss_epoch, valid_metrics_epoch

    def calculate_loss(ts, net, nets, ode_system, conditions, criterion, additional_loss_term):
        us = _trial_solution(net, nets, ts, conditions)
        Futs = ode_system(*us, ts)
        loss = sum(
            criterion(Fut, torch.zeros_like(ts))
            for Fut in Futs
        )
        if additional_loss_term is not None:
            loss += additional_loss_term(*us, ts)
        return loss
    
    def calculate_metrics(ts, net, nets, conditions, metrics):
        us = _trial_solution(net, nets, ts, conditions)
        metrics_ = {
            metric_name: metric_function(*us, ts).item()
            for metric_name, metric_function in metrics.items()
        }
        return metrics_
    ###################################################################################################

    if single_net and nets:
        raise RuntimeError('Only one of net and nets should be specified')
    # defaults to use a single neural network
    if (not single_net) and (not nets):
        single_net = FCNN(n_input_units=1, n_output_units=len(conditions), n_hidden_units=32, n_hidden_layers=1,
                   actv=nn.Tanh)
    if single_net:
        # mark the Conditions so that we know which condition correspond to which output unit
        for ith, con in enumerate(conditions):
            con.set_impose_on(ith)
    if not train_generator:
        if (t_min is None) or (t_max is None):
            raise RuntimeError('Please specify t_min and t_max when train_generator is not specified')
        train_generator = ExampleGenerator(32, t_min, t_max, method='equally-spaced-noisy')
    if not valid_generator:
        if (t_min is None) or (t_max is None):
            raise RuntimeError('Please specify t_min and t_max when train_generator is not specified')
        valid_generator = ExampleGenerator(32, t_min, t_max, method='equally-spaced')
    if (not optimizer) and single_net:  # using a single net
        optimizer = optim.Adam(single_net.parameters(), lr=0.001)
    if (not optimizer) and nets:  # using multiple nets
        all_parameters = []
        for net in nets:
            all_parameters += list(net.parameters())
        optimizer = optim.Adam(all_parameters, lr=0.001)
    if not criterion:
        criterion = nn.MSELoss()
    if metrics is None:
        metrics = {}

    history = {}
    history['train_loss'] = []
    history['valid_loss'] = []
    for metric_name, _ in metrics.items():
        history['train__' + metric_name] = []
        history['valid__' + metric_name] = []

    if return_best:
        valid_loss_epoch_min = np.inf
        solution_min = None

    for epoch in range(max_epochs):
        train_loss_epoch, train_metrics_epoch = train(train_generator, single_net, nets, ode_system, conditions, criterion, additional_loss_term, shuffle,
                                 optimizer)
        history['train_loss'].append(train_loss_epoch)
        for metric_name, metric_value in train_metrics_epoch.items():
            history['train__'+metric_name].append(metric_value)

        valid_loss_epoch, valid_metrics_epoch = valid(valid_generator, single_net, nets, ode_system, conditions, criterion, additional_loss_term,)
        history['valid_loss'].append(valid_loss_epoch)
        for metric_name, metric_value in valid_metrics_epoch.items():
            history['valid__'+metric_name].append(metric_value)

        if monitor and epoch % monitor.check_every == 0:
            monitor.check(single_net, nets, conditions, history)

        if return_best and valid_loss_epoch < valid_loss_epoch_min:
            valid_loss_epoch_min = valid_loss_epoch
            solution_min = Solution(single_net, nets, conditions)

    if return_best:
        solution = solution_min
    else:
        solution = Solution(single_net, nets, conditions)

    if return_internal:
        internal = {
            'single_net': single_net,
            'nets': nets,
            'conditions': conditions,
            'train_generator': train_generator,
            'valid_generator': valid_generator,
            'optimizer': optimizer,
            'criterion': criterion
        }
        return solution, history, internal
    else:
        return solution, history


class Solution:
    """A solution to an ODE (system)

    :param nets: The neural networks that approximates the ODE.
    :type nets: list[`torch.nn.Module`]
    :param conditions: The initial/boundary conditions of the ODE (system).
    :type conditions: list[`neurodiffeq.ode.IVP` or `neurodiffeq.ode.DirichletBVP` or `neurodiffeq.ode.NoCondition`]
    """
    def __init__(self, single_net, nets, conditions):
        """Initializer method
        """
        self.single_net = deepcopy(single_net)
        self.nets = deepcopy(nets)
        self.conditions = deepcopy(conditions)

    def __call__(self, ts, as_type='tf'):
        """Evaluate the solution at certain points.

        :param ts: the points on which the dependent variables are evaluated.
        :type ts: `torch.tensor` or sequence of number
        :param as_type: Whether the returned value is a `torch.tensor` ('tf') or `numpy.array` ('np').
        :type as_type: str
        :return: dependent variables are evaluated at given points.
        :rtype: list[`torch.tensor` or `numpy.array` (when there is more than one dependent variables)
            `torch.tensor` or `numpy.array` (when there is only one dependent variable)
        """
        if not isinstance(ts, torch.Tensor):
            ts = torch.tensor(ts, dtype=torch.float32)
        original_shape = ts.shape
        ts = ts.reshape(-1, 1)
        if as_type not in ('tf', 'np'):
            raise ValueError("The valid return types are 'tf' and 'np'.")

        us = _trial_solution(self.single_net, self.nets, ts, self.conditions)
        us = [u.reshape(original_shape) for u in us]
        if as_type == 'np':
            us = [u.detach().cpu().numpy().flatten() for u in us]

        return us if len(self.conditions) > 1 else us[0]
