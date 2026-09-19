"""D-04: переполнение хранилища не стирает топливо из баланса.

Баланс каждого месяца: I_end = I_start + Q_delivered − Losses − Q_served − Overflow.
До правки излишек просто обрезался присваиванием `stock = capacity`, и 80 т исчезали.
"""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all

PLANS = ("final-candidate", "base-only", "robust", "robust-plus", "no-lunar")
SCENARIOS = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")


@pytest.fixture(scope="module")
def case_and_scenarios():
    return load_case(), load_all()


def test_monthly_balance_closes_with_overflow_term(case_and_scenarios):
    case, scen = case_and_scenarios
    for name in PLANS:
        plan = Plan.load(f"configs/plans/{name}.json")
        for sid in SCENARIOS:
            res = run(case, scen[sid], plan)
            stock = float(plan.inventory_policy["opening_stock_t"])
            for m in res.months:
                expected = stock + m.gross_t - m.losses_t - m.served_t - m.overflow_t
                assert m.closing_t == pytest.approx(expected, abs=1e-9), f"{name} {sid} {m.year}-{m.month}"
                stock = m.closing_t


def test_overflow_is_reported_and_balanced_when_it_happens(case_and_scenarios):
    """Исследовательский сценарий «поставщик привёз больше заказа» переполняет хранилище.

    Проверяем три вещи: перелив посчитан, он попал в баланс, нарушение STORAGE_OVERFLOW выдано
    с годом и величиной. Это единственный физически возможный путь переполнения после того,
    как начальный запас сверх ёмкости отклоняется на входе (D-03).
    """
    case, scen = case_and_scenarios
    plan = Plan.load("configs/plans/final-candidate.json")
    over = scen["BASE"].derive("TEAM_TEST_OVERDELIVERY", "Тест: поставка сверх заказа",
                               delivery_share={"Earth-Core": {y: 3.0 for y in case.years}})
    res = run(case, over, plan)
    assert res.totals["overflow_t"] > 0
    viol = [v for v in res.violations if v.code == "STORAGE_OVERFLOW"]
    assert viol and viol[0].severity == "hard" and viol[0].excess > 0

    stock = float(plan.inventory_policy["opening_stock_t"])
    for m in res.months:
        assert m.closing_t == pytest.approx(stock + m.gross_t - m.losses_t - m.served_t - m.overflow_t, abs=1e-9)
        assert m.closing_t <= m.capacity_t + 1e-6
        stock = m.closing_t
    assert sum(m.overflow_t for m in res.months) == pytest.approx(res.totals["overflow_t"], abs=1e-9)


def test_control_plans_have_no_overflow(case_and_scenarios):
    """Планы команды не переполняют хранилище: диспетчер не заказывает сверх свободного объёма."""
    case, scen = case_and_scenarios
    for name in PLANS:
        plan = Plan.load(f"configs/plans/{name}.json")
        for sid in SCENARIOS:
            res = run(case, scen[sid], plan)
            assert res.totals["overflow_t"] == pytest.approx(0.0, abs=1e-9), f"{name} × {sid}"
