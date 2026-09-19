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
    POST /api/export/xlsx           выгрузка XLSX тем же расчётом
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional


from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import ENGINE_VERSION, db, export, paths
from .caseinput import load_case
from .checks import validate_envelope, validate_plan
from .engine import run as run_engine
from .plan import Plan
from .planner import auto_plan
from .scenarios import SCEN_DIR, load_all

app = FastAPI(title="Криоконтур", version=ENGINE_VERSION)

# Интерфейс можно открыть и файлом (kriokontur-demo.html): тогда origin у страницы «null»,
# и без этих заголовков браузер не даст ей достучаться до локального API. Список намеренно
# узкий: только локальные адреса и файл, чтобы к узлу не ходил произвольный сайт.
_LOCAL_ORIGINS = ["null"] + [f"http://{host}:{port}" for host in ("127.0.0.1", "localhost")
                             for port in range(8000, 8011)]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_LOCAL_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

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


def _plan(raw: dict) -> Plan:
    """Единая точка приёма плана: сначала проверка конверта, потом разбор.

    Пользователь получает 422 с полем, годом и причиной, а не 500 на приведении типов
    и не молча посчитанный «пустой» план.
    """
    errors = [v.as_dict() for v in validate_envelope(CASE, raw)]
    if errors:
        raise HTTPException(422, {"message": "план не принят: проверьте значения", "violations": errors})
    plan = Plan.from_envelope(raw)
    errors = [v.as_dict() for v in validate_plan(CASE, plan) if v.code == "INPUT_INVALID"]
    if errors:
        raise HTTPException(422, {"message": "план не принят: проверьте значения", "violations": errors})
    return plan


def _result_json(res) -> dict:
    from .reporting import kpi_rows
    return {
        "plan_id": res.plan_id, "scenario_id": res.scenario_id, "case_version": res.case_version,
        "engine_version": res.engine_version, "created_at": res.created_at, "assumptions": res.assumptions,
        "feasible": res.feasible, "totals": res.totals, "kpi": kpi_rows(CASE, res),
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
    plan = _plan(body.plan)
    return _result_json(run_engine(CASE, _scenario(body.scenario_id), plan))


@app.post("/api/compare")
def post_compare(body: CompareBody):
    plan = _plan(body.plan)
    for sid in body.scenario_ids:
        _scenario(sid)          # неизвестный сценарий: 404 до расчёта, а не частичный ответ
    return {sid: _result_json(run_engine(CASE, _scenario(sid), plan)) for sid in body.scenario_ids}


class SavePlanBody(BaseModel):
    plan: dict
    scenario_id: str = "BASE"
    name: Optional[str] = None
    plan_id: Optional[str] = None


@app.post("/api/plans")
def post_plan(body: SavePlanBody):
    """Сохранение с именем, идентификатором и сценарием.

    Пресеты команды из configs/plans не затираются: если пользователь сохраняет план под
    именем пресета, идентификатор получает суффикс, и файл-пресет остаётся нетронутым.
    """
    plan = _plan(body.plan)
    _scenario(body.scenario_id)
    if body.name:
        plan.name = body.name
    if body.plan_id:
        plan.plan_id = _safe_name(body.plan_id)
    presets = {p.stem for p in paths.PLANS.glob("*.json")}
    if plan.plan_id in presets:
        from datetime import datetime, timezone
        plan.plan_id = f"{plan.plan_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    conn = db.connect()
    db.sync_case(conn, CASE)
    db.sync_scenarios(conn, SCENARIOS, SCEN_DIR)
    return {"plan_id": db.save_plan(conn, plan, body.scenario_id), "name": plan.name,
            "scenario_id": body.scenario_id}


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
    envelope = db.load_envelope(db.connect(), plan_id)
    if envelope is None:
        raise HTTPException(404, f"план {plan_id} не найден")
    return envelope       # как сохранён: с именем и сценарием, под которым строился


def _export_extras(plan: Plan, res) -> dict:
    """Полный пакет выгрузки: исходные данные, KPI, сравнение сценариев, разложение стресса,
    реестр рисков. Всё считается тем же движком, что и экран."""
    from .reporting import export_sections
    from .risks import load_risks
    try:
        risks = load_risks()
    except Exception:            # реестр рисков не должен ломать выгрузку баланса
        risks = []
    return export_sections(CASE, SCENARIOS, plan, res, risks=risks)


@app.post("/api/export/csv", response_class=PlainTextResponse)
def post_export(body: PlanBody):
    plan = _plan(body.plan)
    scenario = _scenario(body.scenario_id)
    res = run_engine(CASE, scenario, plan)
    return export.to_csv(CASE, res, scenario, _export_extras(plan, res))


def _safe_name(text: str) -> str:
    """Имя файла из plan_id: идентификатор приходит от пользователя, в путь его пускать нельзя."""
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in str(text)]
    return ("".join(keep).strip("-") or "plan")[:60]


