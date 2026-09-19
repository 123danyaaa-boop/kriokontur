"""Сводные представления одного и того же прогона: KPI, сравнение сценариев,
разложение эффекта обязательного стресса, исходные данные.

Зачем отдельный модуль: интерфейс, выгрузка и управленческая записка должны показывать
одни и те же числа. Поэтому здесь нет ни одной новой формулы расчёта — только выборка,
разность и агрегирование результатов `engine.run`. Всё, что считается, считается движком.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from .caseinput import CaseInput
from .engine import RunResult, run
from .plan import Plan
from .scenarios import Scenario

CONTROL_COMPARISON = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")


# --------------------------------------------------------------------------- #
# KPI: реестр показателей надёжности и экономики
# --------------------------------------------------------------------------- #
def kpi_rows(case: CaseInput, res: RunResult) -> List[Dict]:
    """Реестр KPI с формулой, значением, целью и статусом.

    Ключевое отличие от прежнего интерфейса: уровень обслуживания показывается по худшему
    году, а не в среднем по горизонту. Средняя доля скрывает провал отдельного года,
    а ограничение кейса проверяется именно ежегодно.
    """
    years = res.years
    crit_min = case.constraint("BASE_CRITICAL_SERVICE").value
    total_min = case.constraint("BASE_TOTAL_SERVICE").value
    worst_total = min(years, key=lambda y: y.sl_total)
    worst_crit = min(years, key=lambda y: y.sl_critical)
    worst_reserve = min(years, key=lambda y: y.opening_t - y.reserve_required_t)
    worst_stock = min(years, key=lambda y: y.closing_t)
    capex_2037 = sum(y.cost["capex"] for y in years if y.year <= 2037)
    capex_total = sum(y.cost["capex"] for y in years)
    emergency = max(years, key=lambda y: y.emergency_share)
    loss_share = res.totals["losses_t"] / res.totals["gross_t"] if res.totals["gross_t"] else 0.0
    unused = res.totals.get("unused_paid_t", 0.0)

    def row(kpi_id, label, formula, value, unit, target=None, status=None, period=None):
        return {"kpi_id": kpi_id, "label": label, "formula": formula, "value": value, "unit": unit,
                "target": target, "status": status, "period": period}

    return [
        row("SL_TOTAL_WORST", "Обслуживание общее, худший год", "min_y (served_y / demand_y)",
            worst_total.sl_total, "доля", total_min,
            "ok" if worst_total.sl_total >= total_min - 1e-9 else "fail", worst_total.year),
        row("SL_CRIT_WORST", "Обслуживание критическое, худший год", "min_y (served_crit_y / demand_crit_y)",
            worst_crit.sl_critical, "доля", crit_min,
            "ok" if worst_crit.sl_critical >= crit_min - 1e-9 else "fail", worst_crit.year),
        row("SHORTAGE", "Дефицит за горизонт", "Σ_y max(0, demand_y − served_y)",
            res.totals["shortage_t"], "т", 0.0,
            "ok" if res.totals["shortage_t"] <= 1e-6 else "fail", "2035–2040"),
        row("RESERVE_MARGIN", "Запас над резервом 45 суток, худший год",
            "min_y (opening_y − demand_y · 45 / 365)",
            worst_reserve.opening_t - worst_reserve.reserve_required_t, "т", 0.0,
            "ok" if worst_reserve.opening_t >= worst_reserve.reserve_required_t - 1e-6 else "fail",
            worst_reserve.year),
        row("STOCK_MIN", "Минимальный запас на конец года", "min_y I_end,y",
            worst_stock.closing_t, "т", None, None, worst_stock.year),
        row("LOSS_SHARE", "Потери к обороту", "Σ losses / Σ throughput", loss_share, "доля", 0.02,
            "ok" if loss_share <= 0.02 + 1e-9 else "внимание", "2035–2040"),
        row("EMERGENCY_SHARE", "Доля аварийного канала, худший год", "max_y (ordered_E,y / demand_y)",
            emergency.emergency_share, "доля", 0.10,
            "ok" if emergency.emergency_share <= 0.10 + 1e-9 else "внимание", emergency.year),
        row("UNUSED_PAID", "Оплачено по take-or-pay, но не отобрано", "Σ max(0, payable − ordered)",
            unused, "т", 0.0, "ok" if unused <= 1e-6 else "внимание", "2035–2040"),
        row("OVERFLOW", "Перелив хранилища", "Σ overflow", res.totals.get("overflow_t", 0.0), "т", 0.0,
            "ok" if res.totals.get("overflow_t", 0.0) <= 1e-6 else "fail", "2035–2040"),
        row("CAPEX_2037", "CAPEX до конца 2037", "Σ_{y≤2037} capex_y", capex_2037, "млн у.е.",
            case.constraint("CAPEX_2037").value,
            "ok" if capex_2037 <= case.constraint("CAPEX_2037").value + 1e-9 else "fail", "через 2037"),
        row("CAPEX_2040", "CAPEX за горизонт", "Σ_y capex_y", capex_total, "млн у.е.",
            case.constraint("CAPEX_2040").value,
            "ok" if capex_total <= case.constraint("CAPEX_2040").value + 1e-9 else "fail", "через 2040"),
        row("PV", "Приведённые расходы", "Σ_y total_y / (1 + r)^(y − 2035)",
            res.totals["discounted_cost_mln"], "млн у.е.", None, None, "2035–2040"),
        row("TOTAL", "Расходы номиналом", "Σ_y total_y", res.totals["total_cost_mln"], "млн у.е.",
            None, None, "2035–2040"),
        row("COST_PER_T", "Расход на тонну обслуженного спроса", "total / served",
            res.totals["cost_per_served_t"], "млн у.е./т", None, None, "2035–2040"),
    ]


# --------------------------------------------------------------------------- #
# сравнение сценариев на одном плане
# --------------------------------------------------------------------------- #
def scenario_comparison(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
                        scenario_ids: Optional[List[str]] = None, baseline: str = "BASE") -> List[Dict]:
    """Один план в нескольких сценариях на единой базе: расходы, сервис, запас, дефицит."""
    ids = [sid for sid in (scenario_ids or CONTROL_COMPARISON) if sid in scenarios]
    if baseline in scenarios and baseline not in ids:
        ids.insert(0, baseline)
    base_pv = None
    rows = []
    for sid in ids:
        res = run(case, scenarios[sid], plan)
        worst = min(res.years, key=lambda y: y.sl_total)
        hard = [v for v in res.violations if v.severity == "hard"]
        if sid == baseline:
            base_pv = res.totals["discounted_cost_mln"]
        rows.append({
            "scenario_id": sid,
            "label": scenarios[sid].label,
            "status": scenarios[sid].status,
            "pv_mln": res.totals["discounted_cost_mln"],
            "total_mln": res.totals["total_cost_mln"],
            "delta_pv_mln": None if base_pv is None else res.totals["discounted_cost_mln"] - base_pv,
            "sl_total_worst": worst.sl_total,
            "sl_total_worst_year": worst.year,
            "sl_critical_worst": min(y.sl_critical for y in res.years),
            "shortage_t": res.totals["shortage_t"],
            "min_closing_t": min(y.closing_t for y in res.years),
            "reserve_margin_min_t": min(y.opening_t - y.reserve_required_t for y in res.years),
            "emergency_max_share": max(y.emergency_share for y in res.years),
            "hard_violations": len(hard),
            "violation_codes": ",".join(sorted({v.code for v in hard})),
            "feasible": res.feasible,
        })
    return rows


def strategy_comparison(case: CaseInput, scenarios: Dict[str, Scenario], plans: Dict[str, Plan],
                        scenario_ids: Optional[List[str]] = None) -> List[Dict]:
    """Несколько планов в одних и тех же сценариях: база для выбора стратегии."""
    rows = []
    for key, plan in plans.items():
        for row in scenario_comparison(case, scenarios, plan, scenario_ids):
            rows.append({"plan_id": key, "plan_name": plan.name, **row})
    return rows


# --------------------------------------------------------------------------- #
# разложение эффекта обязательного стресса
# --------------------------------------------------------------------------- #
STRESS_COMPONENTS = {
    "demand": ("Спрос +15% с 2038", ("demand_multiplier", "critical_multiplier")),
    "prices": ("Цены Земли +25% в 2038–2039", ("price_multiplier",)),
    "isru_delivery": ("Поставка Луны 55/75/100%", ("delivery_share",)),
    "loss_ceiling": ("Потолок потерь 2% с 2038", ("loss_ceiling_from_year", "loss_ceiling_value")),
}


def stress_decomposition(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
                         baseline: str = "BASE", stress: str = "MANDATORY_STRESS") -> Dict:
    """Вклад каждой составляющей стресса в приведённые расходы и в дефицит.

    Метод: от базового сценария включаем ровно одну составляющую стресса и считаем разницу
    тем же движком. Совместный эффект = полный стресс минус сумма одиночных: он показывает,
    насколько составляющие усиливают друг друга, и не даёт выдать сумму частей за целое.
    """
    base_sc, stress_sc = scenarios[baseline], scenarios[stress]
    base = run(case, base_sc, plan)
    full = run(case, stress_sc, plan)
    base_pv, base_short = base.totals["discounted_cost_mln"], base.totals["shortage_t"]

    rows = []
    for key, (label, fields) in STRESS_COMPONENTS.items():
        overrides = {f: getattr(stress_sc, f) for f in fields}
        sc = base_sc.derive(f"{baseline}+{key}", f"{baseline} плюс «{label}»", **overrides)
        res = run(case, sc, plan)
        rows.append({
            "component": key, "label": label,
            "pv_mln": res.totals["discounted_cost_mln"],
            "delta_pv_mln": res.totals["discounted_cost_mln"] - base_pv,
            "delta_shortage_t": res.totals["shortage_t"] - base_short,
            "hard_violations": sum(1 for v in res.violations if v.severity == "hard"),
        })
    single_sum = sum(r["delta_pv_mln"] for r in rows)
    total_delta = full.totals["discounted_cost_mln"] - base_pv
    return {
        "baseline": baseline, "stress": stress,
        "base_pv_mln": base_pv, "stress_pv_mln": full.totals["discounted_cost_mln"],
        "total_delta_pv_mln": total_delta,
        "components": rows,
        "interaction_pv_mln": total_delta - single_sum,
        "delta_shortage_t": full.totals["shortage_t"] - base_short,
        "method": ("от базового сценария включается ровно одна составляющая стресса, разница считается "
                   "тем же движком; совместный эффект = полный стресс минус сумма одиночных"),
    }


# --------------------------------------------------------------------------- #
# исходные данные и допущения
# --------------------------------------------------------------------------- #
def input_data_rows(case: CaseInput, plan: Plan) -> List[List]:
    """Секция «исходные данные» выгрузки: CASE_INPUT и решения команды в одном месте."""
    rows: List[List] = [["block", "key", "field", "value"]]
    rows.append(["case", "version", "sha256_16", case.version])
    for y in case.years:
        rows.append(["demand", y, "base_total_t", case.demand_total[y]])
        rows.append(["demand", y, "base_critical_t", case.demand_critical[y]])
        rows.append(["demand", y, "low_total_t", case.demand_low[y]])
        rows.append(["demand", y, "high_total_t", case.demand_high[y]])
    for s in case.source_list:
        for field, value in asdict(s).items():
            rows.append(["source", s.source_id, field, value])
    for st in case.storage.values():
        for field, value in asdict(st).items():
            rows.append(["storage", st.storage_id, field, value])
    for inv in case.investments.values():
        for field, value in asdict(inv).items():
            rows.append(["investment", inv.investment_id, field, value])
    for c in case.constraints.values():
        for field, value in asdict(c).items():
            rows.append(["constraint", c.constraint_id, field, value])
    for sid, years in sorted(plan.reservations.items()):
        for year, value in sorted(years.items()):
            rows.append(["decision_reservation", sid, year, value])
    for sid, years in sorted(plan.orders.items()):
        for year, value in sorted(years.items()):
            rows.append(["decision_order", sid, year, value])
    for key, year in plan.investments.items():
        rows.append(["decision_investment", key, "decision_year", "" if year is None else year])
    for key, value in plan.inventory_policy.items():
        rows.append(["decision_inventory_policy", key, "value", value])
    return rows


# --------------------------------------------------------------------------- #
# сборка дополнительных разделов выгрузки
# --------------------------------------------------------------------------- #
def export_sections(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan, res: RunResult,
                    risks: Optional[List] = None, comparison_ids: Optional[List[str]] = None) -> Dict[str, List[List]]:
    """Дополнительные разделы выгрузки одним словарём «имя раздела → строки».

    Каждая строка это список значений: первая строка раздела — заголовок. Числа остаются
    числами, форматирование берёт на себя `export`.
    """
    sections: Dict[str, List[List]] = {}
    sections["input_data"] = input_data_rows(case, plan)

    kpi = kpi_rows(case, res)
    sections["kpi"] = [["kpi_id", "label", "formula", "value", "unit", "target", "status", "period"]] + [
        [r["kpi_id"], r["label"], r["formula"], r["value"], r["unit"],
         "" if r["target"] is None else r["target"], r["status"] or "", r["period"] or ""] for r in kpi]

    comp = scenario_comparison(case, scenarios, plan, comparison_ids)
    sections["scenario_comparison"] = [
        ["scenario_id", "label", "status", "pv_mln", "total_mln", "delta_pv_mln", "sl_total_worst",
         "sl_total_worst_year", "sl_critical_worst", "shortage_t", "min_closing_t",
         "reserve_margin_min_t", "emergency_max_share", "hard_violations", "violation_codes", "feasible"]] + [
        [r["scenario_id"], r["label"], r["status"], r["pv_mln"], r["total_mln"],
         "" if r["delta_pv_mln"] is None else r["delta_pv_mln"], r["sl_total_worst"],
         r["sl_total_worst_year"], r["sl_critical_worst"], r["shortage_t"], r["min_closing_t"],
         r["reserve_margin_min_t"], r["emergency_max_share"], r["hard_violations"],
         r["violation_codes"], int(r["feasible"])] for r in comp]

    if "MANDATORY_STRESS" in scenarios:
        dec = stress_decomposition(case, scenarios, plan)
        sections["stress_decomposition"] = (
            [["component", "label", "pv_mln", "delta_pv_mln", "delta_shortage_t", "hard_violations"]]
            + [[r["component"], r["label"], r["pv_mln"], r["delta_pv_mln"], r["delta_shortage_t"],
                r["hard_violations"]] for r in dec["components"]]
            + [["interaction", "Совместный эффект составляющих", "", dec["interaction_pv_mln"], "", ""],
               ["total", "Полный обязательный стресс", dec["stress_pv_mln"], dec["total_delta_pv_mln"],
                dec["delta_shortage_t"], ""]])

    try:
        from .contracts import contract_card, load_contracts, obligations
        contracts = load_contracts()
        card_keys = list(contract_card(case, contracts[0]).keys())
        sections["contracts"] = [card_keys] + [
            [contract_card(case, c)[k] for k in card_keys] for c in contracts]
        rows = obligations(case, res, contracts)
        ob_keys = list(rows[0].keys()) if rows else []
        sections["contract_obligations"] = [ob_keys] + [
            [("" if r[k] is None else r[k]) for k in ob_keys] for r in rows]
    except Exception:            # карточки договоров не должны ломать выгрузку баланса
        pass

    try:
        from .stakeholders import export_rows, impact
        ids = [sid for sid in ("BASE", "MANDATORY_STRESS") if sid in scenarios]
        risk_ids = [sid for sid in scenarios if sid.startswith("TEAM_RISK_")]
        sections["stakeholders"] = export_rows(
            impact(case, scenarios, plan, None, ids, "BASE", risk_ids))
    except Exception:
        pass

    if risks:
        from .risks import evaluate_risks
        rows = evaluate_risks(case, scenarios, plan, risks, "BASE")
        header = ["risk_id", "event", "cause", "affected_parameter", "period", "probability",
                  "probability_basis", "scenario_id", "owner", "mitigation", "residual", "dependencies",
                  "delta_pv_mln", "delta_shortage_t", "hard_violations", "violation_codes",
                  "expected_delta_pv_mln"]
        sections["risk_register"] = [header] + [
            [r.get(k, "") if r.get(k) is not None else "" for k in header] for r in rows]
    return sections
