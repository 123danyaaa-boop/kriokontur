"""D-09, D-25: контрактно-финансовая архитектура и договорный эквивалент резерва.

Главные свойства: карточка договора не заводит вторую копию контрольных данных,
обязательства сходятся с финансовым блоком прогона, а контрактный резерв проверяется
по сроку из данных кейса и по размеру партии, заданному договором.
"""
import pytest
import yaml

from kriokontur.caseinput import load_case
from kriokontur.contracts import (CONTRACTS_PATH, contract_card, emergency_reserve_check,
                                  load_contracts, obligations, obligations_totals)
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    return case, scen, plan


def test_every_source_has_a_contract(setup):
    case, _, _ = setup
    contracts = load_contracts()
    assert {c.source_id for c in contracts} == set(case.sources)
    for c in contracts:
        assert c.counterparty and c.payment_terms and c.liability and c.revision_rule and c.risk_allocation


def test_contract_numbers_come_from_case_input(setup):
    """Числа карточки обязаны совпадать с data/supply_sources.csv, а не жить в YAML отдельно."""
    case, _, _ = setup
    for c in load_contracts():
        card = contract_card(case, c)
        s = case.sources[c.source_id]
        assert card["capacity_t_per_year"] == s.capacity_t_per_year
        assert card["price_mln_per_t"] == s.variable_cost_mln_per_t
        assert card["take_or_pay_share"] == s.take_or_pay_share
        assert card["reservation_rate_mln_per_t_year"] == s.reservation_rate
        assert card["available_from_year"] == s.available_from_year


def test_contract_file_cannot_shadow_case_input(tmp_path=None):
    """Попытка записать условие кейса в contracts.yaml должна отклоняться на загрузке."""
    from kriokontur import paths
    rows = yaml.safe_load(CONTRACTS_PATH.read_text(encoding="utf-8"))
    rows[0]["capacity_t_per_year"] = 999
    target = paths.RESULTS / "test-contracts-shadow.yaml"
    try:
        target.write_text(yaml.safe_dump(rows, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match="CASE_INPUT"):
            load_contracts(target)
    finally:
        target.unlink(missing_ok=True)


def test_obligations_reconcile_with_the_run(setup):
    """Сумма договорных платежей = закупки плюс резерв прогона (без закупки начального запаса)."""
    case, scen, plan = setup
    for sid in ("BASE", "MANDATORY_STRESS", "HIGH_DEMAND"):
        res = run(case, scen[sid], plan)
        totals = obligations_totals(obligations(case, res))
        expected = (res.totals["procurement"] + res.totals["reservation"]
                    - sum(y.opening_stock_cost_mln for y in res.years))
        assert sum(t["total_payment_mln"] for t in totals.values()) == pytest.approx(expected, abs=1e-9), sid


def test_obligations_show_offtake_against_contract(setup):
    case, scen, plan = setup
    res = run(case, scen["MANDATORY_STRESS"], plan)
    rows = obligations(case, res)
    for r in rows:
        assert r["offtake_t"] <= r["contracted_t"] + 1e-6
        assert r["payable_t"] >= r["offtake_t"] - 1e-6
        assert r["unused_paid_t"] == pytest.approx(max(0.0, r["payable_t"] - r["offtake_t"]), abs=1e-9)
        assert r["take_or_pay_minimum_t"] == pytest.approx(
            case.sources[r["source_id"]].take_or_pay_share * r["contracted_t"], abs=1e-9)


def test_emergency_reserve_uses_lead_time_in_days_from_the_case(setup):
    """D-25. Срок ожидания 42 дня (6 недель), а не 61 день от округления до месячных шагов."""
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    y = res.year(2040)
    check = emergency_reserve_check(case, plan, 2040, y.demand_total_t, y.opening_t)
    assert check["lead_time_days"] == pytest.approx(42.0)
    assert check["waiting_cover_needed_t"] == pytest.approx(y.demand_total_t * 42 / 365, abs=1e-9)
    assert check["required_reserve_t"] == pytest.approx(y.demand_total_t * 45 / 365, abs=1e-9)


def test_contract_reserve_is_infeasible_with_default_batch_policy(setup):
    """При партии «месячная доля × шаги поставки» эквивалент недостижим уже с 2036 года."""
    case, scen, _ = setup
    plan = Plan.load("configs/plans/final-candidate.json")
    plan.inventory_policy = {**plan.inventory_policy, "mode": "contract"}
    plan.reservations["E"] = {y: case.sources["E"].capacity_t_per_year for y in case.years}
    res = run(case, scen["BASE"], plan)
    viol = [v for v in res.violations if v.code == "RESERVE_EQUIVALENCE_NOT_PROVEN"]
    assert viol, "эквивалент не может считаться доказанным при партии 13,3 т против требования 17,3 т"
    assert all(v.period != 2035 for v in viol), "в 2035 требование 12,3 т партия 13,3 т закрывает"
    assert "максимально доступная партия" in viol[0].message


def test_contract_reserve_becomes_feasible_with_an_explicit_batch(setup):
    """Договор может задать размер партии: тогда эквивалент доказуем и это видно расчётом."""
    case, scen, _ = setup
    plan = Plan.load("configs/plans/final-candidate.json")
    plan.inventory_policy = {**plan.inventory_policy, "mode": "contract"}
    plan.reservations["E"] = {y: case.sources["E"].capacity_t_per_year for y in case.years}
    plan.assumptions["emergency_batch_t"] = 60.0
    res = run(case, scen["BASE"], plan)
    assert not [v for v in res.violations if v.code == "RESERVE_EQUIVALENCE_NOT_PROVEN"]
    check = emergency_reserve_check(case, plan, 2040, res.year(2040).demand_total_t,
                                    res.year(2040).opening_t)
    assert check["equivalent"] and check["batch_policy"] == "договорный размер партии"


def test_explicit_batch_does_not_break_the_annual_contract_limit(setup):
    """Большая партия не даёт превысить годовой договор: годовой лимит проверяется отдельно."""
    case, scen, _ = setup
    plan = Plan.load("configs/plans/base-only.json")
    plan.assumptions["emergency_batch_t"] = 80.0
    for sid in ("BASE", "MANDATORY_STRESS", "HIGH_DEMAND"):
        res = run(case, scen[sid], plan)
        for y in res.years:
            assert y.ordered_t["E"] <= plan.reserved("E", y.year) + 1e-6
            assert y.ordered_t["E"] <= case.sources["E"].capacity_t_per_year + 1e-6
