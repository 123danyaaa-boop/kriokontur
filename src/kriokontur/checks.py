"""Проверка ограничений. Каждое нарушение это код, период, факт, лимит и превышение.

Формат сообщения повторяет пример организатора (docs раздел «Ошибки и нарушения»):
CAPACITY_EXCEEDED source=... year=... reserved=... maximum=... excess=...
Перечень ограничений берётся из data/constraints.csv, а не зашит в код.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from . import rules
from .caseinput import CaseInput
from .plan import Plan
from .scenarios import Scenario


def _finite(value):
    """NaN и бесконечности не сериализуются в JSON: наружу отдаём None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return v if v == v and abs(v) != float("inf") else None


@dataclass
class Violation:
    code: str
    severity: str           # hard = план неисполним, reference = ориентир в стрессе, info
    scenario_id: str
    period: object          # год или диапазон
    metric: str
    value: Optional[float]
    limit: Optional[float]
    excess: Optional[float]
    message: str

    def as_dict(self) -> dict:
        row = asdict(self)
        for key in ("value", "limit", "excess"):
            row[key] = _finite(row[key])
        return row


def validate_plan(case: CaseInput, plan: Plan) -> List[Violation]:
    """Проверки самого плана, до расчёта: числа, мощности, сроки инвестиций."""
    out: List[Violation] = []
    for s in case.source_list:
        for year in case.years:
            value = plan.reserved(s.source_id, year)
            ordered = plan.ordered(s.source_id, year)
            for label, v in (("reserved", value), ("ordered", ordered)):
                if v is None:
                    continue
                if not isinstance(v, (int, float)) or v != v or v < 0:
                    out.append(Violation("INPUT_INVALID", "hard", "ALL", year, f"{label}_{s.source_id}",
                                         None, None, None,
                                         f"INPUT_INVALID source={s.source_id} year={year} field={label} "
                                         f"value={v!r} требуется число не меньше нуля"))
                elif v > s.capacity_t_per_year + 1e-9:
                    out.append(Violation("CAPACITY_EXCEEDED", "hard", "ALL", year, f"{label}_{s.source_id}",
                                         round(float(v), 2), s.capacity_t_per_year,
                                         round(float(v) - s.capacity_t_per_year, 2),
                                         f"CAPACITY_EXCEEDED source={s.source_id} year={year} {label}={v:.1f} "
                                         f"maximum={s.capacity_t_per_year:.0f} excess={v - s.capacity_t_per_year:.1f}"))
    isru = plan.investments.get("LUNAR_ISRU")
    if isru is not None and isru > 2037:
        out.append(Violation("ISRU_FUNDING_LATE", "hard", "ALL", isru, "isru_financing_year", isru, 2037, isru - 2037,
                             f"ISRU_FUNDING_LATE year={isru} требование: профинансировать до 2038 года"))
    zbo = plan.investments.get("ZBO")
    if zbo is not None and zbo < case.storage["ZBO"].available_from_year:
        out.append(Violation("ZBO_NOT_AVAILABLE", "hard", "ALL", zbo, "zbo_year", zbo,
                             case.storage["ZBO"].available_from_year, None,
                             f"ZBO_NOT_AVAILABLE year={zbo} опция доступна с {case.storage['ZBO'].available_from_year}"))
    opt, exe = plan.investments.get("EARTH_NEW_OPTION"), plan.investments.get("EARTH_NEW_EXERCISE")
    if exe is not None and opt is None:
        out.append(Violation("OPTION_NOT_PURCHASED", "hard", "ALL", exe, "earth_new", exe, None, None,
                             "OPTION_NOT_PURCHASED: реализация Earth-New без покупки права за 90 млн у.е."))
    if exe is not None and opt is not None and exe < opt:
        out.append(Violation("OPTION_ORDER_INVALID", "hard", "ALL", exe, "earth_new", exe, opt, None,
                             f"OPTION_ORDER_INVALID exercise={exe} option={opt}: реализация раньше покупки права"))
    return out


