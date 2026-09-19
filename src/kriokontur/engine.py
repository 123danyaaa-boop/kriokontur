"""Расчётное ядро: помесячный прогон плана в заданном сценарии.

Поток расчёта (он же порядок функций в файле):

    CASE_INPUT + Scenario + Plan
      -> availability()      когда канал реально может поставлять (lead time, ввод мощности)
      -> dispatch()          сколько заказываем в каждом месяце и что фактически пришло
      -> material balance     I_end = I_start + Q_delivered - Losses - Q_served
      -> costs()             закупка, take-or-pay, резервирование, хранение, OPEX, CAPEX
      -> checks.run_checks() нарушения с годом, величиной и причиной
      -> RunResult           годовые строки, помесячный след, деньги, нарушения, журнал

Шаг расчёта месячный: он различает lead time 6 недель, 4 и 12 месяцев, показывает
внутригодовой дефицит и переполнение хранилища. Годовые обязательства (take-or-pay,
плата за резервирование) считаются один раз за год и не дублируются по месяцам.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import ENGINE_VERSION
from . import rules
from .caseinput import CaseInput, StorageOption
from .checks import Violation, run_checks
from .plan import Plan
from .scenarios import Scenario

MONTHS = 12
MERIT_EXCLUDE = {"E"}  # аварийный канал не участвует в плановом добора по цене


@dataclass
class MonthRow:
    index: int
    year: int
    month: int
    opening_t: float
    ordered_t: Dict[str, float]
    delivered_t: Dict[str, float]
    gross_t: float
    losses_t: float
    served_t: float
    served_critical_t: float
    shortage_t: float
    closing_t: float
    capacity_t: float
    target_t: float


@dataclass
class YearRow:
    year: int
    demand_total_t: float
    demand_critical_t: float
    reserved_t: Dict[str, float] = field(default_factory=dict)
    ordered_t: Dict[str, float] = field(default_factory=dict)
    delivered_t: Dict[str, float] = field(default_factory=dict)
    payable_t: Dict[str, float] = field(default_factory=dict)
    gross_t: float = 0.0
    losses_t: float = 0.0
    served_t: float = 0.0
    served_critical_t: float = 0.0
    shortage_t: float = 0.0
    shortage_critical_t: float = 0.0
    opening_t: float = 0.0
    closing_t: float = 0.0
    avg_stock_t: float = 0.0
    max_stock_t: float = 0.0
    capacity_t: float = 0.0
    reserve_required_t: float = 0.0
    cost: Dict[str, float] = field(default_factory=lambda: {
        "procurement": 0.0, "reservation": 0.0, "holding": 0.0, "fixed_opex": 0.0, "capex": 0.0})
    total_cost_mln: float = 0.0
    discounted_cost_mln: float = 0.0

    @property
    def sl_total(self) -> float:
        return rules.service_level(self.served_t, self.demand_total_t)

    @property
    def sl_critical(self) -> float:
        return rules.service_level(self.served_critical_t, self.demand_critical_t)

    @property
    def loss_share(self) -> float:
        return self.losses_t / self.gross_t if self.gross_t > 0 else 0.0

    @property
    def emergency_share(self) -> float:
        return self.ordered_t.get("E", 0.0) / self.demand_total_t if self.demand_total_t > 0 else 0.0


@dataclass
class RunResult:
    plan_id: str
    scenario_id: str
    case_version: str
    engine_version: str
    created_at: str
    assumptions: Dict
    years: List[YearRow]
    months: List[MonthRow]
    totals: Dict[str, float]
    violations: List[Violation]
    log: List[Dict]

    def year(self, year: int) -> YearRow:
        return next(y for y in self.years if y.year == year)

    @property
    def feasible(self) -> bool:
        return not any(v.severity == "hard" for v in self.violations)


# --------------------------------------------------------------------------- #
# 1. доступность каналов и мощностей
# --------------------------------------------------------------------------- #
def availability(case: CaseInput, plan: Plan, scenario=None) -> Dict[str, Optional[int]]:
    """Первый месяц (индекс от января 2035), когда канал может поставлять.

    A и B: заказ размещается в подготовительном периоде 2034 года, поэтому lead time
    12 и 4 месяца выполнены к январю 2035 (TEAM_ASSUMPTION, см. docs/MATH_MODEL.md).
    C: ввод через earth_new_prep_months после года реализации опциона.
    D: не раньше 2038 года и не раньше, чем через isru_commissioning_lag_months после ввода.
    E: доступен всегда, но заказ идёт emergency_lead_steps месячных шагов.
    """
    first_year = case.years[0]
    delay = (lambda sid: scenario.commissioning_delay(case.sources[sid].name, sid)) if scenario else (lambda sid: 0.0)
    out: Dict[str, Optional[int]] = {}
    # общее правило для любого канала: с года доступности из данных плюс возможная задержка сценария.
    # Дальше специальные правила для каналов с инвестиционным вводом (C и D).
    for sid, src in case.sources.items():
        start = src.available_from_year
        out[sid] = None if start is None else int(max(0, (start - first_year) * MONTHS) + delay(sid))

    exercise = plan.investments.get("EARTH_NEW_EXERCISE")
    prep = float(plan.assume("earth_new_prep_months"))
    out["C"] = None if exercise is None else int((exercise - first_year) * MONTHS + prep + delay("C"))

    isru_year = plan.investments.get("LUNAR_ISRU")
    if isru_year is None:
        out["D"] = None
    else:
        lag = float(plan.assume("isru_commissioning_lag_months"))
        from_year = case.sources["D"].available_from_year or 2038
        out["D"] = int(max((from_year - first_year) * MONTHS + lag, (isru_year - first_year) * MONTHS) + delay("D"))
    return out


def months_available(avail_month: Optional[int], year_index: int) -> int:
    """Сколько месяцев года канал доступен: база для period_fraction."""
    if avail_month is None:
        return 0
    start, end = year_index * MONTHS, year_index * MONTHS + MONTHS
    return int(max(0, min(end, 10 ** 6) - max(start, avail_month)))


def storage_mode(case: CaseInput, plan: Plan, year: int) -> StorageOption:
    """Активный режим хранилища: последняя введённая модернизация на этот год.

    Правило общее, поэтому вторая очередь хранилища работает без правок кода:
    достаточно строки в storage_options.csv и решения в плане.
    """
    best, best_year = case.storage["BASE"], -10 ** 6
    for key, decision_year in plan.investments.items():
        if decision_year is None or key not in case.storage or key == "BASE":
            continue
        if decision_year <= year and decision_year > best_year:
            best, best_year = case.storage[key], decision_year
    return best


INVESTMENT_SOURCE = {"LUNAR_ISRU": "D", "LUNAR_ISRU_PHASE2": "D2", "EARTH_NEW": "C"}


def capex_schedule(case: CaseInput, plan: Plan) -> Dict[int, float]:
    """CAPEX по годам решения. Ключи плана читаются по общему правилу:

        <ID>_OPTION    плата за право из investment_options.csv
        <ID>_EXERCISE  плата за реализацию
        <ID>           идентификатор инвестиции или режима хранилища целиком
    """
    out = {y: 0.0 for y in case.years}
    for key, year in plan.investments.items():
        if year is None or year not in out:
            continue
        if key.endswith("_OPTION") and key[:-7] in case.investments:
            out[year] += case.investments[key[:-7]].option_fee_mln
        elif key.endswith("_EXERCISE") and key[:-9] in case.investments:
            out[year] += case.investments[key[:-9]].exercise_cost_mln
        elif key in case.storage:
            out[year] += case.storage[key].capex_mln
        elif key in case.investments:
            out[year] += case.investments[key].exercise_cost_mln
    return out


def fixed_opex(case: CaseInput, plan: Plan, year: int) -> float:
    """Постоянный OPEX года: активный режим хранилища плюс введённые инвестиции."""
    total = 0.0
    store = storage_mode(case, plan, year)
    if store.storage_id != "BASE":
        total += store.fixed_opex_mln_per_year
    for key, decision_year in plan.investments.items():
        if decision_year is None or key in case.storage:
            continue
        base = key[:-9] if key.endswith("_EXERCISE") else key[:-7] if key.endswith("_OPTION") else key
        inv = case.investments.get(base)
        if inv is None or inv.fixed_opex_mln_per_year == 0:
            continue
        source_id = INVESTMENT_SOURCE.get(base)
        start = case.sources[source_id].available_from_year if source_id in case.sources else decision_year
        if start is not None and year >= max(start, decision_year):
            total += inv.fixed_opex_mln_per_year
    return total

# --------------------------------------------------------------------------- #
# 2. основной прогон
# --------------------------------------------------------------------------- #
def run(case: CaseInput, scenario: Scenario, plan: Plan) -> RunResult:
    first_year = case.years[0]
    avail = availability(case, plan, scenario)
    merit = sorted([s for s in case.source_list if s.source_id not in MERIT_EXCLUDE],
                   key=lambda s: s.variable_cost_mln_per_t)
    lead_steps = int(plan.assume("emergency_lead_steps"))
    ramp_months = float(plan.assume("reserve_ramp_months"))
    safety = float(plan.assume("reserve_safety_factor"))

    years = [YearRow(year=y,
                     demand_total_t=scenario.demand_total(case, y),
                     demand_critical_t=scenario.demand_critical(case, y),
                     reserved_t={s.source_id: plan.reserved(s.source_id, y) for s in case.source_list},
                     ordered_t={s.source_id: 0.0 for s in case.source_list},
                     delivered_t={s.source_id: 0.0 for s in case.source_list},
                     reserve_required_t=rules.reserve_days_to_tonnes(scenario.demand_total(case, y)))
             for y in case.years]
    months: List[MonthRow] = []
    log: List[Dict] = []
    structural: List[Violation] = []

    stock = float(plan.inventory_policy.get("opening_stock_t", 0.0))
    emergency_pipeline: Dict[int, float] = {}
    total_months = len(case.years) * MONTHS

    for m in range(total_months):
        i, month = divmod(m, MONTHS)
        year = case.years[i]
        yr = years[i]
        store = storage_mode(case, plan, year)
        if month == 0:
            yr.opening_t = stock
            yr.capacity_t = store.capacity_t

        demand_m = yr.demand_total_t / MONTHS
        crit_m = yr.demand_critical_t / MONTHS

        # целевой запас: плавный набор к требованию следующего года
        r_now = yr.reserve_required_t
        r_next = years[i + 1].reserve_required_t if i + 1 < len(years) else r_now
        ramp = min(1.0, max(0.0, (month - (MONTHS - ramp_months - 1)) / ramp_months))
        target = (r_now + (r_next - r_now) * ramp) * safety

        ordered = {s.source_id: 0.0 for s in case.source_list}
        net = 1.0 - store.loss_rate_on_throughput

        def room_left() -> float:
            taken = sum(ordered.values()) * net
            return max(0.0, store.capacity_t - stock - taken)

        def take(sid: str, want_net: float) -> float:
            """Заказать столько, чтобы после потерь добавить want_net тонн."""
            if want_net <= 1e-9 or avail.get(sid) is None or m < avail[sid]:
                return 0.0
            cap_factor = scenario.capacity_factor(case.sources[sid].name, sid, year)
            cap_month = plan.reserved(sid, year) / MONTHS * cap_factor
            explicit = plan.ordered(sid, year)
            if explicit is not None:
                cap_month = explicit / max(1, months_available(avail.get(sid), i))
            gross_want = min(want_net / net, max(0.0, cap_month - ordered[sid]), room_left() / net)
            if gross_want <= 1e-9:
                return 0.0
            ordered[sid] += gross_want
            return gross_want * net

        # 2.1 явные заказы команды и минимумы take-or-pay
        for s in case.source_list:
            if s.source_id in MERIT_EXCLUDE:
                continue
            explicit = plan.ordered(s.source_id, year)
            if explicit is not None:
                take(s.source_id, explicit / max(1, months_available(avail.get(s.source_id), i)) * net)
            elif s.take_or_pay_share > 0:
                take(s.source_id, s.take_or_pay_share * plan.reserved(s.source_id, year) / MONTHS * net)

        # 2.2 добор по возрастанию переменной цены
        for s in merit:
            need = demand_m + target - stock - sum(ordered.values()) * net
            if need > 1e-9 and plan.ordered(s.source_id, year) is None:
                take(s.source_id, need)

        # 2.3 аварийный канал: смотрим на lead_steps вперёд
        reserved_e = plan.reserved("E", year)
        if reserved_e > 0:
            projected = stock + sum(ordered.values()) * net - demand_m
            for k in range(1, lead_steps + 1):
                mk = m + k
                if mk >= total_months:
                    break
                ik = mk // MONTHS
                inflow_k = sum(min(plan.reserved(s.source_id, case.years[ik]) / MONTHS,
                                   years[ik].demand_total_t / MONTHS)
                               for s in merit if avail.get(s.source_id) is not None and mk >= avail[s.source_id])
                projected += inflow_k * net + emergency_pipeline.get(mk, 0.0) - years[ik].demand_total_t / MONTHS
            if projected < -1e-6:
                arrive = m + lead_steps
                if arrive < total_months:
                    want = min(-projected, reserved_e / MONTHS * lead_steps)
                    if want > 1e-6:
                        emergency_pipeline[arrive] = emergency_pipeline.get(arrive, 0.0) + want
                        log.append({"month": m, "year": year, "kind": "emergency",
                                    "title": "Вызов аварийного канала",
                                    "text": f"Заказано {want:.1f} т, прибытие через {lead_steps} шага "
                                            f"({case.years[arrive // MONTHS]}-{arrive % MONTHS + 1:02d})."})
        ordered["E"] = emergency_pipeline.pop(m, 0.0)

        # 2.4 фактическая поставка сценария (для BASE равна заказу)
        delivered = {}
        for s in case.source_list:
            share = scenario.delivery_factor(s.name, s.source_id, year)
            delivered[s.source_id] = rules.actual_delivery(ordered[s.source_id], share)
        gross = sum(delivered.values())
        losses = rules.losses_on_throughput(gross, store.loss_rate_on_throughput)
        stock = rules.closing_inventory(stock, gross, losses, 0.0)

        if stock > store.capacity_t + 1e-6:
            structural.append(Violation(
                code="STORAGE_OVERFLOW", severity="hard", scenario_id=scenario.scenario_id, period=year,
                metric="physical_inventory", value=round(stock, 2), limit=store.capacity_t,
                excess=round(stock - store.capacity_t, 2),
                message=f"STORAGE_OVERFLOW year={year} month={month + 1} inventory={stock:.1f} "
                        f"capacity={store.capacity_t:.0f} excess={stock - store.capacity_t:.1f}"))
            stock = store.capacity_t

        served_crit = min(crit_m, stock)
        stock -= served_crit
        served_rest = min(max(0.0, demand_m - crit_m), stock)
        stock -= served_rest
        served = served_crit + served_rest
        short = rules.shortage(demand_m, served)
        short_crit = rules.shortage(crit_m, served_crit)

        for sid in ordered:
            yr.ordered_t[sid] += ordered[sid]
            yr.delivered_t[sid] += delivered[sid]
        yr.gross_t += gross
        yr.losses_t += losses
        yr.served_t += served
        yr.served_critical_t += served_crit
        yr.shortage_t += short
        yr.shortage_critical_t += short_crit
        yr.avg_stock_t += stock / MONTHS
        yr.max_stock_t = max(yr.max_stock_t, stock)
        if month == MONTHS - 1:
            yr.closing_t = stock
        if short > 0.01:
            log.append({"month": m, "year": year, "kind": "shortage", "title": "Дефицит",
                        "text": f"Не выдано {short:.1f} т"
                                + (f", в том числе критического спроса {short_crit:.1f} т" if short_crit > 0.01 else "")
                                + "."})

        months.append(MonthRow(index=m, year=year, month=month + 1, opening_t=yr.opening_t if month == 0 else months[-1].closing_t,
                               ordered_t=dict(ordered), delivered_t=dict(delivered), gross_t=gross, losses_t=losses,
                               served_t=served, served_critical_t=served_crit, shortage_t=short, closing_t=stock,
                               capacity_t=store.capacity_t, target_t=target))

    # ----------------------------------------------------------------------- #
    # 3. деньги
    # ----------------------------------------------------------------------- #
    # накопление float по 12 месяцам может дать 1e-16 сверх годового спроса: срезаем,
    # чтобы уровень обслуживания оставался долей и не превышал единицу
    for yr in years:
        yr.served_t = min(yr.served_t, yr.demand_total_t)
        yr.served_critical_t = min(yr.served_critical_t, yr.demand_critical_t)

    capex = capex_schedule(case, plan)
    rate = float(plan.assume("discount_rate"))
    base_year = int(plan.assume("discount_base_year"))
    opening_source = case.sources[str(plan.assume("opening_stock_source"))]

    for i, yr in enumerate(years):
        store = storage_mode(case, plan, yr.year)
        for s in case.source_list:
            fraction = months_available(avail.get(s.source_id), i) / MONTHS
            reserved_period = plan.reserved(s.source_id, yr.year) * fraction
            price = s.variable_cost_mln_per_t * scenario.price_factor(s.name, s.source_id, yr.year)
            yr.payable_t[s.source_id] = rules.payable_volume(yr.ordered_t[s.source_id], s.take_or_pay_share, reserved_period)
            yr.cost["procurement"] += rules.variable_payment(price, yr.ordered_t[s.source_id],
                                                             s.take_or_pay_share, reserved_period)
            yr.cost["reservation"] += rules.reservation_payment(s.reservation_rate,
                                                                plan.reserved(s.source_id, yr.year), fraction)
        yr.cost["holding"] = yr.avg_stock_t * store.holding_cost_mln_per_t_year
        yr.cost["fixed_opex"] = fixed_opex(case, plan, yr.year)
        yr.cost["capex"] = capex.get(yr.year, 0.0)
        if i == 0:
            # начальный запас не бесплатен: закупка подготовительного периода, отнесена на первый год
            yr.cost["procurement"] += float(plan.inventory_policy.get("opening_stock_t", 0.0)) * opening_source.variable_cost_mln_per_t
        yr.total_cost_mln = sum(yr.cost.values())
        yr.discounted_cost_mln = rules.discount(yr.total_cost_mln, yr.year, base_year, rate)

    totals = {
        "procurement": sum(y.cost["procurement"] for y in years),
        "reservation": sum(y.cost["reservation"] for y in years),
        "holding": sum(y.cost["holding"] for y in years),
        "fixed_opex": sum(y.cost["fixed_opex"] for y in years),
        "capex": sum(y.cost["capex"] for y in years),
        "total_cost_mln": sum(y.total_cost_mln for y in years),
        "discounted_cost_mln": sum(y.discounted_cost_mln for y in years),
        "demand_t": sum(y.demand_total_t for y in years),
        "served_t": sum(y.served_t for y in years),
        "demand_critical_t": sum(y.demand_critical_t for y in years),
        "served_critical_t": sum(y.served_critical_t for y in years),
        "shortage_t": sum(y.shortage_t for y in years),
        "losses_t": sum(y.losses_t for y in years),
        "gross_t": sum(y.gross_t for y in years),
    }
    totals["sl_total"] = rules.service_level(totals["served_t"], totals["demand_t"])
    totals["sl_critical"] = rules.service_level(totals["served_critical_t"], totals["demand_critical_t"])
    totals["cost_per_served_t"] = totals["total_cost_mln"] / totals["served_t"] if totals["served_t"] else 0.0

    violations = structural + run_checks(case, scenario, plan, years, capex, avail)
    for inv, year in plan.investments.items():
        if year is not None:
            log.append({"month": (year - first_year) * MONTHS, "year": year, "kind": "investment",
                        "title": f"Инвестиционное решение: {inv}",
                        "text": f"CAPEX {capex.get(year, 0.0):.0f} млн у.е. в {year} году."})
    for v in violations:
        if isinstance(v.period, int):
            log.append({"month": (v.period - first_year) * MONTHS, "year": v.period, "kind": "violation",
                        "title": v.code, "text": v.message})
    log.sort(key=lambda e: e["month"])

    return RunResult(
        plan_id=plan.plan_id, scenario_id=scenario.scenario_id, case_version=case.version,
        engine_version=ENGINE_VERSION, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        assumptions=dict(plan.assumptions), years=years, months=months, totals=totals,
        violations=violations, log=log)
