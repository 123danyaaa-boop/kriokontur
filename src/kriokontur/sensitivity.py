"""Анализ чувствительности, пороги и обратный стресс.

Три разных вопроса, которые кейс требует различать:
    sweep      как метрика меняется при изменении параметра в обоснованном диапазоне;
    threshold  при каком значении параметра план перестаёт выполнять ограничения;
    tornado    какие параметры двигают приведённые расходы сильнее всего.

Обратный стресс это тот же поиск порога, только сформулированный от нарушения:
ищем ближайшее значение параметра, при котором появляется жёсткое нарушение.

Параметр меняет либо сценарий (спрос, цены, доли поставки, мощность), либо наш план
(ставка дисконтирования, срок подготовки нового поставщика, начальный запас). Ни один
параметр не правит CASE_INPUT напрямую: изменения живут в производном сценарии со статусом
TEAM_RESEARCH.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .caseinput import CaseInput
from .engine import RunResult, run
from .plan import Plan
from .scenarios import Scenario

Applied = Tuple[Scenario, Plan]


@dataclass
class Param:
    key: str
    label: str
    unit: str
    base: float
    low: float
    high: float
    target: str            # scenario | plan
    apply: Callable[[CaseInput, Scenario, Plan, float], Applied]
    basis: str = ""


def _scale_demand(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
    table = dict(sc.demand_multiplier)
    crit = dict(sc.critical_multiplier)
    for y in case.years:
        table[y] = table.get(y, table.get("default", 1.0)) * v
        crit[y] = crit.get(y, crit.get("default", 1.0)) * v
    return sc.derive(f"{sc.scenario_id}+demand{v:.2f}", f"спрос ×{v:.2f}",
                     demand_multiplier=table, critical_multiplier=crit), plan


def _scale_price(sources: Tuple[str, ...]):
    def apply(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
        table = {k: dict(x) for k, x in sc.price_multiplier.items()}
        for sid in sources:
            name = case.sources[sid].name
            cur = table.get(name, table.get(sid, {}))
            table[name] = {y: cur.get(y, cur.get("default", 1.0)) * v for y in case.years}
        return sc.derive(f"{sc.scenario_id}+price{v:.2f}", f"цена {'/'.join(sources)} ×{v:.2f}",
                         price_multiplier=table), plan
    return apply


def _scale_delivery(sid: str):
    def apply(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
        table = {k: dict(x) for k, x in sc.delivery_share.items()}
        name = case.sources[sid].name
        cur = table.get(name, table.get(sid, {}))
        table[name] = {y: min(1.0, cur.get(y, cur.get("default", 1.0)) * v) for y in case.years}
        return sc.derive(f"{sc.scenario_id}+delivery{sid}{v:.2f}", f"поставка {sid} ×{v:.2f}",
                         delivery_share=table), plan
    return apply


def _scale_capacity(sid: str):
    def apply(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
        table = {k: dict(x) for k, x in sc.capacity_multiplier.items()}
        name = case.sources[sid].name
        table[name] = {y: v for y in case.years}
        return sc.derive(f"{sc.scenario_id}+capacity{sid}{v:.2f}", f"мощность {sid} ×{v:.2f}",
                         capacity_multiplier=table), plan
    return apply


def _plan_assumption(name: str):
    def apply(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
        p = copy.deepcopy(plan)
        p.assumptions[name] = v
        return sc, p
    return apply


def _opening_stock(case: CaseInput, sc: Scenario, plan: Plan, v: float) -> Applied:
    p = copy.deepcopy(plan)
    p.inventory_policy = dict(p.inventory_policy)
    p.inventory_policy["opening_stock_t"] = v
    return sc, p


PARAMS: Dict[str, Param] = {
    "demand": Param("demand", "Спрос, множитель ко всем годам", "доля", 1.0, 0.8, 1.4, "scenario", _scale_demand,
                    "диапазон покрывает низкий и высокий варианты кейса и запас сверх них"),
    "price_earth": Param("price_earth", "Цена Earth-Core и Earth-Flex", "доля", 1.0, 0.8, 1.8, "scenario",
                         _scale_price(("A", "B")), "обязательный стресс даёт +25%, проверяем шире"),
    "price_core": Param("price_core", "Цена Earth-Core", "доля", 1.0, 0.8, 2.0, "scenario", _scale_price(("A",)),
                        "чувствительность к главному каналу"),
    "isru_delivery": Param("isru_delivery", "Фактическая поставка Луны", "доля", 1.0, 0.3, 1.0, "scenario",
                           _scale_delivery("D"), "стресс задаёт 55% и 75%, надёжность кейса 0,78–0,93"),
    "core_capacity": Param("core_capacity", "Доступная мощность Earth-Core", "доля", 1.0, 0.4, 1.0, "scenario",
                           _scale_capacity("A"), "сбой у поставщика без снятия take-or-pay"),
    "discount_rate": Param("discount_rate", "Ставка дисконтирования", "доля", 0.08, 0.0, 0.15, "plan",
                           _plan_assumption("discount_rate"), "ставка не задана организатором, это TEAM_ASSUMPTION"),
    "earth_new_prep": Param("earth_new_prep", "Подготовка Earth-New", "мес.", 18, 18, 24, "plan",
                            _plan_assumption("earth_new_prep_months"), "кейс задаёт интервал 18–24 месяца"),
    "opening_stock": Param("opening_stock", "Начальный запас", "т", 12.4, 0.0, 40.0, "plan", _opening_stock,
                           "решение команды о подготовительном периоде"),
}


def metrics(res: RunResult) -> Dict[str, float]:
    hard = [v for v in res.violations if v.severity == "hard"]
    return {
        "pv_mln": res.totals["discounted_cost_mln"],
        "total_mln": res.totals["total_cost_mln"],
        "sl_total": res.totals["sl_total"],
        "sl_critical": res.totals["sl_critical"],
        "shortage_t": res.totals["shortage_t"],
        "losses_t": res.totals["losses_t"],
        "hard_violations": float(len(hard)),
        "feasible": 1.0 if not hard else 0.0,
        "codes": ",".join(sorted({v.code for v in hard})),
    }


def evaluate(case: CaseInput, scenario: Scenario, plan: Plan, key: str, value: float) -> Dict:
    param = PARAMS[key]
    sc, pl = param.apply(case, scenario, plan, value)
    row = metrics(run(case, sc, pl))
    row["value"] = value
    return row


def sweep(case: CaseInput, scenario: Scenario, plan: Plan, key: str,
          values: Optional[List[float]] = None, steps: int = 9) -> List[Dict]:
    param = PARAMS[key]
    if values is None:
        values = [param.low + (param.high - param.low) * i / (steps - 1) for i in range(steps)]
    return [evaluate(case, scenario, plan, key, v) for v in values]


def threshold(case: CaseInput, scenario: Scenario, plan: Plan, key: str,
              predicate: Optional[Callable[[Dict], bool]] = None,
              tol: float = 1e-3, max_iter: int = 40) -> Optional[Dict]:
    """Граница выполнимости по одному параметру.

    Предполагается монотонность метрики по параметру в заданном диапазоне: это допущение
    раскрывается, а результат всегда сопровождается значениями на обоих концах отрезка.
    """
    param = PARAMS[key]
    ok = predicate or (lambda row: row["feasible"] == 1.0)
    lo_row, hi_row = evaluate(case, scenario, plan, key, param.low), evaluate(case, scenario, plan, key, param.high)
    if ok(lo_row) == ok(hi_row):
        return {"key": key, "label": param.label, "unit": param.unit, "boundary": None,
                "low": lo_row, "high": hi_row,
                "note": "в заданном диапазоне план не меняет статус выполнимости"}
    good, bad = (param.low, param.high) if ok(lo_row) else (param.high, param.low)
    for _ in range(max_iter):
        if abs(good - bad) <= tol:
            break
        mid = (good + bad) / 2
        row = evaluate(case, scenario, plan, key, mid)
        good, bad = (mid, bad) if ok(row) else (good, mid)
    boundary_row = evaluate(case, scenario, plan, key, bad)
    return {"key": key, "label": param.label, "unit": param.unit, "boundary": bad,
            "last_feasible": good, "first_violation": boundary_row["codes"],
            "low": lo_row, "high": hi_row,
            "note": "порог найден делением отрезка пополам при допущении монотонности"}


def tornado(case: CaseInput, scenario: Scenario, plan: Plan, keys: Optional[List[str]] = None,
            delta: float = 0.10) -> List[Dict]:
    """Вклад параметров в приведённые расходы при отклонении на ±delta от базового значения."""
    base_pv = metrics(run(case, scenario, plan))["pv_mln"]
    out = []
    for key in keys or list(PARAMS):
        param = PARAMS[key]
        lo = max(param.low, param.base * (1 - delta)) if param.base else param.low
        hi = min(param.high, param.base * (1 + delta)) if param.base else param.high
        if param.base == 0:
            lo, hi = param.low, param.high
        low_row, high_row = evaluate(case, scenario, plan, key, lo), evaluate(case, scenario, plan, key, hi)
        out.append({
            "key": key, "label": param.label, "unit": param.unit, "base_pv": base_pv,
            "low_value": lo, "high_value": hi,
            "pv_low": low_row["pv_mln"], "pv_high": high_row["pv_mln"],
            "swing": abs(high_row["pv_mln"] - low_row["pv_mln"]),
            "breaks_plan": bool(low_row["feasible"] == 0.0 or high_row["feasible"] == 0.0),
        })
    return sorted(out, key=lambda r: r["swing"], reverse=True)


def reverse_stress(case: CaseInput, scenario: Scenario, plan: Plan,
                   keys: Optional[List[str]] = None) -> List[Dict]:
    """Обратный стресс: при каком значении каждого параметра план впервые ломается."""
    rows = []
    for key in keys or ["demand", "price_earth", "isru_delivery", "core_capacity", "earth_new_prep"]:
        res = threshold(case, scenario, plan, key)
        if res:
            rows.append(res)
    return rows
