"""VAL-01 / D-18: ручной пример на данных кейса, а не на синтетике.

Финальный план, сценарий BASE, 2035 год. Все константы выписаны из data/*.csv руками,
экономика года собирается формулами кейса из наблюдаемых физических потоков и сверяется
с прогоном. Если кто-то поменяет данные организатора или формулу, тест упадёт.
"""
import pytest

from kriokontur import rules
from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all

# --- константы CASE_INPUT, выписанные из data/*.csv ---
PRICE = {"A": 6.2, "B": 8.9, "C": 7.1, "D": 3.0, "E": 13.8}          # млн у.е./т
RES_RATE = {"A": 0.45, "B": 0.15, "C": 0.30, "D": 0.00, "E": 0.35}    # млн у.е. за т/год мощности
TOP = {"A": 0.70, "B": 0.00, "C": 0.50, "D": 0.00, "E": 0.00}
LOSS_BASE, HOLD_BASE, CAP_BASE = 0.045, 0.72, 70.0                    # хранилище BASE
DEMAND_2035, CRITICAL_2035 = 100.0, 80.0
OPTION_FEE, EXERCISE_COST = 90.0, 270.0                               # Earth-New, решение 2035
RATE = 0.08


@pytest.fixture(scope="module")
def run_2035():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    res = run(case, scen["BASE"], plan)
    return plan, res, res.year(2035), [m for m in res.months if m.year == 2035]


def test_case_constants_match_the_dataset():
    """Страховка: ручной пример опирается на те же числа, что лежат в data/*.csv."""
    case = load_case()
    for sid, price in PRICE.items():
        assert case.sources[sid].variable_cost_mln_per_t == price
        assert case.sources[sid].reservation_rate == RES_RATE[sid]
        assert case.sources[sid].take_or_pay_share == TOP[sid]
    assert case.storage["BASE"].loss_rate_on_throughput == LOSS_BASE
    assert case.storage["BASE"].holding_cost_mln_per_t_year == HOLD_BASE
    assert case.storage["BASE"].capacity_t == CAP_BASE
    assert case.demand_total[2035] == DEMAND_2035 and case.demand_critical[2035] == CRITICAL_2035


def test_demand_reserve_and_losses_2035(run_2035):
    _, _, y, months = run_2035
    assert y.demand_total_t == DEMAND_2035 and y.demand_critical_t == CRITICAL_2035
    assert y.reserve_required_t == pytest.approx(DEMAND_2035 * 45 / 365, abs=1e-9)   # 12,3288 т
    assert y.losses_t == pytest.approx(y.gross_t * LOSS_BASE, abs=1e-9)
    assert sum(m.losses_t for m in months) == pytest.approx(y.losses_t, abs=1e-9)


def test_material_balance_of_the_year_2035(run_2035):
    plan, _, y, months = run_2035
    opening = float(plan.inventory_policy["opening_stock_t"])
    assert y.opening_t == opening
    assert y.closing_t == pytest.approx(
        rules.closing_inventory(opening, y.gross_t, y.losses_t, y.served_t) - y.overflow_t, abs=1e-9)
    assert y.served_t == pytest.approx(DEMAND_2035, abs=1e-9)          # дефицита в 2035 нет
    assert y.shortage_t == pytest.approx(0.0, abs=1e-9)
    assert max(m.closing_t for m in months) <= CAP_BASE + 1e-9


def test_reservation_payment_2035(run_2035):
    plan, _, y, _ = run_2035
    # в 2035 доступны A, B и E весь год; C введён не будет, D не профинансирован
    expected = sum(RES_RATE[s] * plan.reserved(s, 2035) for s in ("A", "B", "E"))
    assert expected == pytest.approx(0.45 * 120.9 + 0.15 * 11.0 + 0.35 * 13.6, abs=1e-9)
    assert y.cost["reservation"] == pytest.approx(expected, abs=1e-9)


def test_procurement_payment_2035(run_2035):
    plan, _, y, _ = run_2035
    opening = float(plan.inventory_policy["opening_stock_t"])
    expected = opening * PRICE["A"]                     # закупка начального запаса, отнесена на 2035
    for sid in PRICE:
        payable = rules.payable_volume(y.ordered_t[sid], TOP[sid], plan.reserved(sid, 2035))
        assert y.payable_t[sid] == pytest.approx(payable, abs=1e-9)
        expected += PRICE[sid] * payable
    assert y.cost["procurement"] == pytest.approx(expected, abs=1e-9)


def test_holding_capex_and_total_2035(run_2035):
    _, res, y, months = run_2035
    expected_hold = sum((m.opening_t + m.gross_t - m.losses_t - m.overflow_t + m.closing_t) / 2
                        for m in months) / 12 * HOLD_BASE
    assert y.cost["holding"] == pytest.approx(expected_hold, abs=1e-9)
    assert y.cost["capex"] == pytest.approx(OPTION_FEE + EXERCISE_COST, abs=1e-9)
    assert y.cost["fixed_opex"] == pytest.approx(0.0, abs=1e-9)        # ZBO вводится только в 2036
    assert y.total_cost_mln == pytest.approx(sum(y.cost.values()), abs=1e-9)
    assert y.discounted_cost_mln == pytest.approx(y.total_cost_mln, abs=1e-9)   # t0 = 2035
    assert res.totals["discounted_cost_mln"] == pytest.approx(
        sum(yy.total_cost_mln / (1 + RATE) ** (yy.year - 2035) for yy in res.years), abs=1e-9)
