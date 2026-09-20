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


def _scale_reservation(source_id: str, factor: float):
    """Множитель к резервированию канала: решение команды, а не условие кейса."""
    def apply(plan: Plan, case: CaseInput) -> Plan:
        p = copy.deepcopy(plan)
        cap = case.sources[source_id].capacity_t_per_year
        p.reservations[source_id] = {y: min(cap, round(p.reserved(source_id, y) * factor, 3))
                                     for y in case.years}
        return p
    return apply


PARAMS: Dict[str, Param] = {
    "demand": Param("demand", "Спрос, множитель ко всем годам", "доля", 1.0, 0.8, 1.25, "scenario", _scale_demand,
                    "CASE_INPUT: ряды low_total_t и high_total_t дают 0,80 и 1,25 от базового "
                    "спроса; обязательный стресс добавляет 1,15 с 2038 года. Границы диапазона "
                    "взяты из самих данных кейса, а не назначены командой"),
    "price_earth": Param("price_earth", "Цена Earth-Core и Earth-Flex", "доля", 1.0, 0.75, 1.5, "scenario",
                         _scale_price(("A", "B")),
                         "CASE_INPUT: обязательный стресс задаёт +25% на 2038–2039. Вниз берём "
                         "симметричные −25%, вверх удвоенный шок +50% как сценарную границу "
                         "геополитического риска (TEAM_RESEARCH, вероятность не назначается)"),
    "price_core": Param("price_core", "Цена Earth-Core", "доля", 1.0, 0.75, 1.5, "scenario", _scale_price(("A",)),
                        "тот же диапазон, что и для пары земных каналов: проверяем вклад "
                        "главного канала отдельно от гибкого"),
    "isru_delivery": Param("isru_delivery", "Фактическая поставка Луны", "доля", 1.0, 0.55, 1.0, "scenario",
                           _scale_delivery("D"),
                           "CASE_INPUT: обязательный стресс задаёт 55% и 75% фактической поставки, "
                           "профиль надёжности 0,78–0,93. Нижняя граница 0,55 это худшее значение кейса"),
    "core_capacity": Param("core_capacity", "Доступная мощность Earth-Core", "доля", 1.0, 0.5, 1.0, "scenario",
                           _scale_capacity("A"),
                           "TEAM_RESEARCH: половина мощности это сценарий длительной приостановки "
                           "парка носителей; take-or-pay при этом не снимается"),
    "discount_rate": Param("discount_rate", "Ставка дисконтирования", "доля", 0.08, 0.0, 0.15, "plan",
                           _plan_assumption("discount_rate"),
                           "TEAM_ASSUMPTION: ставка кейсом не задана. 0% это недисконтированное "
                           "сравнение, 15% верхняя граница для проектов такого горизонта"),
    "earth_new_prep": Param("earth_new_prep", "Подготовка Earth-New", "мес.", 18, 18, 24, "plan",
                            _plan_assumption("earth_new_prep_months"),
                            "CASE_INPUT: кейс задаёт интервал 18–24 месяца, шире брать нельзя"),
    "opening_stock": Param("opening_stock", "Начальный запас", "т", 13.6, 0.0, 70.0, "plan", _opening_stock,
                           "TEAM_DECISION: от нуля до ёмкости базового хранилища 70 т; "
                           "верхняя граница физическая, а не назначенная"),
    "emergency_reserve": Param("emergency_reserve", "Резерв аварийного канала", "доля", 1.0, 0.0, 1.3, "plan",
                               lambda case, sc, plan, v: (sc, _scale_reservation("E", v)(plan, case)),
                               "TEAM_DECISION: от полного отказа от аварийного договора до "
                               "резерва на уровне мощности канала 80 т/год"),
}


