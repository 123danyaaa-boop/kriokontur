"""Загрузка CASE_INPUT из data/*.csv.

Файлы повторяют стартовый репозиторий организатора (SpaceEconomyPolicy/test_oil).
Ни одно значение здесь не правится кодом: модель только читает. Версия набора
считается как sha256 по содержимому файлов и попадает в каждый прогон.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .paths import DATA as DATA_DIR  # единая точка правды по путям, см. paths.py


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    capacity_t_per_year: float
    variable_cost_mln_per_t: float
    reservation_rate: float
    take_or_pay_share: float
    lead_time_min: float
    lead_time_max: float
    lead_time_unit: str
    reliability_profile: str
    available_from_year: Optional[int]
    notes: str = ""

    def lead_time_months(self, use: str = "min") -> float:
        value = self.lead_time_min if use == "min" else self.lead_time_max
        if self.lead_time_unit == "week":
            return value / 4.345  # недели переводим в месяцы, конвенция описана в допущениях
        if self.lead_time_unit == "day":
            return value / 30.44
        return value


@dataclass(frozen=True)
class StorageOption:
    storage_id: str
    name: str
    capacity_t: float
    loss_rate_on_throughput: float
    holding_cost_mln_per_t_year: float
    capex_mln: float
    fixed_opex_mln_per_year: float
    available_from_year: int


@dataclass(frozen=True)
class InvestmentOption:
    investment_id: str
    name: str
    option_fee_mln: float
    exercise_cost_mln: float
    total_capex_mln: float
    commissioning_rule: str
    fixed_opex_mln_per_year: float


@dataclass(frozen=True)
class Constraint:
    constraint_id: str
    metric: str
    operator: str
    value: float
    unit: str
    period: str
    scenario: str
    severity: str
    description: str


@dataclass
class CaseInput:
    years: List[int]
    demand_total: Dict[int, float]
    demand_critical: Dict[int, float]
    demand_low: Dict[int, float]
    demand_high: Dict[int, float]
    sources: Dict[str, Source]
    storage: Dict[str, StorageOption]
    investments: Dict[str, InvestmentOption]
    constraints: Dict[str, Constraint]
    version: str = ""
    currency_note: str = "млн условных денежных единиц в постоянных ценах 2035 года"

    @property
    def source_list(self) -> List[Source]:
        return [self.sources[k] for k in sorted(self.sources)]

    def constraint(self, cid: str) -> Constraint:
        return self.constraints[cid]


def _rows(path: Path) -> List[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _int_or_none(value: str) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value else None


def load_case(data_dir: Path | str = DATA_DIR) -> CaseInput:
    data_dir = Path(data_dir)
    demand_rows = _rows(data_dir / "demand.csv")
    years = [int(r["year"]) for r in demand_rows]
    case = CaseInput(
        years=years,
        demand_total={int(r["year"]): float(r["base_total_t"]) for r in demand_rows},
        demand_critical={int(r["year"]): float(r["base_critical_t"]) for r in demand_rows},
        demand_low={int(r["year"]): float(r["low_total_t"]) for r in demand_rows},
        demand_high={int(r["year"]): float(r["high_total_t"]) for r in demand_rows},
        sources={
            r["source_id"]: Source(
                source_id=r["source_id"],
                name=r["name"],
                capacity_t_per_year=float(r["capacity_t_per_year"]),
                variable_cost_mln_per_t=float(r["variable_cost_mln_per_t"]),
                reservation_rate=float(r["reservation_rate_mln_per_t_year_capacity"]),
                take_or_pay_share=float(r["take_or_pay_share"]),
                lead_time_min=float(r["lead_time_min_value"]),
                lead_time_max=float(r["lead_time_max_value"]),
                lead_time_unit=r["lead_time_unit"],
                reliability_profile=r["reliability_profile"],
                available_from_year=_int_or_none(r["available_from_year"]),
                notes=r.get("notes", ""),
            )
            for r in _rows(data_dir / "supply_sources.csv")
        },
        storage={
            r["storage_id"]: StorageOption(
                storage_id=r["storage_id"],
                name=r["name"],
                capacity_t=float(r["capacity_t"]),
                loss_rate_on_throughput=float(r["loss_rate_on_throughput"]),
                holding_cost_mln_per_t_year=float(r["holding_cost_mln_per_t_year"]),
                capex_mln=float(r["capex_mln"]),
                fixed_opex_mln_per_year=float(r["fixed_opex_mln_per_year"]),
                available_from_year=int(r["available_from_year"]),
            )
            for r in _rows(data_dir / "storage_options.csv")
        },
        investments={
            r["investment_id"]: InvestmentOption(
                investment_id=r["investment_id"],
                name=r["name"],
                option_fee_mln=float(r["option_fee_mln"]),
                exercise_cost_mln=float(r["exercise_cost_mln"]),
                total_capex_mln=float(r["total_capex_mln"]),
                commissioning_rule=r["commissioning_rule"],
                fixed_opex_mln_per_year=float(r["fixed_opex_mln_per_year"]),
            )
            for r in _rows(data_dir / "investment_options.csv")
        },
        constraints={
            r["constraint_id"]: Constraint(
                constraint_id=r["constraint_id"],
                metric=r["metric"],
                operator=r["operator"],
                value=float(r["value"]),
                unit=r["unit"],
                period=r["period"],
                scenario=r["scenario"],
                severity=r["severity"],
                description=r["description"],
            )
            for r in _rows(data_dir / "constraints.csv")
        },
    )
    case.version = case_version(data_dir)
    return case


def case_version(data_dir: Path | str = DATA_DIR) -> str:
    """sha256 по контрольным файлам: попадает в каждый прогон и выгрузку."""
    digest = hashlib.sha256()
    for name in sorted(p.name for p in Path(data_dir).glob("*.csv")):
        digest.update((Path(data_dir) / name).read_bytes())
    return digest.hexdigest()[:16]
