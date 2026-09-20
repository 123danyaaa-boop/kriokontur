"""D-14: что решается заранее, а что после наблюдения.

Реактивный диспетчер пересматривает месячный отбор по текущему состоянию сценария.
Для канала со сроком поставки 12 месяцев это верхняя граница качества управления.
Замороженный режим фиксирует годовой график в начале года: внутри года реагирует только
аварийный канал. Разница режимов и есть цена оперативной информации.
"""
import copy

import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import availability, frozen_schedule, run
from kriokontur.plan import Plan
from kriokontur.reporting import dispatch_comparison
from kriokontur.scenarios import load_all

SCENARIOS = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = Plan.load("configs/plans/final-candidate.json")
    frozen = copy.deepcopy(plan)
    frozen.assumptions = {**plan.assumptions, "dispatch_mode": "frozen"}
    return case, scen, plan, frozen


def test_default_mode_is_reactive_and_numbers_are_unchanged(setup):
    case, scen, plan, _ = setup
    assert plan.assume("dispatch_mode") == "reactive"
    assert run(case, scen["BASE"], plan).totals["discounted_cost_mln"] == pytest.approx(8918.71, abs=0.01)


def test_frozen_schedule_does_not_react_inside_the_year(setup):
    """В замороженном режиме месячный отбор канала постоянен внутри года работы канала."""
    case, scen, _, frozen = setup
    res = run(case, scen["MANDATORY_STRESS"], frozen)
    avail = availability(case, frozen, scen["MANDATORY_STRESS"])
    for sid in ("A", "B", "C"):
        for year in case.years:
            months = [m for m in res.months if m.year == year and m.index >= (avail.get(sid) or 10 ** 6)]
            values = [round(m.ordered_t[sid], 6) for m in months]
            if len(values) > 1 and max(values) > 0:
                assert max(values) - min(values) < 1e-6, f"{sid} {year}: график менялся внутри года {values}"


def test_reactive_schedule_does_react(setup):
    """Контроль от обратного: в реактивном режиме график внутри года меняется.

    Аудит зафиксировал это на канале A в 2037 году: отбор растёт с апреля, хотя заказ
    у канала со сроком поставки 12 месяцев так быстро не переразмещается.
    """
    case, scen, plan, _ = setup
    res = run(case, scen["MANDATORY_STRESS"], plan)
    varying = [(sid, y.year) for y in res.years for sid in ("A", "B", "C")
               if len({round(m.ordered_t[sid], 6) for m in res.months if m.year == y.year}) > 1]
    assert varying, "реактивный режим обязан менять график внутри года, иначе сравнение бессмысленно"
    assert ("A", 2037) in varying


def test_frozen_schedule_follows_the_annual_plan(setup):
    case, scen, _, frozen = setup
    avail = availability(case, frozen, scen["BASE"])
    schedule = frozen_schedule(case, frozen, scen["BASE"], avail)
    res = run(case, scen["BASE"], frozen)
    for sid in ("A", "B", "C"):
        for y in res.years:
            planned = schedule[sid].get(y.year, 0.0)
            # фактический отбор может быть меньше плана, если не хватило места в хранилище
            assert y.ordered_t[sid] <= planned + 1e-6, (sid, y.year)


def test_plan_stays_feasible_without_intra_year_foresight(setup):
    """Главная проверка: вывод не должен держаться на предвидении сценария."""
    case, scen, _, frozen = setup
    for sid in SCENARIOS:
        res = run(case, scen[sid], frozen)
        hard = [v for v in res.violations if v.severity == "hard"]
        assert not hard, f"{sid}: {[v.code for v in hard]}"
        assert res.totals["shortage_t"] < 0.1, sid


def test_value_of_information_is_reported_per_scenario(setup):
    case, scen, plan, _ = setup
    report = dispatch_comparison(case, scen, plan)
    rows = {r["scenario_id"]: r for r in report["rows"]}
    assert set(rows) == set(SCENARIOS)
    for r in rows.values():
        assert r["value_of_information_mln"] == pytest.approx(
            r["frozen_pv_mln"] - r["reactive_pv_mln"], abs=1e-9)
        assert r["frozen_feasible"]
    # в низком спросе возможность сократить заказ внутри года стоит дороже всего
    assert rows["LOW_DEMAND"]["value_of_information_mln"] > rows["BASE"]["value_of_information_mln"]
    assert report["reaction_time"]["Emergency"].startswith("6 недель")
    assert report["decided_in_advance"] and report["decided_after_observation"]


def test_dispatch_mode_is_validated(setup):
    from kriokontur.checks import validate_envelope
    case, _, plan, _ = setup
    env = plan.to_envelope()
    env["assumptions"] = {**env["assumptions"], "dispatch_mode": "oracle"}
    rows = validate_envelope(case, env)
    assert any(v.metric == "assumptions.dispatch_mode" for v in rows)
