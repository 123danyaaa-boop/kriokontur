"""Оптимизатор: модель предлагает план, помесячный движок подтверждает исполнимость."""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.optimizer import OptimizerSettings, optimize, optimize_and_verify, verify
from kriokontur.paths import PLANS
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all
from kriokontur import rules


@pytest.fixture(scope="module")
def ctx():
    case, scen = load_case(), load_all()
    return case, scen, Plan.load(PLANS / "final-candidate.json")


def test_control_plan_is_feasible_in_control_scenarios(ctx):
    case, scen, base = ctx
    res = optimize_and_verify(case, scen, base, OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"]))
    assert res.status == "Optimal"
    for sid in ("BASE", "MANDATORY_STRESS"):
        assert res.verification[sid]["hard"] == 0, res.verification[sid]


def test_robust_capacity_plan_passes_every_scenario_and_beats_hand_plan(ctx):
    case, scen, base = ctx
    ids = list(scen)
    res = optimize_and_verify(case, scen, base, OptimizerSettings(scenario_ids=ids, strategic_stock=False))
    assert res.status == "Optimal"
    assert all(v["hard"] == 0 for v in res.verification.values()), {k: v["codes"] for k, v in res.verification.items() if v["hard"]}
    ref = verify(case, scen, base, ["BASE"])
    assert res.verification["BASE"]["pv_mln"] <= ref["BASE"]["pv_mln"] + 1e-6


def test_opening_stock_is_not_bought_in_advance(ctx):
    case, scen, base = ctx
    res = optimize(case, scen, base, OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"]))
    r0 = max(rules.reserve_days_to_tonnes(scen[k].demand_total(case, case.years[0])) for k in ("BASE", "MANDATORY_STRESS"))
    assert res.plan.inventory_policy["opening_stock_t"] <= r0 * float(base.assume("reserve_safety_factor")) + 0.2


def test_capex_limits_and_option_order_hold(ctx):
    case, scen, base = ctx
    res = optimize(case, scen, base, OptimizerSettings(scenario_ids=list(scen)))
    d = res.decisions
    if d.get("EARTH_NEW_EXERCISE"):
        assert d.get("EARTH_NEW_OPTION") and d["EARTH_NEW_OPTION"] <= d["EARTH_NEW_EXERCISE"]
    capex = {"ZBO": 180, "LUNAR_ISRU": 1250, "EARTH_NEW_OPTION": 90, "EARTH_NEW_EXERCISE": 270}
    total = sum(capex[k] for k, y in d.items() if y)
    early = sum(capex[k] for k, y in d.items() if y and y <= 2037)
    assert early <= 1800 and total <= 2800


def test_strategic_target_absent_keeps_engine_unchanged(ctx):
    case, scen, base = ctx
    assert "target_opening_t" not in base.inventory_policy
    res = run(case, scen["BASE"], base)
    assert res.totals["discounted_cost_mln"] == pytest.approx(8918.71, abs=0.01)


def test_solution_is_deterministic(ctx):
    case, scen, base = ctx
    a = optimize(case, scen, base, OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"]))
    b = optimize(case, scen, base, OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"]))
    assert a.decisions == b.decisions
    assert a.objective_pv == pytest.approx(b.objective_pv, rel=1e-6)
