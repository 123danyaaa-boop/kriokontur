"""D-15, D-28: сравнение стратегий с лунным производством и без него.

Аудит зафиксировал противоречие: расчёт на перспективу советовал лунный канал, финальный план
его не включал, а сравнение считалось на автопланах, а не на самом финальном плане.
Здесь проверяется именно процедура сравнения: одинаковые условия, честное построение
альтернативы и воспроизводимость вывода.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.horizon import HorizonConfig, cumulative_discounted, extend_case
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("strategy_decision", ROOT / "scripts/strategy_decision.py")
strategy = importlib.util.module_from_spec(SPEC)
sys.modules["strategy_decision"] = strategy
SPEC.loader.exec_module(strategy)

CONTROL = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    swap = strategy.build_isru_variant(case, scen, plan, 2037, release_earth=True)
    add = strategy.build_isru_variant(case, scen, plan, 2037, release_earth=False)
    return case, scen, plan, swap, add


def test_isru_is_financed_in_time(setup):
    """Финансирование до 2038 года: иначе кейс даёт нарушение ISRU_FUNDING_LATE."""
    case, scen, _, swap, add = setup
    for variant in (swap, add):
        assert variant.investments["LUNAR_ISRU"] == 2037
        res = run(case, scen["BASE"], variant)
        assert "ISRU_FUNDING_LATE" not in {v.code for v in res.violations}


def test_substitution_variant_keeps_total_capacity(setup):
    """Замещающий вариант не добавляет мощность: сравниваются только деньги.

    Сравнивается законтрактованный объём (резерв × доля года доступности), а не сырой резерв:
    лунный канал вводится в марте 2038 года, поэтому его годовой резерв 120 т даёт 100 т договора.
    """
    case, scen, plan, swap, _ = setup
    base_run = run(case, scen["MANDATORY_STRESS"], plan)
    swap_run = run(case, scen["MANDATORY_STRESS"], swap)
    for a, b in zip(base_run.years, swap_run.years):
        base_cap = sum(a.contracted_t[s] for s in ("A", "B", "C", "D"))
        swap_cap = sum(b.contracted_t[s] for s in ("A", "B", "C", "D"))
        assert swap_cap == pytest.approx(base_cap, abs=0.15), a.year


def test_expansion_variant_adds_capacity(setup):
    """Расширяющий вариант оставляет земные договоры и добавляет лунную мощность."""
    case, scen, plan, _, add = setup
    for s in ("A", "B", "C"):
        for year in case.years:
            assert add.reserved(s, year) == pytest.approx(plan.reserved(s, year))
    assert add.reserved("D", 2038) > 0 and plan.reserved("D", 2038) == 0


def test_all_variants_are_feasible_in_control_scenarios(setup):
    case, scen, plan, swap, add = setup
    for variant in (plan, swap, add):
        for sid in CONTROL:
            res = run(case, scen[sid], variant)
            assert res.feasible, (variant.plan_id, sid, [v.code for v in res.violations])


def test_moon_is_more_expensive_inside_the_mandatory_horizon(setup):
    """Вывод, на котором держится решение: в горизонте кейса Луна дороже, кроме высокого спроса."""
    case, scen, plan, swap, _ = setup
    pv = {}
    for sid in CONTROL:
        pv[sid] = (run(case, scen[sid], plan).totals["discounted_cost_mln"],
                   run(case, scen[sid], swap).totals["discounted_cost_mln"])
    for sid in ("BASE", "MANDATORY_STRESS", "LOW_DEMAND"):
        assert pv[sid][1] > pv[sid][0], f"{sid}: лунный вариант обязан быть дороже в горизонте кейса"
    assert pv["HIGH_DEMAND"][1] < pv["HIGH_DEMAND"][0], "в высоком спросе лунный вариант выигрывает"


def test_moon_pays_back_right_after_the_horizon(setup):
    """За 2040 годом замещающий лунный вариант дешевле при всех трёх гипотезах спроса."""
    case, scen, plan, swap, _ = setup
    cfg = HorizonConfig.load()
    rate = float(plan.assume("discount_rate"))
    for method in cfg.methods:
        rows, cums = strategy.horizon_table(case, scen, [("без", plan), ("с", swap)], cfg, rate)
        assert cums[(method, "с")][cfg.last_year] < cums[(method, "без")][cfg.last_year], method


def test_expansion_variant_serves_more_beyond_the_horizon(setup):
    """Расширение мощности имеет смысл только за горизонтом: там оно снимает дефицит."""
    case, scen, plan, _, add = setup
    cfg = HorizonConfig.load()
    ext = extend_case(case, cfg, "increment", ["ZBO2"])
    served = {}
    for label, variant in (("без", plan), ("расширение", add)):
        long_plan = strategy.copy.deepcopy(variant)
        long_plan.investments = {**long_plan.investments, "ZBO2": 2041}
        for sid in ("A", "B", "C", "D", "E"):
            last = long_plan.reserved(sid, case.years[-1])
            for year in ext.years:
                if year > case.years[-1]:
                    long_plan.reservations.setdefault(sid, {})[year] = last
        res = run(ext, scen["BASE"], long_plan)
        served[label] = res.totals["served_t"]
    assert served["расширение"] > served["без"] + 100


def test_2040_headroom_is_zero_without_the_moon(setup):
    """D-28: в 2040 году в стрессе земные каналы законтрактованы под завязку."""
    case, scen, plan, _, add = setup
    stress = run(case, scen["MANDATORY_STRESS"], plan)
    y = stress.year(2040)
    contracted = sum(y.contracted_t[s] for s in ("A", "B", "C", "D"))
    offtake = sum(y.ordered_t[s] for s in ("A", "B", "C", "D"))
    assert contracted == pytest.approx(430.0, abs=0.5)
    assert offtake == pytest.approx(contracted, abs=0.5), "свободной земной мощности в 2040 нет"
    assert y.demand_total_t > contracted, "спрос 2040 года выше законтрактованной мощности"
    with_moon = run(case, scen["MANDATORY_STRESS"], add).year(2040)
    headroom = (sum(with_moon.contracted_t[s] for s in ("A", "B", "C", "D"))
                - sum(with_moon.ordered_t[s] for s in ("A", "B", "C", "D")))
    assert headroom > 50, "расширяющий вариант обязан давать запас мощности в 2040 году"


def test_comparison_is_reproducible(setup):
    case, scen, plan, _, _ = setup
    a = strategy.build_isru_variant(case, scen, plan, 2037)
    b = strategy.build_isru_variant(case, scen, plan, 2037)
    assert a.reservations == b.reservations and a.investments == b.investments
    assert (run(case, scen["BASE"], a).totals["discounted_cost_mln"]
            == run(case, scen["BASE"], b).totals["discounted_cost_mln"])
