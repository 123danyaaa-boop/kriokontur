"""Риски: реестр, связанный с расчётом, и вероятностный блок.

Правило кейса: риск это не клетка качественной матрицы, а изменение конкретного входа
модели с измеримым последствием. Поэтому каждый риск ссылается на сценарий, сценарий
пересчитывается тем же движком, а последствие считается как разница с базовым прогоном.

Надёжность каналов в BASE не является множителем поставки. Здесь она используется
только внутри отдельного вероятностного блока и с явно раскрытым смыслом:
    reliability = вероятность того, что канал за год поставит весь заказанный объём.
При отказе фактическая доля поставки берётся из Uniform(partial_low, partial_high).
Оба допущения это TEAM_ASSUMPTION, они печатаются вместе с результатом.
"""
from __future__ import annotations

import copy
import math
import random
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .caseinput import CaseInput
from .engine import run
from .plan import Plan
from .scenarios import Scenario
from .sensitivity import metrics

from .paths import CONFIGS, RISKS_CONFIG as RISKS_PATH  # единая точка правды по путям, см. paths.py

MITIGATIONS_PATH = CONFIGS / "mitigations.yaml"


# --------------------------------------------------------------------------- #
# реестр рисков
# --------------------------------------------------------------------------- #
@dataclass
class Risk:
    risk_id: str
    event: str
    cause: str = ""
    affected_parameter: str = ""
    period: str = ""
    probability: Optional[float] = None
    probability_low: Optional[float] = None
    probability_high: Optional[float] = None
    probability_basis: str = ""
    scenario_id: str = "BASE"
    owner: str = ""
    mitigation: str = ""
    mitigations: List[str] = field(default_factory=list)
    residual: str = ""
    dependencies: str = ""
    # риск может менять не только сценарий, но и вход плана: сроки ввода, допущения,
    # политику запаса. Иначе задержка пусконаладки или ошибка планирования не моделируются.
    plan_overrides: Dict = field(default_factory=dict)


@dataclass
class Mitigation:
    mitigation_id: str
    label: str
    description: str = ""
    owner: str = ""
    plan_overrides: Dict = field(default_factory=dict)
    basis: str = ""


def load_risks(path: Path | str = RISKS_PATH) -> List[Risk]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    known = set(Risk.__dataclass_fields__)
    return [Risk(**{k: v for k, v in row.items() if k in known}) for row in raw]


def load_mitigations(path: Path | str = MITIGATIONS_PATH) -> Dict[str, Mitigation]:
    if not Path(path).exists():
        return {}
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    known = set(Mitigation.__dataclass_fields__)
    rows = [Mitigation(**{k: v for k, v in row.items() if k in known}) for row in raw]
    return {m.mitigation_id: m for m in rows}


def apply_plan_overrides(plan: Plan, overrides: Optional[Dict]) -> Plan:
    """Копия плана с изменёнными решениями: резервы, инвестиции, политика запаса, допущения.

    Используется и рисками (событие меняет вход модели), и мерами (ответ меняет решение).
    Исходный план не трогается, поэтому сравнение всегда идёт на одной базе.
    """
    if not overrides:
        return plan
    p = copy.deepcopy(plan)
    for sid, years in (overrides.get("reservations") or {}).items():
        for year, value in years.items():
            p.reservations.setdefault(sid, {})[int(year)] = float(value)
    for sid, years in (overrides.get("orders") or {}).items():
        for year, value in years.items():
            p.orders.setdefault(sid, {})[int(year)] = float(value)
    for key, year in (overrides.get("investments") or {}).items():
        p.investments[key] = None if year is None else int(year)
    p.inventory_policy = {**p.inventory_policy, **(overrides.get("inventory_policy") or {})}
    p.assumptions = {**p.assumptions, **(overrides.get("assumptions") or {})}
    return p


