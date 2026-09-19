"""HTTP-интерфейс расчётного ядра. Фронтенд не считает ничего сам: он только рисует.

Запуск:  uvicorn kriokontur.api:app --reload --port 8000
Эндпоинты:
    GET  /api/case                  CASE_INPUT и версия набора
    GET  /api/params                параметры для анализа чувствительности
    POST /api/sensitivity/sweep     развёртка по параметру
    POST /api/sensitivity/threshold порог, после которого план нарушает ограничение
    POST /api/sensitivity/tornado   вклад параметров в приведённые расходы
    POST /api/sensitivity/reverse   обратный стресс по набору параметров
    POST /api/risks                 реестр рисков с посчитанными последствиями
    POST /api/geo                   геополитическое событие, заданное пользователем
    POST /api/montecarlo            вероятностный блок с раскрытой трактовкой надёжности
    GET  /api/scenarios             список сценариев
    GET  /api/selftest              контрольные примеры V01-V10
    POST /api/autoplan              эвристический стартовый план
    POST /api/run                   расчёт плана в сценарии
    POST /api/compare               один план в нескольких сценариях
    POST /api/plans                 сохранить план
    GET  /api/plans                 список сохранённых планов
    GET  /api/plans/{plan_id}       открыть сохранённый план
    POST /api/export/csv            выгрузка CSV тем же расчётом
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from pathlib import Path as _Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ENGINE_VERSION, db, export, paths
from .caseinput import load_case
from .checks import validate_plan
from .engine import run as run_engine
from .plan import Plan
from .planner import auto_plan
from .scenarios import SCEN_DIR, load_all

app = FastAPI(title="Криоконтур", version=ENGINE_VERSION)
CASE = load_case()
SCENARIOS = load_all()


class PlanBody(BaseModel):
    plan: dict
    scenario_id: str = "BASE"


class AutoPlanBody(BaseModel):
    investments: Dict[str, Optional[int]]
    scenario_id: str = "MANDATORY_STRESS"
    plan_id: str = "auto"


class CompareBody(BaseModel):
    plan: dict
    scenario_ids: List[str] = ["BASE", "MANDATORY_STRESS"]


def _scenario(scenario_id: str):
    if scenario_id not in SCENARIOS:
        raise HTTPException(404, f"сценарий {scenario_id} не найден")
    return SCENARIOS[scenario_id]


def _result_json(res) -> dict:
    return {
        "plan_id": res.plan_id, "scenario_id": res.scenario_id, "case_version": res.case_version,
        "engine_version": res.engine_version, "created_at": res.created_at, "assumptions": res.assumptions,
        "feasible": res.feasible, "totals": res.totals,
        "years": [{**asdict(y), "sl_total": y.sl_total, "sl_critical": y.sl_critical,
                   "loss_share": y.loss_share, "emergency_share": y.emergency_share} for y in res.years],
        "months": [asdict(m) for m in res.months],
        "violations": [v.as_dict() for v in res.violations],
        "log": res.log,
    }


@app.get("/api/case")
def get_case():
    return {
        "version": CASE.version, "years": CASE.years, "units": CASE.currency_note,
        "demand": {"total": CASE.demand_total, "critical": CASE.demand_critical,
                   "low": CASE.demand_low, "high": CASE.demand_high},
        "sources": [asdict(s) for s in CASE.source_list],
        "storage": [asdict(s) for s in CASE.storage.values()],
        "investments": [asdict(i) for i in CASE.investments.values()],
        "constraints": [asdict(c) for c in CASE.constraints.values()],
    }


@app.get("/api/scenarios")
def get_scenarios():
    return [{"scenario_id": s.scenario_id, "label": s.label, "status": s.status, "notes": s.notes}
            for s in SCENARIOS.values()]


@app.get("/api/selftest")
def selftest():
    import subprocess, sys
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "output": proc.stdout[-4000:]}


@app.post("/api/autoplan")
def post_autoplan(body: AutoPlanBody):
    plan = auto_plan(CASE, _scenario(body.scenario_id), body.investments, body.plan_id)
    return plan.to_envelope(body.scenario_id)


@app.post("/api/run")
def post_run(body: PlanBody):
    plan = Plan.from_envelope(body.plan)
    errors = [v.as_dict() for v in validate_plan(CASE, plan) if v.code == "INPUT_INVALID"]
    if errors:
        raise HTTPException(422, {"message": "план не принят: проверьте значения", "violations": errors})
    return _result_json(run_engine(CASE, _scenario(body.scenario_id), plan))


@app.post("/api/compare")
def post_compare(body: CompareBody):
    plan = Plan.from_envelope(body.plan)
    return {sid: _result_json(run_engine(CASE, _scenario(sid), plan)) for sid in body.scenario_ids}


@app.post("/api/plans")
def post_plan(body: PlanBody):
    plan = Plan.from_envelope(body.plan)
    conn = db.connect()
    db.sync_case(conn, CASE)
    db.sync_scenarios(conn, SCENARIOS, SCEN_DIR)
    return {"plan_id": db.save_plan(conn, plan)}


@app.get("/api/plans")
def get_plans():
    return db.list_plans(db.connect())


@app.get("/api/plans/presets")
def get_plan_presets():
    """Готовые планы команды из configs/plans: то, что фронт показывает в выпадающем списке."""
    import json as _json
    out = {}
    folder = paths.PLANS
    order = {"final-candidate": 0}
    for path in sorted(folder.glob("*.json"), key=lambda p: (order.get(p.stem, 1), p.stem)):
        raw = _json.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("name", path.stem)
        out[path.stem] = raw
    return out


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str):
    plan = db.load_plan(db.connect(), plan_id)
    if plan is None:
        raise HTTPException(404, "план не найден")
    return plan.to_envelope()


@app.post("/api/export/csv", response_class=PlainTextResponse)
def post_export(body: PlanBody):
    plan = Plan.from_envelope(body.plan)
    scenario = _scenario(body.scenario_id)
    res = run_engine(CASE, scenario, plan)
    return export.to_csv(CASE, res, scenario)


# --------------------------------------------------------------------------- #
# чувствительность, риски, геополитика, Монте-Карло
# --------------------------------------------------------------------------- #
class SweepBody(BaseModel):
    plan: dict
    scenario_id: str = "BASE"
    key: str = "demand"
    steps: int = 9
    values: Optional[List[float]] = None


class TornadoBody(BaseModel):
    plan: dict
    scenario_id: str = "BASE"
    keys: Optional[List[str]] = None
    delta: float = 0.10


class RiskBody(BaseModel):
    plan: dict
    baseline: str = "BASE"


class GeoBody(BaseModel):
    plan: dict
    base_scenario: str = "BASE"
    label: str = "Условное геополитическое событие"
    sources: List[str] = ["A", "B"]
    start_year: int = 2036
    end_year: int = 2039
    multiplier: float = 1.4
    component: str = "variable_price"


class MonteCarloBody(BaseModel):
    plan: dict
    scenario_id: str = "BASE"
    trials: int = 200
    seed: int = 20260918
    partial_low: float = 0.5
    partial_high: float = 0.9


@app.get("/api/params")
def get_params():
    from .sensitivity import PARAMS
    return [{"key": p.key, "label": p.label, "unit": p.unit, "base": p.base, "low": p.low,
             "high": p.high, "target": p.target, "basis": p.basis} for p in PARAMS.values()]


@app.post("/api/sensitivity/sweep")
def post_sweep(body: SweepBody):
    from .sensitivity import sweep
    plan = Plan.from_envelope(body.plan)
    return sweep(CASE, _scenario(body.scenario_id), plan, body.key, body.values, body.steps)


@app.post("/api/sensitivity/threshold")
def post_threshold(body: SweepBody):
    from .sensitivity import threshold
    plan = Plan.from_envelope(body.plan)
    return threshold(CASE, _scenario(body.scenario_id), plan, body.key)


@app.post("/api/sensitivity/tornado")
def post_tornado(body: TornadoBody):
    from .sensitivity import tornado
    plan = Plan.from_envelope(body.plan)
    return tornado(CASE, _scenario(body.scenario_id), plan, body.keys, body.delta)


@app.post("/api/sensitivity/reverse")
def post_reverse(body: TornadoBody):
    from .sensitivity import reverse_stress
    plan = Plan.from_envelope(body.plan)
    return reverse_stress(CASE, _scenario(body.scenario_id), plan, body.keys)


@app.post("/api/risks")
def post_risks(body: RiskBody):
    from .risks import evaluate_risks, load_risks
    plan = Plan.from_envelope(body.plan)
    return evaluate_risks(CASE, SCENARIOS, plan, load_risks(), body.baseline)


@app.post("/api/geo")
def post_geo(body: GeoBody):
    """Геополитический модуль: пользователь задаёт событие, модель возвращает прогон и правило сочетания."""
    from .risks import geo_event
    plan = Plan.from_envelope(body.plan)
    sc = geo_event(CASE, _scenario(body.base_scenario), body.label, body.sources,
                   body.start_year, body.end_year, body.multiplier, body.component)
    res = run_engine(CASE, sc, plan)
    payload = _result_json(res)
    payload["combination_rule"] = sc.combination_rule
    payload["restore"] = f"вернуться к контрольным ценам: сценарий {body.base_scenario}"
    return payload


@app.post("/api/montecarlo")
def post_monte_carlo(body: MonteCarloBody):
    from dataclasses import asdict as _asdict
    from .risks import monte_carlo
    plan = Plan.from_envelope(body.plan)
    mc = monte_carlo(CASE, _scenario(body.scenario_id), plan, body.trials, body.seed,
                     body.partial_low, body.partial_high)
    return _asdict(mc)


# --------------------------------------------------------------------------- #
# расчёт на перспективу
# --------------------------------------------------------------------------- #
class HorizonBody(BaseModel):
    method: str = "increment"
    rate: float = 0.08


@app.post("/api/horizon")
def post_horizon(body: HorizonBody):
    """Сравнение архитектур за горизонтом 2040 года. Все величины TEAM_RESEARCH."""
    from .horizon import (HorizonConfig, cost_per_served, cumulative_discounted, earth_ceiling,
                          extend_case, first_deficit_year, first_infeasible_year, payback_year)
    from .planner import auto_plan
    cfg = HorizonConfig.load()
    base = SCENARIOS["BASE"]
    arch = {
        "earth": ("Только Земля", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035, "ZBO2": 2041}, []),
        "earth2": ("Земля плюс второй поставщик", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                                   "ZBO2": 2041, "EARTH_NEW_2": 2041}, ["EARTH_NEW_2"]),
        "isru1": ("Луна, первая очередь", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                           "ZBO2": 2041, "LUNAR_ISRU": 2036}, []),
        "isru2": ("Луна, две очереди", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                        "ZBO2": 2041, "LUNAR_ISRU": 2036, "LUNAR_ISRU_PHASE2": 2041}, ["LUNAR_ISRU_PHASE2"]),
    }
    rows, cums = [], {}
    for key, (label, invest, enable) in arch.items():
        ext = extend_case(CASE, cfg, body.method, ["ZBO2"] + enable)
        plan = auto_plan(ext, base, invest, key, label)
        plan.assumptions["discount_rate"] = body.rate
        res = run_engine(ext, base, plan)
        cums[key] = cumulative_discounted(res, ext.years[0], body.rate)
        rows.append({
            "key": key, "label": label,
            "pv_2040": cums[key][2040], "pv_last": cums[key][cfg.last_year],
            "served_t": res.totals["served_t"], "shortage_t": res.totals["shortage_t"],
            "cost_per_t": cost_per_served(res, ext.years[0], body.rate)[cfg.last_year],
            "first_deficit_year": first_deficit_year(res),
            "years": [{"year": y.year, "demand": y.demand_total_t, "served": y.served_t,
                       "shortage": y.shortage_t} for y in res.years],
        })
    demo = extend_case(CASE, cfg, body.method, ["ZBO2"])
    return {
        "method": body.method, "basis": cfg.methods[body.method]["basis"], "last_year": cfg.last_year,
        "demand": {str(y): demo.demand_total[y] for y in demo.years},
        "earth_ceiling": earth_ceiling(demo),
        "first_infeasible_year": first_infeasible_year(demo, earth_ceiling(demo)),
        "architectures": rows,
        "payback": {
            "isru1_vs_earth2": payback_year(cums["isru1"], cums["earth2"]),
            "isru2_vs_earth2": payback_year(cums["isru2"], cums["earth2"]),
            "isru2_vs_isru1": payback_year(cums["isru2"], cums["isru1"]),
        },
        "notes": cfg.notes,
    }


# --------------------------------------------------------------------------- #
# фронтенд: статические файлы из web/
# --------------------------------------------------------------------------- #
_WEB = paths.WEB
if _WEB.exists():
    @app.get("/")
    def index():
        return FileResponse(_WEB / "index.html")

    app.mount("/web", StaticFiles(directory=_WEB), name="web")