@app.post("/api/export/xlsx")
def post_export_xlsx(body: PlanBody):
    """Тот же расчёт, что и в CSV, но книгой XLSX. Файл кладётся в results/ и отдаётся браузеру."""
    plan = _plan(body.plan)
    scenario = _scenario(body.scenario_id)
    res = run_engine(CASE, scenario, plan)
    paths.ensure_dirs()
    name = f"{_safe_name(plan.plan_id)}_{_safe_name(scenario.scenario_id)}.xlsx"
    path = export.write_xlsx(CASE, res, scenario, paths.RESULTS / name, _export_extras(plan, res))
    return FileResponse(
        path, filename=name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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
    plan = _plan(body.plan)
    return sweep(CASE, _scenario(body.scenario_id), plan, body.key, body.values, body.steps)


@app.post("/api/sensitivity/threshold")
def post_threshold(body: SweepBody):
    from .sensitivity import threshold
    plan = _plan(body.plan)
    return threshold(CASE, _scenario(body.scenario_id), plan, body.key)


@app.post("/api/sensitivity/tornado")
def post_tornado(body: TornadoBody):
    from .sensitivity import tornado
    plan = _plan(body.plan)
    return tornado(CASE, _scenario(body.scenario_id), plan, body.keys, body.delta)


@app.post("/api/sensitivity/reverse")
def post_reverse(body: TornadoBody):
    from .sensitivity import reverse_stress
    plan = _plan(body.plan)
    return reverse_stress(CASE, _scenario(body.scenario_id), plan, body.keys)


class ComparisonBody(BaseModel):
    plan: dict
    scenario_ids: Optional[List[str]] = None
    baseline: str = "BASE"


@app.post("/api/report/scenarios")
def post_scenario_comparison(body: ComparisonBody):
    """Один план в нескольких сценариях: расходы, сервис худшего года, запас, дефицит."""
    from .reporting import scenario_comparison
    plan = _plan(body.plan)
    return scenario_comparison(CASE, SCENARIOS, plan, body.scenario_ids, body.baseline)


@app.post("/api/report/strategies")
def post_strategy_comparison(body: ComparisonBody):
    """Готовые планы команды и текущий план в одних и тех же сценариях."""
    import json as _json

    from .reporting import strategy_comparison
    current = _plan(body.plan)
    plans = {}
    for path in sorted(paths.PLANS.glob("*.json")):
        raw = _json.loads(path.read_text(encoding="utf-8"))
        plans[path.stem] = Plan.from_envelope(raw)
    plans["текущий"] = current
    return strategy_comparison(CASE, SCENARIOS, plans, body.scenario_ids)


@app.post("/api/report/stress-decomposition")
def post_stress_decomposition(body: PlanBody):
    """Разложение эффекта обязательного стресса на спрос, цены, поставку Луны и потолок потерь."""
    from .reporting import stress_decomposition
    plan = _plan(body.plan)
    return stress_decomposition(CASE, SCENARIOS, plan)


@app.get("/api/contracts")
def get_contracts():
    """Карточки договоров: условия кейса из data/*.csv плюс коммерческая рамка команды."""
    from .contracts import contract_card, load_contracts
    return [contract_card(CASE, c) for c in load_contracts()]


@app.post("/api/contracts/obligations")
def post_contract_obligations(body: PlanBody):
    """Договорные обязательства по годам и итоги: сходятся с финансовым блоком прогона."""
    from .contracts import emergency_reserve_check, load_contracts, obligations, obligations_totals
    plan = _plan(body.plan)
    scenario = _scenario(body.scenario_id)
    res = run_engine(CASE, scenario, plan)
    contracts = load_contracts()
    rows = obligations(CASE, res, contracts)
    totals = obligations_totals(rows)
    return {
        "scenario_id": scenario.scenario_id,
        "rows": rows,
        "totals": list(totals.values()),
        "emergency_reserve": [emergency_reserve_check(CASE, plan, y.year, y.demand_total_t, y.opening_t)
                              for y in res.years],
        "reconciliation": {
            "contracts_total_mln": sum(t["total_payment_mln"] for t in totals.values()),
            "run_procurement_and_reservation_mln": (res.totals["procurement"] + res.totals["reservation"]
                                                    - sum(y.opening_stock_cost_mln for y in res.years)),
        },
    }


class StakeholderBody(BaseModel):
    plan: dict
    scenario_ids: Optional[List[str]] = None
    risk_scenarios: Optional[List[str]] = None
    baseline: str = "BASE"


@app.get("/api/stakeholders")
def get_stakeholders():
    from dataclasses import asdict as _asdict

    from .stakeholders import load_stakeholders
    return [_asdict(s) for s in load_stakeholders()]


@app.post("/api/stakeholders/impact")
def post_stakeholder_impact(body: StakeholderBody):
    """Положение каждой стороны по сценариям и рискам: метрики, разница и ухудшение."""
    from .stakeholders import impact
    plan = _plan(body.plan)
    risks = body.risk_scenarios
    if risks is None:
        risks = [sid for sid in SCENARIOS if sid.startswith("TEAM_RISK_")]
    return impact(CASE, SCENARIOS, plan, None, body.scenario_ids, body.baseline, risks)


class CustomScenarioBody(BaseModel):
    """Редактор исследовательского сценария: изменения исходных условий на копии набора."""
    plan: dict
    base_scenario: str = "BASE"
    label: str = "Исследовательский сценарий команды"
    demand_multiplier: float = 1.0
    price_multiplier: float = 1.0
    price_sources: List[str] = ["A", "B"]
    delivery_share: float = 1.0
    delivery_sources: List[str] = []
    capacity_multiplier: float = 1.0
    capacity_sources: List[str] = []
    start_year: int = 2035
    end_year: int = 2040
    basis: str = "TEAM_RESEARCH: сценарное допущение команды, статистики нет"


@app.post("/api/scenario/custom")
def post_custom_scenario(body: CustomScenarioBody):
    """Изменение исходных условий через интерфейс, без правки контрольных данных.

    Работает на копии: строится производный сценарий со статусом TEAM_RESEARCH, контрольные
    файлы data/*.csv и configs/scenarios/*.yaml не меняются. Возврат к исходным условиям —
    это просто выбор базового сценария, он назван в ответе.
    """
    plan = _plan(body.plan)
    base = _scenario(body.base_scenario)
    if body.start_year > body.end_year:
        raise HTTPException(422, {"message": "интервал задан неверно: начало позже конца",
                                  "violations": []})
    years = [y for y in CASE.years if body.start_year <= y <= body.end_year]
    if not years:
        raise HTTPException(422, {"message": f"в горизонте {CASE.years[0]}–{CASE.years[-1]} "
                                             f"нет лет из интервала", "violations": []})
    changes = []
    sc = base
    if abs(body.demand_multiplier - 1.0) > 1e-9:
        table = dict(sc.demand_multiplier)
        crit = dict(sc.critical_multiplier)
        for y in years:
            table[y] = table.get(y, table.get("default", 1.0)) * body.demand_multiplier
            crit[y] = crit.get(y, crit.get("default", 1.0)) * body.demand_multiplier
        sc = sc.derive("TEAM_CUSTOM", body.label, demand_multiplier=table, critical_multiplier=crit)
        changes.append(f"спрос ×{body.demand_multiplier} в {body.start_year}–{body.end_year}")
    for component, mult, sources in (("variable_price", body.price_multiplier, body.price_sources),
                                     ("delivery", body.delivery_share, body.delivery_sources),
                                     ("capacity", body.capacity_multiplier, body.capacity_sources)):
        if abs(mult - 1.0) < 1e-9 or not sources:
            continue
        unknown = [s for s in sources if s not in CASE.sources]
        if unknown:
            raise HTTPException(422, {"message": f"неизвестные каналы: {unknown}", "violations": []})
        if component == "variable_price":
            from .risks import geo_event
            sc = geo_event(CASE, sc, body.label, sources, body.start_year, body.end_year, mult,
                           "variable_price", "TEAM_CUSTOM", body.basis)
        elif component == "capacity":
            from .risks import geo_event
            sc = geo_event(CASE, sc, body.label, sources, body.start_year, body.end_year, mult,
                           "capacity", "TEAM_CUSTOM", body.basis)
        else:
            table = {k: dict(v) for k, v in sc.delivery_share.items()}
            for s in sources:
                name = CASE.sources[s].name
                cur = table.get(name, {})
                for y in years:
                    cur[y] = cur.get(y, cur.get("default", 1.0)) * mult
                table[name] = cur
            sc = sc.derive("TEAM_CUSTOM", body.label, delivery_share=table)
        changes.append(f"{component} каналов {', '.join(sources)} ×{mult} в {body.start_year}–{body.end_year}")
    if not changes:
        raise HTTPException(422, {"message": "не задано ни одного изменения исходных условий",
                                  "violations": []})
    res = run_engine(CASE, sc, plan)
    base_res = run_engine(CASE, base, plan)
    payload = _result_json(res)
    payload["scenario_id"] = "TEAM_CUSTOM"
    payload["status"] = "TEAM_RESEARCH"
    payload["changes"] = changes
    payload["basis"] = body.basis
    payload["combination_rule"] = (
        f"Изменения применяются поверх сценария {body.base_scenario} только к годам "
        f"{body.start_year}–{body.end_year}; годы, где базовый сценарий уже менял тот же параметр, "
        f"не затрагиваются, поэтому один эффект не начисляется дважды.")
    payload["restore"] = f"вернуться к исходным условиям: выбрать сценарий {body.base_scenario}"
    payload["baseline"] = {
        "scenario_id": base.scenario_id,
        "pv_mln": base_res.totals["discounted_cost_mln"],
        "delta_pv_mln": res.totals["discounted_cost_mln"] - base_res.totals["discounted_cost_mln"],
        "delta_shortage_t": res.totals["shortage_t"] - base_res.totals["shortage_t"],
    }
    return payload


@app.post("/api/risks")
def post_risks(body: RiskBody):
    from .risks import evaluate_risks, load_risks
    plan = _plan(body.plan)
    return evaluate_risks(CASE, SCENARIOS, plan, load_risks(), body.baseline)


@app.post("/api/geo")
def post_geo(body: GeoBody):
    """Геополитический модуль: пользователь задаёт событие, модель возвращает прогон и правило сочетания."""
    from .risks import geo_event
    plan = _plan(body.plan)
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
    plan = _plan(body.plan)
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
