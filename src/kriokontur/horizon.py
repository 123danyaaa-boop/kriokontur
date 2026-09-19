"""Расчёт на перспективу: горизонт за пределами 2035–2040.

Кейс требует показать последствия сегодняшних решений за горизонтом обязательного плана,
но при двух условиях: расширение делается на копии набора и все добавленные величины
объявлены как допущения команды, а не выдаются за данные организатора.

Поэтому здесь:
    * исходный CaseInput не меняется, функция возвращает новый объект;
    * версия набора получает суффикс, чтобы прогон нельзя было спутать с контрольным;
    * каждая добавленная строка спроса, мощности и инвестиции имеет статус TEAM_RESEARCH;
    * ограничения кейса на добавленные годы автоматически не переносятся: лимиты CAPEX
      проверяются на 2037 и 2040 годах, как и было, а для новых лет лимит объявляется отдельно.

Способы продления спроса (раскрываются в отчёте):
    increment   сохраняем последний годовой прирост кейса (2039→2040 это +70 т/год);
    saturating  логистическое насыщение к заявленному потолку;
    plateau     спрос остаётся на уровне 2040 года.
Ни один из них не является прогнозом организатора: это три явных гипотезы, и вывод
проверяется на всех трёх.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .caseinput import CaseInput, InvestmentOption, Source, StorageOption

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "horizon.yaml"


@dataclass
class HorizonConfig:
    last_year: int
    methods: Dict[str, Dict]
    low_factor: float
    high_factor: float
    extensions: Dict[str, Dict] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    @staticmethod
    def load(path: Path | str = CONFIG_PATH) -> "HorizonConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return HorizonConfig(
            last_year=int(raw["last_year"]),
            methods=raw["demand_methods"],
            low_factor=float(raw.get("low_factor", 0.8)),
            high_factor=float(raw.get("high_factor", 1.25)),
            extensions=raw.get("extensions", {}),
            notes=raw.get("notes", []),
        )


def extrapolate_demand(case: CaseInput, last_year: int, method: str, params: Dict) -> Dict[int, float]:
    """Возвращает общий спрос по годам за пределами кейса. Метод раскрывается в отчёте."""
    years = case.years
    known = dict(case.demand_total)
    kind = params.get("type", method)
    last = years[-1]
    step = known[last] - known[last - 1]
    for year in range(last + 1, last_year + 1):
        prev = known[year - 1]
        if kind == "increment":
            step = step * float(params.get("decay", 1.0))
            known[year] = prev + step
        elif kind == "logistic":
            ceiling = float(params["ceiling"])
            rate = float(params.get("rate", 0.45))
            known[year] = prev + rate * prev * (1 - prev / ceiling)
        elif kind == "flat":
            known[year] = prev
        else:
            raise ValueError(f"неизвестный способ продления спроса: {kind}")
    return {y: round(v, 1) for y, v in known.items()}


def extend_case(case: CaseInput, cfg: HorizonConfig, method: str = "increment",
                enable: Optional[List[str]] = None) -> CaseInput:
    """Копия CASE_INPUT с продлённым горизонтом и включёнными расширениями мощностей."""
    ext = copy.deepcopy(case)
    params = cfg.methods[method]
    total = extrapolate_demand(case, cfg.last_year, method, params)
    share = case.demand_critical[case.years[-1]] / case.demand_total[case.years[-1]]

    ext.years = list(range(case.years[0], cfg.last_year + 1))
    for year in ext.years:
        ext.demand_total[year] = total[year]
        if year not in case.demand_critical:
            ext.demand_critical[year] = round(total[year] * share, 1)
            ext.demand_low[year] = round(total[year] * cfg.low_factor, 1)
            ext.demand_high[year] = round(total[year] * cfg.high_factor, 1)

    for key in enable or []:
        block = cfg.extensions[key]
        if "source" in block:
            s = block["source"]
            ext.sources[s["id"]] = Source(
                source_id=s["id"], name=s["name"], capacity_t_per_year=float(s["capacity"]),
                variable_cost_mln_per_t=float(s["price"]), reservation_rate=float(s.get("reservation", 0.0)),
                take_or_pay_share=float(s.get("take_or_pay", 0.0)), lead_time_min=float(s.get("lead", 2)),
                lead_time_max=float(s.get("lead_max", s.get("lead", 2))), lead_time_unit="month",
                reliability_profile=s.get("reliability", "constant:0.9"),
                available_from_year=int(s["available_from"]), notes="TEAM_RESEARCH, расширение горизонта")
        if "investment" in block:
            inv = block["investment"]
            ext.investments[key] = InvestmentOption(
                investment_id=key, name=block.get("name", key), option_fee_mln=float(inv.get("option_fee", 0.0)),
                exercise_cost_mln=float(inv["exercise_cost"]), total_capex_mln=float(inv.get("total", inv["exercise_cost"])),
                commissioning_rule=inv.get("rule", "TEAM_RESEARCH"), fixed_opex_mln_per_year=float(inv.get("opex", 0.0)))
        if "storage" in block:
            st = block["storage"]
            ext.storage[key] = StorageOption(
                storage_id=key, name=block.get("name", key), capacity_t=float(st["capacity"]),
                loss_rate_on_throughput=float(st["loss"]), holding_cost_mln_per_t_year=float(st.get("holding", 0.72)),
                capex_mln=float(st["capex"]), fixed_opex_mln_per_year=float(st.get("opex", 0.0)),
                available_from_year=int(st.get("from", case.years[-1] + 1)))

    ext.version = f"{case.version}+horizon{cfg.last_year}-{method}" + (f"+{'+'.join(enable)}" if enable else "")
    return ext


def earth_ceiling(case: CaseInput, include_emergency: bool = False) -> float:
    """Физический потолок земной архитектуры, т/год: сумма мощностей земных каналов."""
    total = sum(s.capacity_t_per_year for s in case.source_list
                if s.source_id in ("A", "B", "C", "C2"))
    if include_emergency:
        total += case.sources["E"].capacity_t_per_year
    return total


def first_infeasible_year(case: CaseInput, ceiling: float, loss_rate: float = 0.012) -> Optional[int]:
    """Первый год, где валовая потребность превышает потолок мощностей."""
    for year in case.years:
        gross = case.demand_total[year] / (1 - loss_rate)
        if gross > ceiling + 1e-9:
            return year
    return None


def cumulative_discounted(res, base_year: int, rate: float) -> Dict[int, float]:
    """Накопленные приведённые расходы по годам: на этом строится год окупаемости."""
    out, acc = {}, 0.0
    for y in res.years:
        acc += y.total_cost_mln / ((1 + rate) ** (y.year - base_year))
        out[y.year] = acc
    return out


def payback_year(with_invest: Dict[int, float], without_invest: Dict[int, float]) -> Optional[int]:
    """Год, после которого вариант с инвестицией навсегда становится дешевле накопленным итогом.

    Ищем последний год, где инвестиционный вариант ещё дороже, и берём следующий за ним.
    Так ранние годы до самой инвестиции не выдаются за окупаемость.
    """
    years = sorted(y for y in with_invest if y in without_invest)
    last_expensive = None
    for year in years:
        if with_invest[year] > without_invest[year] + 1e-9:
            last_expensive = year
    if last_expensive is None:
        return years[0] if years else None
    nxt = [y for y in years if y > last_expensive]
    return nxt[0] if nxt else None


def cost_per_served(res, base_year: int, rate: float) -> Dict[int, float]:
    """Накопленные приведённые расходы на накопленную обслуженную тонну.

    Нужен, потому что архитектуры с разным обслуживанием нельзя сравнивать по одним расходам:
    вариант, который просто не выдал топливо, всегда выглядит дешевле.
    """
    out, cost, served = {}, 0.0, 0.0
    for y in res.years:
        cost += y.total_cost_mln / ((1 + rate) ** (y.year - base_year))
        served += y.served_t
        out[y.year] = cost / served if served else 0.0
    return out


def first_deficit_year(res) -> Optional[int]:
    for y in res.years:
        if y.shortage_t > 0.05:
            return y.year
    return None
