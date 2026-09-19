"""Расчёт на перспективу: продление горизонта, расширения мощностей, метрики окупаемости."""
import pytest

from kriokontur.caseinput import load_case
from kriokontur.engine import capex_schedule, fixed_opex, run, storage_mode
from kriokontur.horizon import (HorizonConfig, cost_per_served, cumulative_discounted, earth_ceiling,
                                extend_case, extrapolate_demand, first_deficit_year,
                                first_infeasible_year, payback_year)
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INVEST = {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035, "LUNAR_ISRU": 2036}


@pytest.fixture(scope="module")
def setup():
    return load_case(), load_all(), HorizonConfig.load()


def test_control_case_is_not_modified(setup):
    case, _, cfg = setup
    ext = extend_case(case, cfg, "increment", ["ZBO2", "LUNAR_ISRU_PHASE2"])
    assert case.years[-1] == 2040 and len(case.sources) == 5 and "ZBO2" not in case.storage
    assert ext.years[-1] == cfg.last_year and "D2" in ext.sources and "ZBO2" in ext.storage
    assert ext.version != case.version and case.version in ext.version


def test_extrapolation_methods(setup):
    case, _, cfg = setup
    inc = extrapolate_demand(case, 2045, "increment", cfg.methods["increment"])
    assert inc[2040] == case.demand_total[2040]
    assert inc[2041] == pytest.approx(460)          # 390 + последний прирост 70
    flat = extrapolate_demand(case, 2045, "plateau", cfg.methods["plateau"])
    assert flat[2045] == case.demand_total[2040]
    log = extrapolate_demand(case, 2048, "saturating", cfg.methods["saturating"])
    assert log[2041] > log[2040] and log[2048] < cfg.methods["saturating"]["ceiling"]


def test_critical_share_and_variants_are_kept(setup):
    case, _, cfg = setup
    ext = extend_case(case, cfg, "increment")
    share_2040 = case.demand_critical[2040] / case.demand_total[2040]
    assert ext.demand_critical[2043] / ext.demand_total[2043] == pytest.approx(share_2040, abs=1e-3)
    assert ext.demand_low[2043] / ext.demand_total[2043] == pytest.approx(cfg.low_factor, abs=1e-3)
    assert ext.demand_high[2043] / ext.demand_total[2043] == pytest.approx(cfg.high_factor, abs=1e-3)


def test_earth_ceiling_and_first_infeasible_year(setup):
    case, _, cfg = setup
    ext = extend_case(case, cfg, "increment", ["ZBO2"])
    assert earth_ceiling(ext) == pytest.approx(430)                 # 190 + 110 + 130
    assert earth_ceiling(ext, include_emergency=True) == pytest.approx(510)
    assert first_infeasible_year(ext, earth_ceiling(ext)) == 2041
    flat = extend_case(case, cfg, "plateau", ["ZBO2"])
    assert first_infeasible_year(flat, earth_ceiling(flat)) is None


def test_extended_case_runs_full_horizon(setup):
    case, scen, cfg = setup
    ext = extend_case(case, cfg, "increment", ["ZBO2", "LUNAR_ISRU_PHASE2"])
    plan = auto_plan(ext, scen["BASE"], {**INVEST, "ZBO2": 2041, "LUNAR_ISRU_PHASE2": 2041}, "h")
    res = run(ext, scen["BASE"], plan)
    assert len(res.months) == len(ext.years) * 12
    assert res.year(2045).demand_total_t == ext.demand_total[2045]
    assert res.year(2044).delivered_t.get("D2", 0) > 0          # вторая очередь реально работает


def test_generic_capex_and_opex_pick_up_new_investments(setup):
    case, scen, cfg = setup
    ext = extend_case(case, cfg, "increment", ["ZBO2", "LUNAR_ISRU_PHASE2"])
    plan = auto_plan(ext, scen["BASE"], {**INVEST, "ZBO2": 2041, "LUNAR_ISRU_PHASE2": 2041}, "h")
    capex = capex_schedule(ext, plan)
    assert capex[2041] == pytest.approx(ext.storage["ZBO2"].capex_mln + ext.investments["LUNAR_ISRU_PHASE2"].exercise_cost_mln)
    assert storage_mode(ext, plan, 2042).storage_id == "ZBO2"
    assert storage_mode(ext, plan, 2039).storage_id == "ZBO"
    opex_2044 = fixed_opex(ext, plan, 2044)
    assert opex_2044 == pytest.approx(ext.storage["ZBO2"].fixed_opex_mln_per_year
                                      + ext.investments["LUNAR_ISRU"].fixed_opex_mln_per_year
                                      + ext.investments["LUNAR_ISRU_PHASE2"].fixed_opex_mln_per_year)


def test_payback_ignores_years_before_investment():
    with_invest = {2035: 10, 2036: 60, 2037: 90, 2038: 110, 2039: 130}
    without = {2035: 12, 2036: 40, 2037: 80, 2038: 120, 2039: 160}
    assert payback_year(with_invest, without) == 2038


def test_cost_per_served_is_positive_and_uses_served_volume(setup):
    case, scen, cfg = setup
    ext = extend_case(case, cfg, "increment", ["ZBO2"])
    plan = auto_plan(ext, scen["BASE"], {**INVEST, "ZBO2": 2041}, "h")
    res = run(ext, scen["BASE"], plan)
    per = cost_per_served(res, ext.years[0], 0.08)
    cum = cumulative_discounted(res, ext.years[0], 0.08)
    assert all(v > 0 for v in per.values())
    assert per[2040] == pytest.approx(cum[2040] / sum(y.served_t for y in res.years if y.year <= 2040))
    assert first_deficit_year(res) is None or first_deficit_year(res) > 2040