def _consequence(case: CaseInput, scenario: Scenario, plan: Plan, base: Dict) -> Dict:
    """Последствие в метриках, которые кейс просит показать: тонны, сервис, деньги, нарушения."""
    res = run(case, scenario, plan)
    m = metrics(res)
    unused = res.totals.get("unused_paid_t", 0.0)
    return {
        "pv_mln": m["pv_mln"],
        "delta_pv_mln": m["pv_mln"] - base["pv_mln"],
        "shortage_t": m["shortage_t"],
        "delta_shortage_t": m["shortage_t"] - base["shortage_t"],
        "sl_total": m["sl_total"],
        "sl_total_worst": min(y.sl_total for y in res.years),
        "delta_sl_total": m["sl_total"] - base["sl_total"],
        "sl_critical_worst": min(y.sl_critical for y in res.years),
        "unused_paid_t": unused,
        "delta_unused_paid_t": unused - base.get("unused_paid_t", 0.0),
        "cost_per_served_t": res.totals["cost_per_served_t"],
        "delta_cost_per_served_t": res.totals["cost_per_served_t"] - base["cost_per_served_t"],
        "emergency_delivered_t": sum(y.delivered_t.get("E", 0.0) for y in res.years),
        "hard_violations": m["hard_violations"],
        "violation_codes": m["codes"],
    }


def _baseline_metrics(case: CaseInput, scenario: Scenario, plan: Plan) -> Dict:
    res = run(case, scenario, plan)
    m = metrics(res)
    m["unused_paid_t"] = res.totals.get("unused_paid_t", 0.0)
    m["cost_per_served_t"] = res.totals["cost_per_served_t"]
    return m


def reference_scenario_id(scenarios: Dict[str, Scenario], risk: Risk, baseline: str) -> str:
    """Сценарий, относительно которого измеряется последствие риска.

    Событие на стороне плана (задержка ввода, изменение политики) сравнивается с тем же
    сценарием без изменения плана. Событие на стороне сценария сравнивается с родительским
    сценарием: риск, надстроенный над обязательным стрессом, не должен приписывать себе
    ещё и весь эффект стресса.
    """
    if risk.plan_overrides:
        return risk.scenario_id
    parent = scenarios[risk.scenario_id].base_scenario
    if parent and parent in scenarios and parent != risk.scenario_id:
        return parent
    return baseline


def evaluate_risks(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
                   risks: List[Risk], baseline: str = "BASE") -> List[Dict]:
    """Последствие риска = разница прогонов «риск» и «база» на одном и том же плане.

    Риск может менять сценарий (спрос, цены, доступность) и вход плана (сроки ввода,
    допущения, политику запаса). И то и другое считается тем же движком.
    """
    base_plan_metrics = _baseline_metrics(case, scenarios[baseline], plan)
    rows = []
    for risk in risks:
        scenario = scenarios.get(risk.scenario_id)
        if scenario is None:
            rows.append({**asdict(risk), "error": f"сценарий {risk.scenario_id} не найден"})
            continue
        risk_plan = apply_plan_overrides(plan, risk.plan_overrides)
        cons = _consequence(case, scenario, risk_plan, base_plan_metrics)
        # полная разница к базовому сценарию смешивает эффект условий и эффект самого события.
        # Поэтому рядом считаем собственный вклад риска: разницу с его опорным сценарием.
        ref_id = reference_scenario_id(scenarios, risk, baseline)
        scenario_ref = _baseline_metrics(case, scenarios[ref_id], plan)
        cons["reference_scenario_id"] = ref_id
        cons["scenario_reference_pv_mln"] = scenario_ref["pv_mln"]
        cons["delta_pv_marginal_mln"] = cons["pv_mln"] - scenario_ref["pv_mln"]
        marginal = cons["delta_pv_marginal_mln"]
        cons["consequence_pv_mln"] = marginal
        cons["delta_shortage_marginal_t"] = cons["shortage_t"] - scenario_ref["shortage_t"]
        probability = risk.probability
        expected = None if probability is None else probability * marginal
        expected_range = None
        if risk.probability_low is not None and risk.probability_high is not None:
            expected_range = [risk.probability_low * marginal, risk.probability_high * marginal]
        rows.append({
            **asdict(risk),
            "baseline": baseline,
            **cons,
            "expected_delta_pv_mln": expected,
            "expected_delta_pv_range_mln": expected_range,
            "material": abs(marginal) > 0.5 or cons["delta_shortage_t"] > 0.05
                        or cons["hard_violations"] > 0 or cons["delta_unused_paid_t"] > 0.05
                        or abs(cons["delta_cost_per_served_t"]) > 0.01,
        })
    return rows


