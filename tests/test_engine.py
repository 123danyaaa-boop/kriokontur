"""Инварианты расчёта: баланс, единицы, отсутствие двойного счёта, реакция на сценарий."""
import pytest

from kriokontur import rules
from kriokontur.caseinput import load_case
from kriokontur.engine import run, availability
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INVEST = {"ZBO": 2036, "LUNAR_ISRU": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}


@pytest.fixture(scope="module")
def setup():
    case = load_case()
    scen = load_all()
    plan = auto_plan(case, scen["MANDATORY_STRESS"], INVEST, "test", "Тестовый план")
    return case, scen, plan


def test_material_balance_holds_every_month(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    stock = plan.inventory_policy["opening_stock_t"]
    for m in res.months:
        # перелив входит в баланс отдельной статьёй, а не срезается молча (D-04)
        expected = rules.closing_inventory(stock, m.gross_t, m.losses_t, m.served_t) - m.overflow_t
        assert m.closing_t == pytest.approx(expected, abs=1e-6)
        assert m.closing_t <= m.capacity_t + 1e-6
        stock = m.closing_t


def test_inventory_never_negative_and_within_capacity(setup):
    case, scen, plan = setup
    for name in ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND"):
        res = run(case, scen[name], plan)
        assert min(m.closing_t for m in res.months) >= -1e-9
        assert all(m.closing_t <= m.capacity_t + 1e-6 for m in res.months)


def test_losses_are_charged_once_on_throughput(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    for y in res.years:
        rate = 0.012 if y.year >= plan.investments["ZBO"] else 0.045
        assert y.losses_t == pytest.approx(y.gross_t * rate, rel=1e-9)


def test_critical_demand_is_subset_of_total(setup):
    case, scen, plan = setup
    res = run(case, scen["MANDATORY_STRESS"], plan)
    for y in res.years:
        assert y.demand_critical_t <= y.demand_total_t
        assert y.served_critical_t <= y.served_t + 1e-9


def test_stress_changes_demand_price_and_isru_delivery(setup):
    case, scen, plan = setup
    base, stress = run(case, scen["BASE"], plan), run(case, scen["MANDATORY_STRESS"], plan)
    assert stress.year(2038).demand_total_t == pytest.approx(base.year(2038).demand_total_t * 1.15)
    assert stress.year(2037).demand_total_t == pytest.approx(base.year(2037).demand_total_t)
    ordered = stress.year(2038).ordered_t["D"]
    assert stress.year(2038).delivered_t["D"] == pytest.approx(ordered * 0.55)


def test_take_or_pay_minimum_is_paid_even_with_low_offtake(setup):
    case, scen, plan = setup
    plan2 = auto_plan(case, scen["BASE"], INVEST, "top", "ToP")
    plan2.orders["A"] = {2036: 10.0}          # берём мало при большом резервировании
    res = run(case, scen["BASE"], plan2)
    year = res.year(2036)
    reserved = plan2.reserved("A", 2036)
    assert year.payable_t["A"] == pytest.approx(max(year.ordered_t["A"], 0.70 * reserved), rel=1e-6)


def test_reservation_payment_is_prorated_for_partial_year(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    avail = availability(case, plan)
    assert avail["C"] is not None and avail["C"] % 12 != 0     # ввод в середине года
    year_index = avail["C"] // 12
    year = res.years[year_index]
    fraction = (12 - avail["C"] % 12) / 12
    expected = case.sources["C"].reservation_rate * plan.reserved("C", year.year) * fraction
    # платёж по каналу публикуется движком, поэтому проверяем точное равенство, а не «не меньше»
    assert year.reservation_payment_mln["C"] == pytest.approx(expected, abs=1e-9)
    assert year.cost["reservation"] == pytest.approx(sum(year.reservation_payment_mln.values()), abs=1e-9)


def test_discounting_uses_disclosed_rate(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    rate = plan.assumptions["discount_rate"]
    for y in res.years:
        assert y.discounted_cost_mln == pytest.approx(y.total_cost_mln / (1 + rate) ** (y.year - 2035))


def test_service_levels_are_shares(setup):
    case, scen, plan = setup
    res = run(case, scen["BASE"], plan)
    assert 0 <= res.totals["sl_total"] <= 1 and 0 <= res.totals["sl_critical"] <= 1


def test_plan_envelope_roundtrip(setup):
    case, scen, plan = setup
    from kriokontur.plan import Plan
    again = Plan.from_envelope(plan.to_envelope())
    assert again.reservations == plan.reservations
    assert again.investments == plan.investments
    r1, r2 = run(case, scen["BASE"], plan), run(case, scen["BASE"], again)
    assert r1.totals["total_cost_mln"] == pytest.approx(r2.totals["total_cost_mln"])
