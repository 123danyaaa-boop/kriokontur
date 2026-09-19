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

from .paths import RISKS_CONFIG as RISKS_PATH  # единая точка правды по путям, см. paths.py


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
    probability_basis: str = ""
    scenario_id: str = "BASE"
    owner: str = ""
    mitigation: str = ""
    residual: str = ""
    dependencies: str = ""


def load_risks(path: Path | str = RISKS_PATH) -> List[Risk]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    return [Risk(**row) for row in raw]


def evaluate_risks(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan,
                   risks: List[Risk], baseline: str = "BASE") -> List[Dict]:
    """Последствие риска = разница прогонов «сценарий риска» и «базовый сценарий» на одном плане."""
    base = metrics(run(case, scenarios[baseline], plan))
    rows = []
    for risk in risks:
        scenario = scenarios.get(risk.scenario_id)
        if scenario is None:
            rows.append({**asdict(risk), "error": f"сценарий {risk.scenario_id} не найден"})
            continue
        m = metrics(run(case, scenario, plan))
        delta_cost = m["pv_mln"] - base["pv_mln"]
        rows.append({
            **asdict(risk),
            "baseline": baseline,
            "pv_mln": m["pv_mln"],
            "delta_pv_mln": delta_cost,
            "delta_shortage_t": m["shortage_t"] - base["shortage_t"],
            "sl_total": m["sl_total"],
            "delta_sl_total": m["sl_total"] - base["sl_total"],
            "hard_violations": m["hard_violations"],
            "violation_codes": m["codes"],
            "expected_delta_pv_mln": None if risk.probability is None else risk.probability * delta_cost,
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


@dataclass
class MonteCarloResult:
    trials: int
    seed: int
    interpretation: str
    assumptions: Dict
    p_hard_violation: float
    p_service_below_min: float
    pv_mean: float
    pv_p50: float
    pv_p95: float
    shortage_mean_t: float
    shortage_p95_t: float
    worst_codes: Dict[str, int] = field(default_factory=dict)


def monte_carlo(case: CaseInput, scenario: Scenario, plan: Plan, trials: int = 300, seed: int = 20260918,
                partial_low: float = 0.5, partial_high: float = 0.9,
                sources: Optional[List[str]] = None) -> MonteCarloResult:
    rng = random.Random(seed)
    sources = sources or ["A", "B", "C", "D"]
    first_op = {"C": None, "D": case.sources["D"].available_from_year}
    pv, shortage, hard, service_fail = [], [], 0, 0
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
        for code in filter(None, m["codes"].split(",")):
            codes[code] = codes.get(code, 0) + 1

    pv_sorted, sh_sorted = sorted(pv), sorted(shortage)
    pick = lambda arr, q: arr[min(len(arr) - 1, int(q * len(arr)))]
    return MonteCarloResult(
        trials=trials, seed=seed,
        interpretation="reliability = вероятность полной годовой поставки канала; при отказе доля из Uniform",
        assumptions={"partial_low": partial_low, "partial_high": partial_high,
                     "independence": "отказы независимы между каналами и годами",
                     "sources": sources, "base_scenario": scenario.scenario_id},
        p_hard_violation=hard / trials, p_service_below_min=service_fail / trials,
        pv_mean=statistics.fmean(pv), pv_p50=pick(pv_sorted, 0.5), pv_p95=pick(pv_sorted, 0.95),
        shortage_mean_t=statistics.fmean(shortage), shortage_p95_t=pick(sh_sorted, 0.95),
        worst_codes=dict(sorted(codes.items(), key=lambda kv: -kv[1])),
    )
