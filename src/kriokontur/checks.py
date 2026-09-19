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


# Диапазоны допущений. Всё, что не задано организатором, проверяется здесь: иначе
# «чувствительность» превращается в подгонку результата недопустимым значением.
ASSUMPTION_RANGES = {
    # ключ: (минимум, максимум, единица, основание)
    "discount_rate": (0.0, 0.20, "доля", "TEAM_ASSUMPTION: реальная ставка 0–20% годовых"),
    "discount_base_year": (2030, 2050, "год", "момент приведения внутри горизонта кейса"),
    "emergency_lead_steps": (1, 6, "шаг", "6 недель Emergency при месячном шаге это 2 шага (CASE_INPUT)"),
    "earth_new_prep_months": (18, 24, "мес.", "CASE_INPUT: подготовка Earth-New 18–24 месяца"),
    "isru_commissioning_lag_months": (1, 2, "мес.", "CASE_INPUT: пусконаладка Lunar-ISRU 1–2 месяца"),
    "reserve_ramp_months": (1, 12, "мес.", "TEAM_ASSUMPTION: набор резерва к следующему году"),
    "reserve_safety_factor": (1.0, 2.0, "доля", "TEAM_ASSUMPTION: запас над требуемым резервом"),
    "emergency_base_share_threshold": (0.0, 1.0, "доля", "TEAM_ASSUMPTION: порог «Emergency как базовый канал»"),
    "storage_commissioning_lag_months": (0, 24, "мес.", "TEAM_ASSUMPTION: пусконаладка модернизации хранилища"),
}


def _invalid(field: str, period, value, reason: str) -> Violation:
    return Violation("INPUT_INVALID", "hard", "ALL", period, field, None, None, None,
                     f"INPUT_INVALID field={field}" + (f" year={period}" if period is not None else "")
                     + f" value={value!r} {reason}")


