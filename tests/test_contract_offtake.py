"""D-01, D-02: отбор не может превышать законтрактованный объём канала.

Правило организатора (docs/CALCULATION_RULES.md §13):
    scheduled/offtake volume <= contractually available volume under team's model
и §14: аварийный канал ограничен годовой мощностью и шестинедельным сроком поставки.

До правки движок мог отобрать у Emergency больше годового резерва (партия ограничивалась,
годовая сумма — нет), а явный заказ команды исполнялся целиком поверх резерва.
"""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INVEST = {"ZBO": 2036, "LUNAR_ISRU": None, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035}


@pytest.fixture(scope="module")
def case_and_scenarios():
    return load_case(), load_all()


def test_emergency_offtake_never_exceeds_annual_reservation(case_and_scenarios):
    """D-01. Годовой отбор E ≤ зарезервированный объём во всех контрольных сценариях.

    План base-only в HIGH_DEMAND до правки отбирал у Emergency на 10,17 т больше договора.
    """
    case, scen = case_and_scenarios
    plan = Plan.load("configs/plans/base-only.json")
    for sid in ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND"):
        res = run(case, scen[sid], plan)
        for y in res.years:
            assert y.ordered_t["E"] <= plan.reserved("E", y.year) + 1e-6, (
                f"{sid} {y.year}: отбор E {y.ordered_t['E']:.2f} т при резерве "
                f"{plan.reserved('E', y.year):.2f} т")


def test_offtake_never_exceeds_contract_for_every_source(case_and_scenarios):
    """D-01. Общее правило: отбор любого канала ≤ резерв × доля года доступности."""
    case, scen = case_and_scenarios
    for name in ("final-candidate", "base-only", "robust", "robust-plus", "no-lunar"):
        plan = Plan.load(f"configs/plans/{name}.json")
        for sid in ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND"):
            res = run(case, scen[sid], plan)
            codes = {v.code for v in res.violations}
            assert "CONTRACT_OFFTAKE_EXCEEDED" not in codes, f"{name} × {sid}: {codes}"
            for y in res.years:
                for s in case.source_list:
                    assert y.ordered_t[s.source_id] <= y.contracted_t[s.source_id] + 1e-6


def test_explicit_order_above_reservation_is_hard_violation(case_and_scenarios):
    """D-02. Явный заказ B 2036 = 100 т при резерве 15,4 т: нарушение с годом и превышением."""
    case, scen = case_and_scenarios
    plan = Plan.load("configs/plans/final-candidate.json")
    plan.orders["B"] = {2036: 100.0}
    res = run(case, scen["BASE"], plan)
    viol = [v for v in res.violations if v.code == "ORDER_EXCEEDS_RESERVATION"]
    assert viol, [v.code for v in res.violations]
    assert viol[0].period == 2036
    assert viol[0].severity == "hard"
    assert viol[0].excess == pytest.approx(100.0 - 15.4, abs=1e-6)
    assert not res.feasible


def test_explicit_order_is_clamped_to_contracted_volume(case_and_scenarios):
    """D-02. Сверхдоговорной заказ не исполняется физически: отбор ограничен договором."""
    case, scen = case_and_scenarios
    plan = Plan.load("configs/plans/final-candidate.json")
    plan.orders["B"] = {2036: 100.0}
    res = run(case, scen["BASE"], plan)
    assert res.year(2036).ordered_t["B"] <= 15.4 + 1e-6


def test_emergency_call_respects_capacity_of_the_channel(case_and_scenarios):
    """D-01. Даже при завышенном резерве отбор E не выходит за физическую мощность канала."""
    case, scen = case_and_scenarios
    plan = auto_plan(case, scen["MANDATORY_STRESS"], INVEST, "e-cap")
    cap = case.sources["E"].capacity_t_per_year
    for y in case.years:                       # резерв на уровне мощности канала
        plan.reservations.setdefault("E", {})[y] = cap
    for sid in ("BASE", "MANDATORY_STRESS", "HIGH_DEMAND"):
        res = run(case, scen[sid], plan)
        for y in res.years:
            assert y.ordered_t["E"] <= cap + 1e-6
