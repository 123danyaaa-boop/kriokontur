"""Исследовательские сценарии: наследование, отсутствие двойного счёта, геополитика, Монте-Карло."""
import shutil
import tempfile
from pathlib import Path

import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import availability, run
from kriokontur.plan import Plan
from kriokontur.planner import auto_plan
from kriokontur.risks import evaluate_risks, geo_event, load_risks, monte_carlo, parse_reliability
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import PARAMS, metrics, sweep, threshold, tornado

INVEST = {"ZBO": 2036, "LUNAR_ISRU": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}


@pytest.fixture(scope="module")
def setup():
    case, scen = load_case(), load_all()
    plan = auto_plan(case, scen["MANDATORY_STRESS"], INVEST, "test-research")
    return case, scen, plan


def test_control_scenarios_are_untouched_by_research(setup):
    case, scen, _ = setup
    base, stress = scen["BASE"], scen["MANDATORY_STRESS"]
    assert base.status == "CASE_INPUT" and stress.status == "CASE_INPUT"
    assert stress.demand_total(case, 2038) == pytest.approx(case.demand_total[2038] * 1.15)
    assert base.demand_total(case, 2038) == case.demand_total[2038]


def test_combined_scenario_does_not_double_count_demand(setup):
    case, scen, _ = setup
    combined = scen["TEAM_COMBINED_STRESS_HIGH"]
    assert combined.demand_total(case, 2039) == pytest.approx(case.demand_high[2039])
    assert combined.demand_total(case, 2039) != pytest.approx(case.demand_high[2039] * 1.15)
    assert combined.delivery_factor("Lunar-ISRU", "D", 2038) == pytest.approx(0.55)
    assert combined.combination_rule


def test_geo_shock_does_not_stack_on_stress_price(setup):
    case, scen, _ = setup
    geo = scen["TEAM_GEO_WITH_STRESS"]
    assert geo.price_factor("Earth-Core", "A", 2039) == pytest.approx(1.25)   # год стресса
    assert geo.price_factor("Earth-Core", "A", 2040) == pytest.approx(1.40)   # год без стресса


def test_geo_builder_skips_years_already_shocked(setup):
    case, scen, plan = setup
    sc = geo_event(case, scen["MANDATORY_STRESS"], "тест", ["A"], 2038, 2040, 1.5)
    assert sc.price_factor("Earth-Core", "A", 2038) == pytest.approx(1.25)
    assert sc.price_factor("Earth-Core", "A", 2040) == pytest.approx(1.5)
    assert sc.status == "TEAM_RESEARCH" and "не затрагиваются" in sc.combination_rule


def test_capacity_shock_reduces_delivery_but_keeps_take_or_pay(setup):
    case, scen, plan = setup
    base = run(case, scen["BASE"], plan).year(2037)
    shock = run(case, scen["TEAM_RISK_CORE_OUTAGE"], plan).year(2037)
    assert shock.delivered_t["A"] < base.delivered_t["A"]
    assert shock.payable_t["A"] == pytest.approx(0.70 * plan.reserved("A", 2037))


def test_commissioning_delay_shifts_availability(setup):
    case, scen, plan = setup
    normal = availability(case, plan, scen["BASE"])["C"]
    delayed = availability(case, plan, scen["TEAM_RISK_EARTH_NEW_DELAY"])["C"]
    assert delayed == normal + 6


def test_threshold_finds_boundary_and_is_reproducible(setup):
    case, scen, plan = setup
    first = threshold(case, scen["BASE"], plan, "demand")
    second = threshold(case, scen["BASE"], plan, "demand")
    assert first["boundary"] == second["boundary"]
    if first["boundary"] is not None:
        below = metrics(run(case, *PARAMS["demand"].apply(case, scen["BASE"], plan, first["last_feasible"])[::-1][::-1]))
        assert below["feasible"] == 1.0


def test_sweep_and_tornado_return_sorted_effects(setup):
    case, scen, plan = setup
    rows = sweep(case, scen["BASE"], plan, "price_earth", steps=3)
    assert len(rows) == 3 and rows[0]["pv_mln"] < rows[-1]["pv_mln"]
    tor = tornado(case, scen["BASE"], plan, ["price_earth", "discount_rate"])
    assert tor[0]["swing"] >= tor[-1]["swing"]


def test_reliability_profiles_are_parsed():
    assert parse_reliability("constant:0.96", 2037, None) == 0.96
    assert parse_reliability("2038:0.78;2039:0.90;2040:0.93", 2039, None) == 0.90
    assert parse_reliability("first_operating_year:0.88;later:0.94", 2038, 2038) == 0.88
    assert parse_reliability("first_operating_year:0.88;later:0.94", 2039, 2038) == 0.94


def test_monte_carlo_is_deterministic_with_seed(setup):
    case, scen, plan = setup
    a = monte_carlo(case, scen["BASE"], plan, trials=25, seed=7)
    b = monte_carlo(case, scen["BASE"], plan, trials=25, seed=7)
    c = monte_carlo(case, scen["BASE"], plan, trials=25, seed=8)
    assert a.pv_mean == pytest.approx(b.pv_mean)
    assert a.trials == 25 and a.seed == 7 and a.assumptions["independence"]
    assert c.pv_mean != pytest.approx(a.pv_mean) or c.p_hard_violation != a.p_hard_violation


def test_risk_register_links_to_scenarios(setup):
    case, scen, plan = setup
    rows = evaluate_risks(case, scen, plan, load_risks())
    assert rows and all("delta_pv_mln" in r for r in rows)
    for row in rows:
        assert row["scenario_id"] in scen
        assert row["probability_basis"]


def test_extensibility_new_source_and_year_without_code_changes(setup):
    case, scen, plan = setup
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "data"
        shutil.copytree(Path(__file__).resolve().parents[1] / "data", copy)
        with (copy / "supply_sources.csv").open("a", encoding="utf-8") as fh:
            fh.write('X,Source-X,150,5.4,0.25,0.30,10,14,month,"constant:0.95",2037,TEAM_RESEARCH,"тест"\n')
        with (copy / "demand.csv").open("a", encoding="utf-8") as fh:
            fh.write("2041,470,300,376,587.5,TEAM_RESEARCH\n")
        ext = load_case(copy)
        assert len(ext.sources) == 6 and ext.years[-1] == 2041
        p = auto_plan(ext, scen["BASE"], INVEST, "ext")
        res = run(ext, scen["BASE"], p)
        assert len(res.months) == 84
        assert res.year(2041).demand_total_t == 470
        assert sum(res.year(y).ordered_t.get("X", 0.0) for y in ext.years) > 0
    assert len(load_case().sources) == 5     # исходный набор не тронут