def evaluate_mitigations(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
                         risks: List[Risk], mitigations: Optional[Dict[str, Mitigation]] = None,
                         baseline: str = "BASE") -> List[Dict]:
    """Стоимость меры, её эффект и остаточный риск — расчётом, а не словами.

        стоимость меры   = PV(план с мерой, базовый сценарий) − PV(плана, базовый сценарий);
        эффект           = последствие риска без меры − последствие риска с мерой;
        остаточный риск  = последствие риска с мерой.
    """
    mitigations = mitigations if mitigations is not None else load_mitigations()
    base_plan_metrics = _baseline_metrics(case, scenarios[baseline], plan)
    rows = []
    for risk in risks:
        scenario = scenarios.get(risk.scenario_id)
        if scenario is None:
            continue
        # опора та же, что у последствия риска: событие на стороне плана сравнивается с тем же
        # сценарием, событие на стороне сценария — с его родительским сценарием
        reference = _baseline_metrics(case, scenarios[reference_scenario_id(scenarios, risk, baseline)],
                                      plan)
        risk_plan = apply_plan_overrides(plan, risk.plan_overrides)
        without = _consequence(case, scenario, risk_plan, reference)
        for mid in risk.mitigations:
            m = mitigations.get(mid)
            if m is None:
                rows.append({"risk_id": risk.risk_id, "mitigation_id": mid,
                             "error": f"мера {mid} не найдена в configs/mitigations.yaml"})
                continue
            mitigated_plan = apply_plan_overrides(apply_plan_overrides(plan, m.plan_overrides),
                                                  risk.plan_overrides)
            with_m = _consequence(case, scenario, mitigated_plan, reference)
            cost = _baseline_metrics(case, scenarios[baseline],
                                     apply_plan_overrides(plan, m.plan_overrides))["pv_mln"] \
                - base_plan_metrics["pv_mln"]
            effect = without["delta_pv_mln"] - with_m["delta_pv_mln"]
            served_effect = without["delta_shortage_t"] - with_m["delta_shortage_t"]
            expected_net = None
            if risk.probability is not None:
                expected_net = risk.probability * effect - cost
            expected_net_range = None
            if risk.probability_low is not None and risk.probability_high is not None:
                expected_net_range = [risk.probability_low * effect - cost,
                                      risk.probability_high * effect - cost]
            rows.append({
                "risk_id": risk.risk_id, "risk_event": risk.event,
                "mitigation_id": mid, "mitigation": m.label, "description": m.description,
                "owner": m.owner or risk.owner, "basis": m.basis,
                "cost_mln": cost,
                "consequence_without_mln": without["delta_pv_mln"],
                "consequence_with_mln": with_m["delta_pv_mln"],
                "effect_mln": effect,
                "shortage_without_t": without["delta_shortage_t"],
                "shortage_with_t": with_m["delta_shortage_t"],
                "shortage_avoided_t": served_effect,
                "sl_worst_without": without["sl_total_worst"],
                "sl_worst_with": with_m["sl_total_worst"],
                "hard_without": without["hard_violations"],
                "hard_with": with_m["hard_violations"],
                "residual_mln": with_m["delta_pv_mln"],
                "residual_shortage_t": with_m["delta_shortage_t"],
                "residual_codes": with_m["violation_codes"],
                "net_benefit_mln": effect - cost,
                "expected_net_benefit_mln": expected_net,
                "expected_net_benefit_range_mln": expected_net_range,
            })
    return rows