def metrics(res: RunResult) -> Dict[str, float]:
    """Метрики чувствительности: деньги, сервис, запас и дефицит.

    Кейс требует показывать не только стоимость: метрика запаса и дефицита обязательна,
    иначе анализ не отвечает на вопрос «где план перестаёт быть исполнимым».
    """
    hard = [v for v in res.violations if v.severity == "hard"]
    return {
        "pv_mln": res.totals["discounted_cost_mln"],
        "total_mln": res.totals["total_cost_mln"],
        "sl_total": res.totals["sl_total"],
        "sl_total_worst": min(y.sl_total for y in res.years),
        "sl_critical": res.totals["sl_critical"],
        "sl_critical_worst": min(y.sl_critical for y in res.years),
        "shortage_t": res.totals["shortage_t"],
        "losses_t": res.totals["losses_t"],
        "min_closing_t": min(y.closing_t for y in res.years),
        "reserve_margin_min_t": min(y.opening_t - y.reserve_required_t for y in res.years),
        "unused_paid_t": res.totals.get("unused_paid_t", 0.0),
        "cost_per_served_t": res.totals["cost_per_served_t"],
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


def grid(case: CaseInput, scenario: Scenario, plan: Plan, key_x: str, key_y: str,
         steps: int = 5) -> Dict:
    """Двухфакторная сетка: совместное изменение двух параметров.

    Кейс прямо требует проверять совместные изменения: по одному параметру план может
    держаться, а вместе спрос и цена ломают его раньше. Сетка показывает область
    исполнимости, а не одну точку порога.
    """
    px, py = PARAMS[key_x], PARAMS[key_y]
    xs = [px.low + (px.high - px.low) * i / (steps - 1) for i in range(steps)]
    ys = [py.low + (py.high - py.low) * i / (steps - 1) for i in range(steps)]
    cells = []
    for vy in ys:
        row = []
        for vx in xs:
            sc, pl = px.apply(case, scenario, plan, vx)
            sc, pl = py.apply(case, sc, pl, vy)
            m = metrics(run(case, sc, pl))
            row.append({"x": vx, "y": vy, "pv_mln": m["pv_mln"], "shortage_t": m["shortage_t"],
                        "sl_total_worst": m["sl_total_worst"],
                        "reserve_margin_min_t": m["reserve_margin_min_t"],
                        "feasible": bool(m["feasible"]), "codes": m["codes"]})
        cells.append(row)
    infeasible = [c for row in cells for c in row if not c["feasible"]]
    return {
        "x": {"key": key_x, "label": px.label, "unit": px.unit, "values": xs, "basis": px.basis},
        "y": {"key": key_y, "label": py.label, "unit": py.unit, "values": ys, "basis": py.basis},
        "cells": cells,
        "feasible_cells": sum(1 for row in cells for c in row if c["feasible"]),
        "total_cells": steps * steps,
        "first_infeasible": min(infeasible, key=lambda c: (c["x"], c["y"])) if infeasible else None,
        "note": ("Совместное изменение двух параметров: клетка «неисполнимо» означает жёсткое "
                 "нарушение хотя бы одного ограничения при данном сочетании значений."),
    }


def switch_point(case: CaseInput, scenario: Scenario, plan: Plan, alternative: Plan, key: str,
                 tol: float = 1e-3, max_iter: int = 40) -> Dict:
    """Порог, после которого выбранная стратегия уступает альтернативе.

    Кейс требует не просто «где план ломается», а «при каком значении параметра другой план
    становится лучше». Сравнение идёт по приведённым расходам при условии исполнимости:
    неисполнимый план не может выигрывать у исполнимого.
    """
    param = PARAMS[key]

    def better(value: float) -> Dict:
        """Правило выбора лидера, объявленное явно и одинаковое для обеих стратегий:

            1) исполнимый план всегда лучше неисполнимого;
            2) если оба неисполнимы — меньше недопоставка;
            3) при равном обслуживании — меньше приведённые расходы.

        Порядок именно такой: кейс запрещает выдавать неисполнимый план за корректный,
        поэтому дешевизна не может перевесить нарушение ограничения.
        """
        sc_a, pl_a = param.apply(case, scenario, plan, value)
        sc_b, pl_b = param.apply(case, scenario, alternative, value)
        a, b = metrics(run(case, sc_a, pl_a)), metrics(run(case, sc_b, pl_b))
        if a["feasible"] != b["feasible"]:
            wins, reason = b["feasible"] > a["feasible"], "исполнимость"
        elif abs(a["shortage_t"] - b["shortage_t"]) > 0.05:
            wins, reason = b["shortage_t"] < a["shortage_t"], "недопоставка"
        else:
            wins, reason = b["pv_mln"] < a["pv_mln"] - 1e-9, "приведённые расходы"
        return {"value": value, "plan_pv_mln": a["pv_mln"], "alternative_pv_mln": b["pv_mln"],
                "plan_shortage_t": a["shortage_t"], "alternative_shortage_t": b["shortage_t"],
                "plan_feasible": bool(a["feasible"]), "alternative_feasible": bool(b["feasible"]),
                "decided_by": reason, "alternative_wins": wins}

    lo, hi = better(param.low), better(param.high)
    if lo["alternative_wins"] == hi["alternative_wins"]:
        return {"key": key, "label": param.label, "unit": param.unit, "basis": param.basis,
                "switch_value": None, "low": lo, "high": hi,
                "note": ("в заданном диапазоне лидер не меняется: "
                         + ("альтернатива лучше везде" if lo["alternative_wins"]
                            else "выбранный план лучше везде"))}
    good, bad = (param.low, param.high) if not lo["alternative_wins"] else (param.high, param.low)
    for _ in range(max_iter):
        if abs(good - bad) <= tol:
            break
        mid = (good + bad) / 2
        good, bad = (mid, bad) if not better(mid)["alternative_wins"] else (good, mid)
    return {"key": key, "label": param.label, "unit": param.unit, "basis": param.basis,
            "switch_value": bad, "last_value_where_plan_wins": good,
            "low": lo, "high": hi, "at_switch": better(bad),
            "note": ("порог найден делением отрезка пополам при допущении монотонности разницы "
                     "приведённых расходов по параметру; обе границы диапазона показаны рядом")}
