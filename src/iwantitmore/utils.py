from typing import List, Optional, Callable


class MovingAvgCalculator:
    """calculates moving average given window size"""

    def __init__(self, window: int):
        if window < 2:
            raise ValueError("Window must be at least 2")
        self.window = window
        self.buffer: List[float] = [0.0] * window
        self.cur_idx = 0
        self.agg_sum = 0.0
        self.num_inserted = 0

    def add(self, elem: float) -> None:
        val_to_subtract = self.buffer[self.cur_idx]
        self.buffer[self.cur_idx] = elem
        self.agg_sum -= val_to_subtract
        self.agg_sum += elem
        self.cur_idx = self.cur_idx + 1 if self.cur_idx != self.window - 1 else 0
        # 'if' is for protection from overflow which will turn avg to None
        if self.num_inserted < self.window:
            self.num_inserted += 1

    def avg(self) -> Optional[float]:
        return self.agg_sum / self.window if self.num_inserted >= self.window else None


def approx_derivative_symmetric(f: Callable[[float], float], x: float, h: float) -> float:
    return (f(x + h) - f(x - h)) / (2.0 * h)


def approx_derivative_right(f: Callable[[float], float], x: float, h: float) -> float:
    return (f(x + h) - f(x - h)) / (2.0 * h)


def find_root_newton(f: Callable[[float], float], init_guess: float, eps: float = 1.0E-10,
                     max_iter: int = 1000, f_der: Callable[[float], float] = None) -> (float, int, float):
    """Uses Newton method to find roots of a function. Returns tuple where first element is found
    root value, second element is number iterations it took to find it, third element is resulting abs eps,
    how close the root is to 0.0.
    Raises ValueError if couldn't converge after max_iter iterations.
    """
    if f_der is None:
        f_der = lambda x: approx_derivative_symmetric(f, x, 1.0E-7)
    cur_x = init_guess
    num_iter = 0
    cur_f = f(cur_x)
    observed_eps = abs(cur_f)
    while num_iter < max_iter and observed_eps > eps:
        cur_x -= cur_f / f_der(cur_x)
        cur_f = f(cur_x)
        observed_eps = abs(cur_f)
        num_iter += 1
    if observed_eps < eps:
        return cur_x, num_iter, observed_eps
    else:
        raise ValueError(f"Has not converged in {max_iter} iterations")