def _number(value) -> Optional[float]:
    """Число или None. Строки, NaN и бесконечности числами не считаются."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if v == v and abs(v) != float("inf") else None


def validate_envelope(case: CaseInput, raw) -> List[Violation]:
    """Проверка переносимого конверта плана до расчёта.

    Возвращает список нарушений INPUT_INVALID с именем поля, годом и причиной.
    Вызывается API до `Plan.from_envelope`, поэтому пользователь получает 422
    с понятным текстом, а не 500 на приведении типов.
    """
    out: List[Violation] = []
    if not isinstance(raw, dict):
        return [_invalid("plan", None, raw, "ожидается объект плана")]
    decisions = raw.get("decisions")
    if decisions is None:
        return [_invalid("decisions", None, None, "раздел decisions обязателен: в нём резервы, заказы и инвестиции")]
    if not isinstance(decisions, dict):
        return [_invalid("decisions", None, decisions, "раздел decisions должен быть объектом")]

    known_sources = set(case.sources)
    known_years = set(case.years)
    for section, field_name in (("capacity_reservations", "reserved_t_per_year"), ("supply_orders", "ordered_t")):
        rows = decisions.get(section) or []
        if not isinstance(rows, list):
            out.append(_invalid(section, None, rows, "ожидается список строк"))
            continue
        for row in rows:
            if not isinstance(row, dict):
                out.append(_invalid(section, None, row, "строка должна быть объектом"))
                continue
            src, year, value = row.get("source_id"), row.get("year"), row.get(field_name)
            if src not in known_sources:
                out.append(_invalid(f"{section}.source_id", year, src,
                                    f"неизвестный канал, допустимы {sorted(known_sources)}"))
            try:
                year_int = int(year)
            except (TypeError, ValueError):
                out.append(_invalid(f"{section}.year", year, year, "год должен быть целым числом"))
                continue
            if year_int not in known_years:
                out.append(_invalid(f"{section}.year", year_int, year_int,
                                    f"год вне горизонта {min(known_years)}–{max(known_years)}"))
            num = _number(value)
            if num is None:
                out.append(_invalid(f"{section}.{field_name}", year_int, value,
                                    "требуется конечное число не меньше нуля"))
            elif num < 0:
                out.append(_invalid(f"{section}.{field_name}", year_int, value, "объём не может быть отрицательным"))

    investments = decisions.get("investments") or []
    known_invest = set(case.investments) | set(case.storage) - {"BASE"}
    known_invest |= {f"{k}_OPTION" for k in case.investments} | {f"{k}_EXERCISE" for k in case.investments}
    chosen: Dict[str, Optional[int]] = {}
    if not isinstance(investments, list):
        out.append(_invalid("investments", None, investments, "ожидается список решений"))
    else:
        for row in investments:
            if not isinstance(row, dict):
                out.append(_invalid("investments", None, row, "строка должна быть объектом"))
                continue
            key, year = row.get("investment_id"), row.get("decision_year")
            if key not in known_invest:
                out.append(_invalid("investments.investment_id", None, key,
                                    f"неизвестная инвестиция, допустимы {sorted(known_invest)}"))
                continue
            if year is None:
                chosen[key] = None
                continue
            try:
                year_int = int(year)
            except (TypeError, ValueError):
                out.append(_invalid("investments.decision_year", None, year, "год должен быть целым числом"))
                continue
            if year_int not in known_years:
                out.append(_invalid("investments.decision_year", year_int, year_int,
                                    f"год вне горизонта {min(known_years)}–{max(known_years)}"))
            chosen[key] = year_int

    policy = decisions.get("inventory_policy") or {}
    if not isinstance(policy, dict):
        out.append(_invalid("inventory_policy", None, policy, "ожидается объект политики запаса"))
    else:
        mode = policy.get("mode", "physical")
        if mode not in ("physical", "contract"):
            out.append(_invalid("inventory_policy.mode", None, mode, "допустимо physical или contract"))
        opening = _number(policy.get("opening_stock_t", 0.0))
        if opening is None:
            out.append(_invalid("inventory_policy.opening_stock_t", case.years[0], policy.get("opening_stock_t"),
                                "требуется конечное число не меньше нуля"))
        else:
            first_year = case.years[0]
            active = case.storage["BASE"]
            for key, year in chosen.items():
                if year is not None and key in case.storage and key != "BASE" and year <= first_year:
                    active = case.storage[key]
            if opening < 0:
                out.append(_invalid("inventory_policy.opening_stock_t", first_year, opening,
                                    "начальный запас не может быть отрицательным"))
            elif opening > active.capacity_t + 1e-9:
                out.append(_invalid("inventory_policy.opening_stock_t", first_year, opening,
                                    f"начальный запас больше ёмкости хранилища {active.capacity_t:.0f} т "
                                    f"(режим {active.storage_id})"))

    assumptions = raw.get("assumptions") or {}
    if not isinstance(assumptions, dict):
        out.append(_invalid("assumptions", None, assumptions, "ожидается объект допущений"))
    else:
        for key, (low, high, unit, basis) in ASSUMPTION_RANGES.items():
            if key not in assumptions:
                continue
            num = _number(assumptions[key])
            if num is None:
                out.append(_invalid(f"assumptions.{key}", None, assumptions[key], f"требуется число, {unit}"))
            elif not (low - 1e-9 <= num <= high + 1e-9):
                out.append(_invalid(f"assumptions.{key}", None, num,
                                    f"вне допустимого диапазона {low}–{high} {unit}; основание: {basis}"))
    return out


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
    # явный заказ не может превышать законтрактованный объём канала (CALCULATION_RULES §13)
    for s in case.source_list:
        for year in case.years:
            ordered = plan.ordered(s.source_id, year)
            reserved = plan.reserved(s.source_id, year)
            if ordered is None or not isinstance(ordered, (int, float)) or ordered != ordered:
                continue
            if ordered > reserved + 1e-9:
                out.append(Violation("ORDER_EXCEEDS_RESERVATION", "hard", "ALL", year, f"ordered_{s.source_id}",
                                     round(float(ordered), 2), round(float(reserved), 2),
                                     round(float(ordered) - float(reserved), 2),
                                     f"ORDER_EXCEEDS_RESERVATION source={s.source_id} year={year} "
                                     f"ordered={ordered:.1f} reserved={reserved:.1f} "
                                     f"excess={ordered - reserved:.1f} причина: отбор сверх законтрактованного объёма"))
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

        # фактический отбор против договора: резерв × доля года доступности (CALCULATION_RULES §13).
        # Проверка независима от диспетчера и ловит любой отбор сверх договора, включая аварийный канал.
        for s in case.source_list:
            months_on = rules.months_available(availability.get(s.source_id), i)
            contracted = plan.reserved(s.source_id, yr.year) * months_on / 12
            offtake = yr.ordered_t.get(s.source_id, 0.0)
            if offtake > contracted + 1e-6:
                out.append(Violation("CONTRACT_OFFTAKE_EXCEEDED", "hard", sid, yr.year, f"offtake_{s.source_id}",
                                     round(offtake, 2), round(contracted, 2), round(offtake - contracted, 2),
                                     f"CONTRACT_OFFTAKE_EXCEEDED source={s.source_id} year={yr.year} "
                                     f"offtake={offtake:.1f} contracted={contracted:.1f} "
                                     f"excess={offtake - contracted:.1f} причина: отбор превышает договор"))

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
