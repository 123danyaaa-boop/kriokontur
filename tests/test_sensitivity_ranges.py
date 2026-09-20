"""D-16: чувствительность с обоснованными диапазонами, совместными изменениями и порогом.

Аудит зафиксировал: торнадо строилось на ±10% без обоснования, метрики запаса не было,
совместные изменения не проверялись, порога «стратегия уступает альтернативе» не существовало.
"""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import PARAMS, grid, metrics, reverse_stress, switch_point, sweep, tornado
from kriokontur.engine import run


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    return case, scen, plan


def test_every_parameter_range_has_a_basis(setup):
    for key, p in PARAMS.items():
        assert p.basis and len(p.basis) > 30, f"{key}: диапазон без обоснования"
        assert p.low < p.high, key
        assert p.low <= p.base <= p.high, f"{key}: базовое значение вне диапазона"


def test_ranges_are_anchored_in_case_data(setup):
    """Границы спроса и поставки Луны обязаны совпадать с рядами и долями кейса."""
    case, _, _ = setup
    demand = PARAMS["demand"]
    low_ratio = min(case.demand_low[y] / case.demand_total[y] for y in case.years)
    high_ratio = max(case.demand_high[y] / case.demand_total[y] for y in case.years)
    assert demand.low == pytest.approx(low_ratio, abs=1e-9)
    assert demand.high == pytest.approx(high_ratio, abs=1e-9)
    assert PARAMS["isru_delivery"].low == pytest.approx(0.55)      # худшая доля обязательного стресса
    assert PARAMS["earth_new_prep"].low == 18 and PARAMS["earth_new_prep"].high == 24
    assert PARAMS["opening_stock"].high == case.storage["BASE"].capacity_t


def test_metrics_include_stock_and_shortage(setup):
    case, scen, plan = setup
    m = metrics(run(case, scen["BASE"], plan))
    for key in ("min_closing_t", "reserve_margin_min_t", "shortage_t", "sl_total_worst",
                "unused_paid_t", "cost_per_served_t"):
        assert key in m, key


def test_two_factor_grid_finds_the_feasible_region(setup):
    case, scen, plan = setup
    g = grid(case, scen["BASE"], plan, "demand", "core_capacity", steps=4)
    assert g["total_cells"] == 16
    assert 0 < g["feasible_cells"] < 16, "сетка должна показывать границу, а не быть одноцветной"
    assert g["first_infeasible"] is not None
    assert g["x"]["basis"] and g["y"]["basis"]
    # план исполним в углу «низкий спрос, полная мощность» и неисполним в противоположном
    assert g["cells"][-1][0]["feasible"] is True
    assert g["cells"][0][-1]["feasible"] is False


def test_joint_change_breaks_earlier_than_single_ones(setup):
    """Смысл совместной проверки: вместе два умеренных отклонения ломают план, по одному нет.

    Пороги по одному параметру: спрос 1,103 и мощность Earth-Core 0,812. Значения 1,05 и 0,85
    лежат внутри обоих порогов, но вместе выводят план за требование 45-дневного резерва.
    """
    case, scen, plan = setup

    def outcome(demand=None, capacity=None):
        sc, pl = scen["BASE"], plan
        if demand is not None:
            sc, pl = PARAMS["demand"].apply(case, sc, pl, demand)
        if capacity is not None:
            sc, pl = PARAMS["core_capacity"].apply(case, sc, pl, capacity)
        return metrics(run(case, sc, pl))

    only_demand = outcome(demand=1.05)
    only_capacity = outcome(capacity=0.85)
    together = outcome(demand=1.05, capacity=0.85)
    assert only_demand["feasible"] == 1.0, "спрос 1,05 по одному не ломает план"
    assert only_capacity["feasible"] == 1.0, "мощность 0,85 по одному не ломает план"
    assert together["feasible"] == 0.0, "совместное изменение обязано ломать план раньше одиночных"
    assert "RESERVE_45D_NOT_MET" in together["codes"]


def test_reverse_stress_reports_thresholds_with_codes(setup):
    case, scen, plan = setup
    rows = {r["key"]: r for r in reverse_stress(case, scen["BASE"], plan)}
    demand = rows["demand"]
    assert demand["boundary"] == pytest.approx(1.103, abs=0.01)
    assert demand["first_violation"] == "RESERVE_45D_NOT_MET"
    assert rows["core_capacity"]["boundary"] == pytest.approx(0.812, abs=0.01)
    for r in rows.values():
        # у найденного порога раскрывается метод, у ненайденного — что диапазон пройден целиком
        assert ("монотонности" in r["note"]) == (r["boundary"] is not None)


def test_switch_point_compares_two_strategies(setup):
    """Порог, после которого выбранная стратегия уступает плану с лунным каналом.

    До порога финальный план дешевле и обслуживает спрос полностью, после порога он начинает
    недопоставлять раньше альтернативы — и дешевизна перестаёт иметь значение.
    """
    case, scen, plan = setup
    alternative = Plan.load("configs/plans/robust.json")
    res = switch_point(case, scen["MANDATORY_STRESS"], plan, alternative, "demand")
    assert res["basis"]
    assert res["low"]["plan_pv_mln"] < res["low"]["alternative_pv_mln"]
    assert res["low"]["decided_by"] == "приведённые расходы" and not res["low"]["alternative_wins"]
    assert res["switch_value"] == pytest.approx(1.113, abs=0.02)
    assert res["at_switch"]["alternative_wins"]
    assert res["at_switch"]["decided_by"] in ("недопоставка", "исполнимость")
    assert res["high"]["alternative_shortage_t"] < res["high"]["plan_shortage_t"]


def test_switch_point_finds_a_real_threshold_when_it_exists(setup):
    """Контроль от обратного: если альтернатива где-то выигрывает, порог обязан найтись."""
    case, scen, plan = setup
    weak = Plan.load("configs/plans/final-candidate.json")
    weak.reservations["E"] = {y: 0.0 for y in case.years}        # план без аварийного договора
    res = switch_point(case, scen["BASE"], weak, plan, "core_capacity")
    assert res["switch_value"] is not None, "при падении мощности план с аварийным договором обязан выигрывать"
    assert res["at_switch"]["alternative_wins"]
    # при полной мощности дешевле план без аварийного договора, при падении выигрывает план с ним
    assert res["low"]["decided_by"] == "недопоставка" and res["low"]["alternative_wins"]
    assert res["high"]["decided_by"] == "приведённые расходы" and not res["high"]["alternative_wins"]


def test_tornado_uses_the_disclosed_ranges(setup):
    case, scen, plan = setup
    rows = tornado(case, scen["BASE"], plan, ["demand", "price_earth", "opening_stock"], delta=0.10)
    by_key = {r["key"]: r for r in rows}
    assert by_key["demand"]["low_value"] >= PARAMS["demand"].low
    assert by_key["demand"]["high_value"] <= PARAMS["demand"].high
    assert all(r["swing"] >= 0 for r in rows)


def test_sweep_covers_the_full_disclosed_range(setup):
    case, scen, plan = setup
    rows = sweep(case, scen["BASE"], plan, "emergency_reserve", steps=3)
    assert rows[0]["value"] == pytest.approx(PARAMS["emergency_reserve"].low)
    assert rows[-1]["value"] == pytest.approx(PARAMS["emergency_reserve"].high)
    # отказ от аварийного договора дешевле, но хуже по устойчивости
    assert rows[0]["pv_mln"] < rows[-1]["pv_mln"]
