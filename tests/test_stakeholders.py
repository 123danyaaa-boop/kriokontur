"""D-08: интересы сторон, связанные с метриками расчёта.

Проверяется не наличие текста, а то, что положение стороны считается из прогона и меняется
в стрессе и при риске. Отдельно проверяется, что ухудшение определяется по направлению
метрики: рост платежей поставщику это не ухудшение для поставщика.
"""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all
from kriokontur.stakeholders import METRICS, export_rows, impact, load_stakeholders, metric_values


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    return case, scen, plan


def test_every_stakeholder_is_described_and_measurable():
    stakeholders = load_stakeholders()
    assert len(stakeholders) >= 5
    ids = {s.stakeholder_id for s in stakeholders}
    assert {"OPERATOR", "CRITICAL", "COMMERCIAL", "INVESTOR"} <= ids
    for s in stakeholders:
        assert s.role and s.gets and s.pays and s.bears_risk and s.requires
        assert s.metrics, f"{s.stakeholder_id}: нет ни одной метрики"
        assert all(k in METRICS for k in s.metrics), f"{s.stakeholder_id}: неизвестная метрика"
        assert s.obligations, f"{s.stakeholder_id}: не связан ни с одним договором"


def test_stakeholder_obligations_point_to_real_contracts():
    from kriokontur.contracts import load_contracts
    known = {c.contract_id for c in load_contracts()}
    for s in load_stakeholders():
        assert set(s.obligations) <= known, (s.stakeholder_id, s.obligations)


def test_metrics_are_taken_from_the_run(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    values = metric_values(case, res, ["pv_mln", "sl_critical_worst", "capex_total_mln", "shortage_t"])
    assert values["pv_mln"]["value"] == pytest.approx(res.totals["discounted_cost_mln"], abs=1e-9)
    assert values["capex_total_mln"]["value"] == pytest.approx(res.totals["capex"], abs=1e-9)
    assert values["shortage_t"]["value"] == pytest.approx(res.totals["shortage_t"], abs=1e-9)
    assert values["sl_critical_worst"]["value"] == pytest.approx(
        min(y.sl_critical for y in res.years), abs=1e-12)


def test_impact_shows_change_between_baseline_and_stress(setup):
    case, scen, plan = setup
    result = impact(case, scen, plan, scenario_ids=["BASE", "MANDATORY_STRESS"], risk_scenarios=[])
    assert result["scenario_ids"] == ["BASE", "MANDATORY_STRESS"]
    operator = next(s for s in result["stakeholders"] if s["stakeholder_id"] == "OPERATOR")
    base_pv = operator["scenarios"]["BASE"]["pv_mln"]["value"]
    stress_pv = operator["scenarios"]["MANDATORY_STRESS"]["pv_mln"]["value"]
    assert stress_pv > base_pv
    assert operator["scenarios"]["MANDATORY_STRESS"]["pv_mln"]["delta_vs_baseline"] == pytest.approx(
        stress_pv - base_pv, abs=1e-9)
    # для оператора рост расходов это ухудшение
    assert operator["scenarios"]["MANDATORY_STRESS"]["pv_mln"]["worse"] is True
    # для поставщика рост платежей это не ухудшение: направление метрики другое
    supplier = next(s for s in result["stakeholders"] if s["stakeholder_id"] == "EARTH_SUPPLIERS")
    payments = supplier["scenarios"]["MANDATORY_STRESS"]["earth_payments_mln"]
    assert payments["delta_vs_baseline"] > 0 and payments["worse"] is False


def test_impact_covers_team_risk_scenarios(setup):
    """Кейс требует показать, что происходит со сторонами при реализации риска."""
    case, scen, plan = setup
    risks = [sid for sid in scen if sid.startswith("TEAM_RISK_")]
    assert risks
    result = impact(case, scen, plan, scenario_ids=["BASE"], risk_scenarios=risks)
    for st in result["stakeholders"]:
        for sid in risks:
            assert sid in st["scenarios"], (st["stakeholder_id"], sid)
    operator = next(s for s in result["stakeholders"] if s["stakeholder_id"] == "OPERATOR")
    deltas = [operator["scenarios"][sid]["pv_mln"]["delta_vs_baseline"] for sid in risks]
    assert any(abs(d) > 1e-6 for d in deltas), "ни один риск не меняет положение оператора"


def test_export_rows_are_flat_and_complete(setup):
    case, scen, plan = setup
    result = impact(case, scen, plan, scenario_ids=["BASE", "MANDATORY_STRESS"], risk_scenarios=[])
    rows = export_rows(result)
    header = rows[0]
    assert header[0] == "stakeholder_id" and "delta_vs_baseline" in header
    body = rows[1:]
    assert len(body) == sum(len(s.metrics) for s in load_stakeholders()) * 2
    assert all(len(r) == len(header) for r in body)
