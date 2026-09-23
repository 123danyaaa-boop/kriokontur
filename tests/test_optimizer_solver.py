"""Решатель без устаревших вызовов PuLP: COIN_CMD вместо PULP_CBC_CMD, prob.add_variable.

Основной решатель это CBC 2.10.3, встроенный в PuLP: им получены все опубликованные числа.
cbcbox и cbc из PATH только запасные.
"""
import sys
import types
import warnings

import pulp
import pytest

from kriokontur import optimizer
from kriokontur.caseinput import load_case
from kriokontur.optimizer import OptimizerSettings, _solver, build_model, bundled_cbc_path
from kriokontur.paths import PLANS
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all


def test_primary_solver_is_bundled_cbc_via_coin_cmd():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        solver = _solver(None)
    assert isinstance(solver, pulp.COIN_CMD)
    assert not isinstance(solver, pulp.PULP_CBC_CMD)
    assert bundled_cbc_path() is not None
    assert solver.path == bundled_cbc_path()
    assert solver.available()


def test_falls_back_to_cbcbox_when_bundled_is_missing(monkeypatch):
    fake = types.SimpleNamespace(cbc_bin_path=lambda: "/fake/cbcbox/cbc")
    monkeypatch.setitem(sys.modules, "cbcbox", fake)
    monkeypatch.setattr(optimizer, "bundled_cbc_path", lambda: None)
    monkeypatch.setattr(pulp.COIN_CMD, "available", lambda self: self.path == "/fake/cbcbox/cbc")
    assert _solver(None).path == "/fake/cbcbox/cbc"


def test_clear_error_when_no_cbc_anywhere(monkeypatch):
    monkeypatch.setitem(sys.modules, "cbcbox", None)       # import cbcbox -> ImportError
    monkeypatch.setattr(optimizer, "bundled_cbc_path", lambda: None)
    monkeypatch.setattr(pulp.COIN_CMD, "available", lambda self: False)
    with pytest.raises(RuntimeError, match="requirements.txt"):
        _solver(None)


def test_model_builds_without_pulp_deprecations():
    case, scen = load_case(), load_all()
    base = Plan.load(PLANS / "final-candidate.json")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        model, _ = build_model(case, scen, base, OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"]))
    assert model.numVariables() > 0