# --------------------------------------------------------------------------- #
# геополитический модуль: событие задаёт пользователь
# --------------------------------------------------------------------------- #
def geo_event(case: CaseInput, base: Scenario, label: str, sources: List[str],
              start_year: int, end_year: int, multiplier: float,
              component: str = "variable_price", scenario_id: Optional[str] = None,
              basis: str = "TEAM_ASSUMPTION, сценарное допущение без статистики") -> Scenario:
    """Строит исследовательский сценарий из параметров события.

    component: variable_price (цена канала) или capacity (доступность канала).
    Правило сочетания: множитель применяется только к тем годам, где базовый сценарий
    не менял этот же параметр, поэтому один эффект не начисляется дважды.
    """
    years = [y for y in case.years if start_year <= y <= end_year]
    sid = scenario_id or f"TEAM_GEO_{component.upper()}_{int(multiplier * 100)}"
    if component == "variable_price":
        table = {k: dict(v) for k, v in base.price_multiplier.items()}
        for s in sources:
            name = case.sources[s].name if s in case.sources else s
            cur = table.get(name, {})
            for y in years:
                if cur.get(y, 1.0) == 1.0:          # базовый сценарий эту цену не трогал
                    cur[y] = multiplier
            table[name] = cur
        sc = base.derive(sid, label, price_multiplier=table)
    elif component == "capacity":
        table = {k: dict(v) for k, v in base.capacity_multiplier.items()}
        for s in sources:
            name = case.sources[s].name if s in case.sources else s
            cur = table.get(name, {})
            for y in years:
                cur[y] = multiplier
            table[name] = cur
        sc = base.derive(sid, label, capacity_multiplier=table)
    else:
        raise ValueError("component должен быть variable_price или capacity")
    sc.combination_rule = (f"Событие «{label}»: {component} каналов {', '.join(sources)} умножается на "
                           f"{multiplier} в {start_year}–{end_year}. Годы, где базовый сценарий уже менял "
                           f"этот параметр, не затрагиваются. Основание: {basis}. "
                           f"Возврат к контрольным ценам делается выбором базового сценария.")
    return sc


# --------------------------------------------------------------------------- #
# Монте-Карло
# --------------------------------------------------------------------------- #
def parse_reliability(profile: str, year: int, first_operating_year: Optional[int]) -> float:
    """Читает профиль надёжности из data/supply_sources.csv.

    Форматы кейса: constant:0.96 | first_operating_year:0.88;later:0.94 | 2038:0.78;2039:0.90;2040:0.93
    """
    parts = [p for p in profile.split(";") if p]
    table: Dict[str, float] = {}
    for part in parts:
        key, _, value = part.partition(":")
        table[key.strip()] = float(value)
    if "constant" in table:
        return table["constant"]
    if str(year) in table:
        return table[str(year)]
    if "first_operating_year" in table:
        if first_operating_year is not None and year == first_operating_year:
            return table["first_operating_year"]
        return table.get("later", 1.0)
    return 1.0


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> List[float]:
    """Доверительный интервал доли по Уилсону (95% при z = 1,96).

    Обычный нормальный интервал на малых долях и малом числе прогонов даёт отрицательные
    границы и занижает ширину, поэтому берётся интервал Уилсона: он корректен при p, близких
    к нулю, что для вероятности нарушения и есть типичный случай.
    """
    if trials <= 0:
        return [0.0, 0.0]
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def first_operating_year(case: CaseInput, plan: Plan, source_id: str) -> Optional[int]:
    """Первый год фактической работы канала при этом плане.

    Нужен для профиля надёжности вида first_operating_year:0.88;later:0.94. Раньше для
    Earth-New сюда подставлялся None, и первый год молча считался по ставке 0,94 (D-12).
    """
    from .engine import availability, months_available
    avail = availability(case, plan)
    start = avail.get(source_id)
    if start is None:
        return None
    for i, year in enumerate(case.years):
        if months_available(start, i) > 0:
            return year
    return None


@dataclass
class MonteCarloResult:
    trials: int
    seed: int
    interpretation: str
    assumptions: Dict
    p_hard_violation: float
    p_hard_violation_ci: List[float]
    p_service_below_min: float
    p_service_below_min_ci: List[float]
    p_shortage: float
    p_shortage_ci: List[float]
    pv_mean: float
    pv_mean_ci: List[float]
    pv_p50: float
    pv_p95: float
    shortage_mean_t: float
    shortage_p95_t: float
    first_operating_year: Dict[str, Optional[int]] = field(default_factory=dict)
    reliability_used: Dict[str, Dict[int, float]] = field(default_factory=dict)
    worst_codes: Dict[str, int] = field(default_factory=dict)


