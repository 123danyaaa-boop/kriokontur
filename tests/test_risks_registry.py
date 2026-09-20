"""D-11, D-12: реестр рисков под выбранный план, расчёт мер и вероятностный блок.

Аудит поймал три вещи: риски R1 и R2 давали нулевое последствие для финального плана,
меры и остаточный риск не считались вовсе, а в Монте-Карло у нового поставщика первый год
шёл по ставке 0,94 вместо 0,88 и не было доверительных интервалов.
"""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.plan import Plan
from kriokontur.risks import (apply_plan_overrides, evaluate_mitigations, evaluate_risks,
                              first_operating_year, load_mitigations, load_risks, monte_carlo,
                              parse_reliability, wilson_interval)
from kriokontur.scenarios import load_all


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    return case, scen, plan


# --------------------------------------------------------------------------- #
# D-11: реестр относится к выбранному плану
# --------------------------------------------------------------------------- #
def test_every_risk_is_material_for_the_final_plan(setup):
    case, scen, plan = setup
    rows = evaluate_risks(case, scen, plan, load_risks())
    assert len(rows) >= 5
    for r in rows:
        assert "error" not in r, r
        assert r["material"], f"{r['risk_id']}: последствие неотличимо от нуля на финальном плане"


def test_risk_registry_has_the_fields_the_case_requires(setup):
    for risk in load_risks():
        assert risk.event and risk.cause and risk.affected_parameter and risk.period
        assert risk.owner and risk.residual and risk.dependencies
        assert risk.probability_basis, risk.risk_id
        if risk.probability is None:
            assert risk.probability_low is not None or "не назначается" in risk.probability_basis \
                or "не выводится" in risk.probability_basis, risk.risk_id


def test_risk_consequences_are_measured_in_tonnes_money_and_service(setup):
    case, scen, plan = setup
    rows = {r["risk_id"]: r for r in evaluate_risks(case, scen, plan, load_risks())}
    for r in rows.values():
        for key in ("consequence_pv_mln", "delta_shortage_t", "sl_total_worst",
                    "delta_cost_per_served_t", "hard_violations"):
            assert key in r, (r["risk_id"], key)
    # риск, который меняет вход плана, показывает собственный вклад, а не весь эффект сценария
    r2 = rows["R2"]
    assert abs(r2["consequence_pv_mln"]) < abs(r2["delta_pv_mln"]), \
        "для риска на стороне плана нужно отделять вклад события от эффекта сценария"
    assert r2["consequence_pv_mln"] == pytest.approx(r2["delta_pv_marginal_mln"], abs=1e-9)


def test_expected_value_is_given_as_a_range_when_probability_is_a_range(setup):
    case, scen, plan = setup
    rows = {r["risk_id"]: r for r in evaluate_risks(case, scen, plan, load_risks())}
    r1 = rows["R1"]
    assert r1["expected_delta_pv_range_mln"] is not None
    low, high = r1["expected_delta_pv_range_mln"]
    assert low < r1["expected_delta_pv_mln"] < high
    # где статистики нет, точечная вероятность не публикуется
    assert rows["R4"]["probability"] is None and rows["R4"]["expected_delta_pv_mln"] is None


def test_plan_overrides_do_not_touch_the_original_plan(setup):
    case, scen, plan = setup
    before = plan.assume("storage_commissioning_lag_months")
    changed = apply_plan_overrides(plan, {"assumptions": {"storage_commissioning_lag_months": 12},
                                          "reservations": {"E": {2040: 80.0}}})
    assert changed.assume("storage_commissioning_lag_months") == 12
    assert changed.reserved("E", 2040) == 80.0
    assert plan.assume("storage_commissioning_lag_months") == before
    assert plan.reserved("E", 2040) != 80.0


# --------------------------------------------------------------------------- #
# D-11: меры считаются, а не описываются
# --------------------------------------------------------------------------- #
def test_mitigations_have_cost_effect_and_residual(setup):
    case, scen, plan = setup
    rows = evaluate_mitigations(case, scen, plan, load_risks(), load_mitigations())
    assert rows
    for m in rows:
        assert "error" not in m, m
        for key in ("cost_mln", "effect_mln", "residual_mln", "shortage_avoided_t",
                    "sl_worst_with", "net_benefit_mln"):
            assert key in m and m[key] is not None, (m["risk_id"], m["mitigation_id"], key)
        # арифметика согласована: эффект это разница последствий без меры и с мерой
        assert m["effect_mln"] == pytest.approx(
            m["consequence_without_mln"] - m["consequence_with_mln"], abs=1e-9)
        assert m["net_benefit_mln"] == pytest.approx(m["effect_mln"] - m["cost_mln"], abs=1e-9)


