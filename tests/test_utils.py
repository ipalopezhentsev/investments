import pytest

from iwantitmore.utils import MovingAvgCalculator, find_root_newton, approx_derivative_right, \
    approx_derivative_symmetric


class TestMovingAvg:
    def test_simple(self):
        elems = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        window = 2
        avgs = [None, (elems[0] + elems[1]) / window, (elems[1] + elems[2]) / window, (elems[2] + elems[3]) / window,
                (elems[3] + elems[4]) / window, (elems[4] + elems[5]) / window]
        calc = MovingAvgCalculator(window)
        for idx, elem in enumerate(elems):
            calc.add(elem)
            expected_avg = avgs[idx]
            assert calc.avg() == expected_avg


class TestFindRoot:
    def test_simple(self):
        # has only one root 1
        f = lambda x: x ** 2 - 2 * x + 1
        root, iters, obs_eps = find_root_newton(f, 0.5, f_der=lambda x: approx_derivative_symmetric(f, x, 1E-8))
        assert root == pytest.approx(0.9999923713783172)
        assert iters == 16
        assert obs_eps == pytest.approx(5.819589254940638e-11)

    def test_exact_derivative_gives_comparable_results(self):
        """note it isn't always better - by careful choosing of h in numerical diff test_simple
        actually gives closer answer in same number of iterations"""
        # has only one root 1
        f = lambda x: x ** 2 - 2 * x + 1
        f_der = lambda x: 2 * x - 2
        root, iters, obs_eps = find_root_newton(f, 0.5, f_der=f_der)
        assert root == pytest.approx(0.9999923706054688)
        assert iters == 16
        assert obs_eps == pytest.approx(5.820766091346741e-11)

    def test_already_converged(self):
        f = lambda x: x ** 2 - 2 * x + 1
        root, iters, obs_eps = find_root_newton(f, 1.0)
        assert root == 1.0
        assert iters == 0
        assert obs_eps == 0.0

    def test_cannot_converge(self):
        # doesn't have roots
        f = lambda x: x ** 2 - 2 * x + 2
        with pytest.raises(ValueError):
            find_root_newton(f, 2.0)