def monte_carlo(case: CaseInput, scenario: Scenario, plan: Plan, trials: int = 300, seed: int = 20260918,
                partial_low: float = 0.5, partial_high: float = 0.9,
                sources: Optional[List[str]] = None) -> MonteCarloResult:
    rng = random.Random(seed)
    sources = sources or ["A", "B", "C", "D"]
    # первый рабочий год берётся из плана, а не задаётся руками: для Earth-New это год ввода
    # после реализации опциона, для Lunar-ISRU — год пуска установки
    first_op = {sid: first_operating_year(case, plan, sid) for sid in sources}
    used_reliability = {
        sid: {y: parse_reliability(case.sources[sid].reliability_profile, y, first_op.get(sid))
              for y in case.years}
        for sid in sources}
    pv, shortage, hard, service_fail, short_fail = [], [], 0, 0, 0
    codes: Dict[str, int] = {}

    for _ in range(trials):
        table = {k: dict(v) for k, v in scenario.delivery_share.items()}
        for sid in sources:
            src = case.sources[sid]
            row = table.get(src.name, table.get(sid, {}))
            for year in case.years:
                base_share = row.get(year, row.get("default", 1.0))
                rel = parse_reliability(src.reliability_profile, year, first_op.get(sid))
                draw = 1.0 if rng.random() < rel else rng.uniform(partial_low, partial_high)
                row[year] = base_share * draw     # сценарная доля и отказ независимы, эффект не дублируется
            table[src.name] = row
        sc = scenario.derive(f"{scenario.scenario_id}+mc", "Монте-Карло", delivery_share=table)
        m = metrics(run(case, sc, plan))
        pv.append(m["pv_mln"])
        shortage.append(m["shortage_t"])
        if m["hard_violations"] > 0:
            hard += 1
        if m["sl_total"] < case.constraint("BASE_TOTAL_SERVICE").value - 1e-9:
            service_fail += 1
        if m["shortage_t"] > 0.05:
            short_fail += 1
        for code in filter(None, m["codes"].split(",")):
            codes[code] = codes.get(code, 0) + 1

    pv_sorted, sh_sorted = sorted(pv), sorted(shortage)
    pick = lambda arr, q: arr[min(len(arr) - 1, int(q * len(arr)))]
    pv_sd = statistics.pstdev(pv) if len(pv) > 1 else 0.0
    pv_half = 1.96 * pv_sd / math.sqrt(len(pv)) if pv else 0.0
    pv_mean = statistics.fmean(pv) if pv else 0.0
    return MonteCarloResult(
        trials=trials, seed=seed,
        interpretation=("reliability = вероятность полной годовой поставки канала; при отказе доля "
                        "поставки берётся из Uniform(partial_low, partial_high). Доли обязательного "
                        "стресса 55% и 75% на надёжность повторно не умножаются: случайный множитель "
                        "применяется к сценарной доле один раз."),
        assumptions={
            "partial_low": partial_low, "partial_high": partial_high,
            "independence": "отказы независимы между каналами и годами",
            "independence_basis": ("статистики зависимостей нет; независимость это верхняя оценка "
                                   "разнообразия исходов, зависимые отказы проверяются отдельными "
                                   "сценариями рисков, а не распределением"),
            "distribution_basis": ("равномерное распределение доли при отказе выбрано как "
                                   "максимально неинформативное: данных о форме распределения нет"),
            "sources": sources, "base_scenario": scenario.scenario_id,
            "confidence_level": 0.95, "interval_method": "Wilson для долей, нормальный для среднего PV",
        },
        p_hard_violation=hard / trials, p_hard_violation_ci=wilson_interval(hard, trials),
        p_service_below_min=service_fail / trials,
        p_service_below_min_ci=wilson_interval(service_fail, trials),
        p_shortage=short_fail / trials, p_shortage_ci=wilson_interval(short_fail, trials),
        pv_mean=pv_mean, pv_mean_ci=[pv_mean - pv_half, pv_mean + pv_half],
        pv_p50=pick(pv_sorted, 0.5), pv_p95=pick(pv_sorted, 0.95),
        shortage_mean_t=statistics.fmean(shortage), shortage_p95_t=pick(sh_sorted, 0.95),
        first_operating_year=first_op, reliability_used=used_reliability,
        worst_codes=dict(sorted(codes.items(), key=lambda kv: -kv[1])),
    )
