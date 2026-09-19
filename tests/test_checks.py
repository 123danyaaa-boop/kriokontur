"""Проверки ограничений должны ловить неисполнимый план и называть причину."""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.checks import validate_plan
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INVEST = {"ZBO": 2036, "LUNAR_ISRU": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}


def codes(res):
    return {v.code for v in res.violations}


def test_capex_limit_2037_is_detected():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["BASE"], {"ZBO": 2036, "LUNAR_ISRU": 2036,
                                          "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}, "capex")
    plan.investments["LUNAR_ISRU"] = 2037
    plan.investments["EARTH_NEW_EXERCISE"] = 2037
    res = run(case, scen["BASE"], plan)
    assert plan.investments["LUNAR_ISRU"] == 2037
    total_capex = sum(y.cost["capex"] for y in res.years)
    assert total_capex == pytest.approx(1790)          # 180 + 1250 + 90 + 270


def test_negative_reservation_is_input_invalid():
    case = load_case()
    plan = Plan(plan_id="neg")
    plan.reservations["A"] = {2035: -5}
    assert any(v.code == "INPUT_INVALID" for v in validate_plan(case, plan))


def test_isru_financing_after_2037_is_violation():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["BASE"], {**INVEST, "LUNAR_ISRU": 2038}, "late")
    assert any(v.code == "ISRU_FUNDING_LATE" for v in validate_plan(case, plan))


def test_option_exercise_without_purchase_is_violation():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["BASE"], {**INVEST, "EARTH_NEW_OPTION": None}, "opt")
    assert any(v.code == "OPTION_NOT_PURCHASED" for v in validate_plan(case, plan))


def test_stress_loss_ceiling_requires_zbo():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["MANDATORY_STRESS"], {**INVEST, "ZBO": None}, "nozbo")
    res = run(case, scen["MANDATORY_STRESS"], plan)
    assert "STRESS_LOSS_LIMIT" in codes(res)


def test_service_violation_in_base_is_hard_and_in_stress_is_reference():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["BASE"], INVEST, "thin")
    for sid in ("A", "B", "C", "D", "E"):           # обрезаем мощности: план становится неисполнимым
        plan.reservations[sid] = {y: plan.reserved(sid, y) * 0.5 for y in case.years}
    base, stress = run(case, scen["BASE"], plan), run(case, scen["MANDATORY_STRESS"], plan)
    base_service = [v for v in base.violations if v.code.startswith("SERVICE_")]
    stress_service = [v for v in stress.violations if v.code.startswith("SERVICE_")]
    assert base_service and all(v.severity == "hard" for v in base_service)
    assert stress_service and all(v.severity == "reference" for v in stress_service)
    assert not base.feasible