def run_checks(case: CaseInput, scenario: Scenario, plan: Plan, years, capex: Dict[int, float],
               availability: Dict[str, Optional[int]]) -> List[Violation]:
    """Ограничения из constraints.csv плюс проверка доступности каналов и резерва."""
    sid = scenario.scenario_id
    out: List[Violation] = validate_plan(case, plan)

    def applies(constraint_scenario: str) -> bool:
        return constraint_scenario in ("ALL", sid)

    crit = case.constraint("BASE_CRITICAL_SERVICE")
    total = case.constraint("BASE_TOTAL_SERVICE")
    reserve_rule = case.constraint("RESERVE_45D")
    streak_rule = case.constraint("EMERGENCY_BASE_STREAK")
    loss_rule = case.constraint("STRESS_LOSS_LIMIT")

    for i, yr in enumerate(years):
        # уровень обслуживания: жёсткое требование в BASE, ориентир устойчивости в стрессе
        severity = "hard" if sid == "BASE" else "reference"
        if yr.sl_critical < crit.value - 1e-9:
            out.append(Violation("SERVICE_CRITICAL_BELOW_MIN", severity, sid, yr.year, "critical_service_level",
                                 round(yr.sl_critical, 4), crit.value, round(yr.demand_critical_t - yr.served_critical_t, 2),
                                 f"SERVICE_CRITICAL_BELOW_MIN year={yr.year} value={yr.sl_critical:.3f} "
                                 f"minimum={crit.value} shortage_t={yr.demand_critical_t - yr.served_critical_t:.1f}"))
        if yr.sl_total < total.value - 1e-9:
            out.append(Violation("SERVICE_TOTAL_BELOW_MIN", severity, sid, yr.year, "total_service_level",
                                 round(yr.sl_total, 4), total.value, round(yr.demand_total_t - yr.served_t, 2),
                                 f"SERVICE_TOTAL_BELOW_MIN year={yr.year} value={yr.sl_total:.3f} "
                                 f"minimum={total.value} shortage_t={yr.demand_total_t - yr.served_t:.1f}"))

        # 45-дневный резерв на начало года
        if applies(reserve_rule.scenario):
            mode = plan.inventory_policy.get("mode", "physical")
            required = yr.reserve_required_t
            if mode == "physical":
                if yr.opening_t < required - 1e-6:
                    out.append(Violation("RESERVE_45D_NOT_MET", "hard", sid, yr.year, "reserve_equivalent_days",
                                         round(yr.opening_t, 2), round(required, 2), round(required - yr.opening_t, 2),
                                         f"RESERVE_45D_NOT_MET year={yr.year} opening_inventory={yr.opening_t:.1f} "
                                         f"required={required:.1f} excess={required - yr.opening_t:.1f}"))
            else:
                lead_steps = int(plan.assume("emergency_lead_steps"))
                callable_t = plan.reserved("E", yr.year) / 12 * lead_steps
                waiting_cover = yr.demand_total_t * (lead_steps * 30.44) / rules.DAYS_IN_YEAR
                if callable_t < required - 1e-6 or yr.opening_t < waiting_cover - 1e-6:
                    out.append(Violation("RESERVE_EQUIVALENCE_NOT_PROVEN", "hard", sid, yr.year,
                                         "reserve_equivalent_days", round(callable_t, 2), round(required, 2), None,
                                         f"RESERVE_EQUIVALENCE_NOT_PROVEN year={yr.year} callable_t={callable_t:.1f} "
                                         f"required={required:.1f} opening_inventory={yr.opening_t:.1f} "
                                         f"waiting_cover_needed={waiting_cover:.1f}"))

        # потолок потерь обязательного стресса
        ceiling = scenario.loss_ceiling(yr.year)
        if ceiling is not None and yr.loss_share > ceiling + 1e-9:
            out.append(Violation("STRESS_LOSS_LIMIT", "hard", sid, yr.year, "losses_divided_by_throughput",
                                 round(yr.loss_share, 4), ceiling, round(yr.loss_share - ceiling, 4),
                                 f"STRESS_LOSS_LIMIT year={yr.year} losses/throughput={yr.loss_share:.3f} "
                                 f"limit={ceiling} причина: режим хранилища с потерями {yr.loss_share:.1%}"))

        # канал используется до ввода
        for s in case.source_list:
            first = availability.get(s.source_id)
            used = plan.reserved(s.source_id, yr.year) + (plan.ordered(s.source_id, yr.year) or 0.0)
            if used > 1e-9 and (first is None or first >= (i + 1) * 12):
                out.append(Violation("SOURCE_NOT_AVAILABLE", "hard", sid, yr.year, f"availability_{s.source_id}",
                                     used, None, None,
                                     f"SOURCE_NOT_AVAILABLE source={s.source_id} year={yr.year} "
                                     f"reserved_or_ordered={used:.1f} причина: мощность ещё не введена"))

    # Emergency как базовый канал
    if applies(streak_rule.scenario):
        threshold = float(plan.assume("emergency_base_share_threshold") or 0.10)
        streak = 0
        for yr in years:
            streak = streak + 1 if yr.emergency_share > threshold else 0
            if streak > streak_rule.value:
                out.append(Violation("EMERGENCY_BASE_STREAK", "hard", sid, yr.year,
                                     "emergency_base_channel_consecutive_years", streak, streak_rule.value,
                                     streak - streak_rule.value,
                                     f"EMERGENCY_BASE_STREAK year={yr.year} consecutive_years={streak} "
                                     f"limit={streak_rule.value:.0f} share={yr.emergency_share:.0%}"))

    # лимиты CAPEX
    cumulative = 0.0
    for yr in years:
        cumulative += capex.get(yr.year, 0.0)
        if yr.year == 2037:
            rule = case.constraint("CAPEX_2037")
            if cumulative > rule.value + 1e-9:
                out.append(Violation("CAPEX_2037", "hard", sid, "through_2037", "cumulative_capex",
                                     cumulative, rule.value, cumulative - rule.value,
                                     f"CAPEX_2037 cumulative={cumulative:.0f} limit={rule.value:.0f} "
                                     f"excess={cumulative - rule.value:.0f}"))
        if yr.year == 2040:
            rule = case.constraint("CAPEX_2040")
            if cumulative > rule.value + 1e-9:
                out.append(Violation("CAPEX_2040", "hard", sid, "through_2040", "cumulative_capex",
                                     cumulative, rule.value, cumulative - rule.value,
                                     f"CAPEX_2040 cumulative={cumulative:.0f} limit={rule.value:.0f} "
                                     f"excess={cumulative - rule.value:.0f}"))
    return out
