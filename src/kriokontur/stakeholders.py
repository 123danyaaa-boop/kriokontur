"""Интересы заинтересованных сторон в метриках расчёта.

Кейс (с. 12, критерии 17 и 18) требует не список ролей, а связь интереса с числом:
кто получает топливо, кто несёт затраты и риск, и как меняется положение каждой стороны
в стрессе и при реализации риска.

Поэтому каждая метрика стороны это функция от готового `RunResult`. Новых формул здесь нет:
уровень обслуживания, платежи и CAPEX уже посчитаны движком, модуль только смотрит на них
с точки зрения конкретной стороны и считает разницу между сценариями.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml

from .caseinput import CaseInput
from .engine import RunResult, run
from .paths import CONFIGS
from .plan import Plan
from .scenarios import Scenario

STAKEHOLDERS_PATH = CONFIGS / "stakeholders.yaml"
EARTH_SOURCES = ("A", "B", "C")


@dataclass
class Stakeholder:
    stakeholder_id: str
    name: str
    role: str = ""
    gets: str = ""
    pays: str = ""
    bears_risk: str = ""
    requires: str = ""
    metrics: List[str] = field(default_factory=list)
    obligations: List[str] = field(default_factory=list)


def load_stakeholders(path: Path | str = STAKEHOLDERS_PATH) -> List[Stakeholder]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [Stakeholder(**row) for row in raw]


def _sum_sources(mapping_getter: Callable[[object], Dict[str, float]], res: RunResult,
                 sources) -> float:
    return sum(sum(mapping_getter(y).get(s, 0.0) for s in sources) for y in res.years)


# Метрика: имя -> (подпись, единица, направление «лучше», функция от прогона).
# direction: +1 — больше лучше, -1 — меньше лучше, 0 — нейтрально.
METRICS: Dict[str, tuple] = {
    "pv_mln": ("Приведённые расходы", "млн у.е.", -1,
               lambda case, res: res.totals["discounted_cost_mln"]),
    "total_mln": ("Расходы номиналом", "млн у.е.", -1,
                  lambda case, res: res.totals["total_cost_mln"]),
    "hard_violations": ("Жёстких нарушений", "шт.", -1,
                        lambda case, res: float(sum(1 for v in res.violations if v.severity == "hard"))),
    "min_reserve_margin_t": ("Запас над резервом, худший год", "т", 1,
                             lambda case, res: min(y.opening_t - y.reserve_required_t for y in res.years)),
    "overflow_t": ("Перелив хранилища", "т", -1, lambda case, res: res.totals.get("overflow_t", 0.0)),
    "sl_critical_worst": ("Обслуживание критического спроса, худший год", "доля", 1,
                          lambda case, res: min(y.sl_critical for y in res.years)),
    "critical_shortage_t": ("Недопоставка критического спроса", "т", -1,
                            lambda case, res: sum(y.shortage_critical_t for y in res.years)),
    "reserve_days_worst": ("Запас на начало года в сутках спроса, худший год", "сут.", 1,
                           lambda case, res: min(y.opening_t / y.demand_total_t * 365
                                                 if y.demand_total_t else 0.0 for y in res.years)),
    "sl_total_worst": ("Общее обслуживание, худший год", "доля", 1,
                       lambda case, res: min(y.sl_total for y in res.years)),
    "shortage_t": ("Дефицит за горизонт", "т", -1, lambda case, res: res.totals["shortage_t"]),
    "cost_per_served_t": ("Расход на тонну обслуженного спроса", "млн у.е./т", -1,
                          lambda case, res: res.totals["cost_per_served_t"]),
    "earth_payments_mln": ("Платежи земным каналам", "млн у.е.", 1,
                           lambda case, res: _sum_sources(lambda y: y.variable_payment_mln, res, EARTH_SOURCES)
                           + _sum_sources(lambda y: y.reservation_payment_mln, res, EARTH_SOURCES)),
    "earth_delivered_t": ("Поставлено земными каналами", "т", 1,
                          lambda case, res: _sum_sources(lambda y: y.delivered_t, res, EARTH_SOURCES)),
    "earth_utilisation": ("Загрузка законтрактованной земной мощности", "доля", 1,
                          lambda case, res: (_sum_sources(lambda y: y.ordered_t, res, EARTH_SOURCES)
                                             / _sum_sources(lambda y: y.contracted_t, res, EARTH_SOURCES))
                          if _sum_sources(lambda y: y.contracted_t, res, EARTH_SOURCES) > 1e-9 else 0.0),
    "unused_paid_t": ("Оплачено по take-or-pay, но не отобрано", "т", -1,
                      lambda case, res: res.totals.get("unused_paid_t", 0.0)),
    "emergency_reservation_mln": ("Плата за доступность аварийного канала", "млн у.е.", 1,
                                  lambda case, res: sum(y.reservation_payment_mln.get("E", 0.0)
                                                        for y in res.years)),
    "emergency_delivered_t": ("Поставлено аварийным каналом", "т", 1,
                              lambda case, res: sum(y.delivered_t.get("E", 0.0) for y in res.years)),
    "emergency_max_share": ("Доля аварийного канала, худший год", "доля", -1,
                            lambda case, res: max(y.emergency_share for y in res.years)),
    "capex_total_mln": ("CAPEX за горизонт", "млн у.е.", 0, lambda case, res: res.totals["capex"]),
    "capex_through_2037_mln": ("CAPEX до конца 2037", "млн у.е.", 0,
                               lambda case, res: sum(y.cost["capex"] for y in res.years if y.year <= 2037)),
    "capex_headroom_2037_mln": ("Свободный лимит CAPEX до 2037", "млн у.е.", 1,
                                lambda case, res: case.constraint("CAPEX_2037").value
                                - sum(y.cost["capex"] for y in res.years if y.year <= 2037)),
}


def metric_values(case: CaseInput, res: RunResult, keys: List[str]) -> Dict[str, Dict]:
    out = {}
    for key in keys:
        if key not in METRICS:
            continue
        label, unit, direction, fn = METRICS[key]
        out[key] = {"label": label, "unit": unit, "direction": direction, "value": fn(case, res)}
    return out


def impact(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
           stakeholders: Optional[List[Stakeholder]] = None,
           scenario_ids: Optional[List[str]] = None, baseline: str = "BASE",
           risk_scenarios: Optional[List[str]] = None) -> Dict:
    """Положение каждой стороны в наборе сценариев и при реализации рисков.

    Для каждой стороны возвращается её интерес, договорные обязательства и метрики
    по сценариям с разницей к базовому. Ухудшение отмечается по направлению метрики,
    поэтому «больше платежей поставщику» не выдаётся за ухудшение для поставщика.
    """
    stakeholders = stakeholders if stakeholders is not None else load_stakeholders()
    ids = [sid for sid in (scenario_ids or [baseline, "MANDATORY_STRESS"]) if sid in scenarios]
    risk_ids = [sid for sid in (risk_scenarios or []) if sid in scenarios]
    runs = {sid: run(case, scenarios[sid], plan) for sid in dict.fromkeys(ids + risk_ids)}

    rows = []
    for st in stakeholders:
        per_scenario = {}
        for sid, res in runs.items():
            per_scenario[sid] = metric_values(case, res, st.metrics)
        base = per_scenario.get(baseline, {})
        for sid, values in per_scenario.items():
            for key, item in values.items():
                ref = base.get(key, {}).get("value")
                item["delta_vs_baseline"] = None if ref is None else item["value"] - ref
                item["worse"] = (None if ref is None or item["direction"] == 0
                                 else (item["value"] - ref) * item["direction"] < -1e-9)
        rows.append({
            "stakeholder_id": st.stakeholder_id, "name": st.name, "role": st.role,
            "gets": st.gets, "pays": st.pays, "bears_risk": st.bears_risk, "requires": st.requires,
            "obligations": st.obligations,
            "scenarios": per_scenario,
        })
    return {"baseline": baseline, "scenario_ids": list(runs), "risk_scenario_ids": risk_ids,
            "stakeholders": rows}


def export_rows(result: Dict) -> List[List]:
    """Плоская таблица для выгрузки: сторона × сценарий × метрика."""
    rows: List[List] = [["stakeholder_id", "name", "role", "gets", "pays", "bears_risk", "requires",
                         "obligations", "scenario_id", "metric", "label", "unit", "value",
                         "delta_vs_baseline", "worse_than_baseline"]]
    for st in result["stakeholders"]:
        for sid, metrics in st["scenarios"].items():
            for key, item in metrics.items():
                rows.append([st["stakeholder_id"], st["name"], st["role"], st["gets"], st["pays"],
                             st["bears_risk"], st["requires"], ",".join(st["obligations"]), sid, key,
                             item["label"], item["unit"], item["value"],
                             "" if item["delta_vs_baseline"] is None else item["delta_vs_baseline"],
                             "" if item["worse"] is None else int(item["worse"])])
    return rows