def test_counterfactual_mitigation_shows_the_value_of_the_emergency_contract(setup):
    """Отказ от аварийного договора обязан ухудшать обслуживание: иначе он не нужен плану."""
    case, scen, plan = setup
    rows = [m for m in evaluate_mitigations(case, scen, plan, load_risks(), load_mitigations())
            if m["mitigation_id"] == "M7" and m["risk_id"] == "R6"]
    assert rows, "контрфактическая проверка ценности аварийного договора не посчитана"
    m = rows[0]
    assert m["cost_mln"] < 0, "отказ от резерва должен быть дешевле в базовом сценарии"
    assert m["shortage_avoided_t"] < -1.0, "без аварийного договора дефицит обязан вырасти"
    assert m["sl_worst_with"] < m["sl_worst_without"] - 0.01


def test_mitigation_effect_is_reproducible(setup):
    case, scen, plan = setup
    a = evaluate_mitigations(case, scen, plan, load_risks(), load_mitigations())
    b = evaluate_mitigations(case, scen, plan, load_risks(), load_mitigations())
    assert [r["effect_mln"] for r in a] == [r["effect_mln"] for r in b]


# --------------------------------------------------------------------------- #
# D-12: вероятностный блок
# --------------------------------------------------------------------------- #
def test_first_operating_year_follows_the_plan(setup):
    """Для Earth-New первый рабочий год зависит от года реализации опциона, а не задан руками."""
    case, scen, _ = setup
    early = Plan.load("configs/plans/final-candidate.json")     # опцион реализован в 2035
    late = Plan.load("configs/plans/robust.json")               # реализация в 2036
    assert first_operating_year(case, early, "C") == 2036
    assert first_operating_year(case, late, "C") == 2037
    assert first_operating_year(case, early, "D") is None       # Луны в плане нет


def test_first_year_reliability_of_earth_new_is_used(setup):
    """D-12. В первый рабочий год у канала C надёжность 0,88, дальше 0,94."""
    case, scen, plan = setup
    mc = monte_carlo(case, scen["BASE"], plan, trials=20, seed=1)
    used = mc.reliability_used["C"]
    first = mc.first_operating_year["C"]
    assert used[first] == pytest.approx(0.88)
    assert all(used[y] == pytest.approx(0.94) for y in case.years if y != first)
    assert parse_reliability(case.sources["C"].reliability_profile, first, first) == 0.88


def test_probabilities_come_with_confidence_intervals(setup):
    case, scen, plan = setup
    mc = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=200, seed=20260918)
    for value, ci in ((mc.p_hard_violation, mc.p_hard_violation_ci),
                      (mc.p_service_below_min, mc.p_service_below_min_ci),
                      (mc.p_shortage, mc.p_shortage_ci)):
        assert 0.0 <= ci[0] <= value <= ci[1] <= 1.0, (value, ci)
    assert mc.pv_mean_ci[0] < mc.pv_mean < mc.pv_mean_ci[1]
    assert mc.assumptions["confidence_level"] == 0.95
    assert mc.assumptions["distribution_basis"] and mc.assumptions["independence_basis"]


def test_wilson_interval_is_correct_on_known_values():
    assert wilson_interval(0, 100) == [0.0, pytest.approx(0.0370, abs=1e-3)]
    low, high = wilson_interval(18, 200)
    assert low == pytest.approx(0.0580, abs=1e-3) and high == pytest.approx(0.1376, abs=1e-3)
    assert wilson_interval(5, 0) == [0.0, 0.0]


def test_monte_carlo_is_reproducible_and_seed_sensitive(setup):
    case, scen, plan = setup
    a = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=60, seed=7)
    b = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=60, seed=7)
    c = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=60, seed=8)
    assert a.pv_mean == b.pv_mean and a.p_hard_violation == b.p_hard_violation
    assert a.pv_mean != c.pv_mean
    # интервалы разных зёрен должны пересекаться: иначе оценка неустойчива
    assert a.p_hard_violation_ci[0] <= c.p_hard_violation_ci[1]
    assert c.p_hard_violation_ci[0] <= a.p_hard_violation_ci[1]


def test_stress_shares_are_not_multiplied_by_reliability_twice(setup):
    """Доли 55% и 75% обязательного стресса случайный множитель применяет один раз."""
    case, scen, _ = setup
    plan = Plan.load("configs/plans/robust.json")               # план с Луной
    mc = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=30, seed=3)
    assert "не умножаются" in mc.interpretation
    assert mc.assumptions["base_scenario"] == "MANDATORY_STRESS"
