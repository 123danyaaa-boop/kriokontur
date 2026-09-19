"""TEAM_DECISION: план оператора.

Хранится в переносимом конверте организатора (schemas/plan.schema.json):
plan_id, scenario_id, decisions{capacity_reservations, supply_orders, investments, inventory_policy}.
Всё, чего нет в CASE_INPUT, лежит в assumptions с именем, значением и обоснованием.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from .paths import resolve_for_read, resolve_for_write

DEFAULT_ASSUMPTIONS = {
    "discount_rate": 0.08,
    "discount_base_year": 2035,
    "timestep": "month",
    "emergency_lead_steps": 2,
    "earth_new_prep_months": 18,
    "isru_commissioning_lag_months": 2,
    "opening_stock_source": "A",
    "reserve_ramp_months": 9,
    "reserve_safety_factor": 1.15,
    "emergency_base_share_threshold": 0.10,
    # TEAM_ASSUMPTION: модернизация хранилища работает с 1 января года решения.
    # Кейс не задаёт срок её пусконаладки, поэтому момент ввода вынесен в явный параметр.
    "storage_commissioning_lag_months": 0,
}


@dataclass
class Plan:
    plan_id: str = "plan-1"
    name: str = "План"
    reservations: Dict[str, Dict[int, float]] = field(default_factory=dict)
    orders: Dict[str, Dict[int, float]] = field(default_factory=dict)
    investments: Dict[str, Optional[int]] = field(default_factory=lambda: {
        "ZBO": None, "LUNAR_ISRU": None, "EARTH_NEW_OPTION": None, "EARTH_NEW_EXERCISE": None})
    inventory_policy: Dict = field(default_factory=lambda: {"mode": "physical", "opening_stock_t": 12.4, "target_days": 45})
    assumptions: Dict = field(default_factory=lambda: dict(DEFAULT_ASSUMPTIONS))

    def reserved(self, source_id: str, year: int) -> float:
        return float(self.reservations.get(source_id, {}).get(year, 0.0))

    def ordered(self, source_id: str, year: int) -> Optional[float]:
        value = self.orders.get(source_id, {}).get(year)
        return None if value is None else float(value)

    def assume(self, key: str):
        return self.assumptions.get(key, DEFAULT_ASSUMPTIONS.get(key))

    # --- переносимый конверт организатора ---
    def to_envelope(self, scenario_id: str = "BASE") -> dict:
        return {
            "plan_id": self.plan_id,
            "scenario_id": scenario_id,
            "name": self.name,
            "decisions": {
                "capacity_reservations": [
                    {"source_id": s, "year": y, "reserved_t_per_year": v}
                    for s, years in sorted(self.reservations.items()) for y, v in sorted(years.items())
                ],
                "supply_orders": [
                    {"source_id": s, "year": y, "ordered_t": v}
                    for s, years in sorted(self.orders.items()) for y, v in sorted(years.items()) if v is not None
                ],
                "investments": [
                    {"investment_id": k, "decision_year": v} for k, v in self.investments.items() if v is not None
                ],
                "inventory_policy": self.inventory_policy,
            },
            "assumptions": self.assumptions,
        }

    @staticmethod
    def from_envelope(raw: dict) -> "Plan":
        d = raw.get("decisions", {})
        plan = Plan(plan_id=raw.get("plan_id", "plan-1"), name=raw.get("name", raw.get("plan_id", "План")))
        for row in d.get("capacity_reservations", []):
            plan.reservations.setdefault(row["source_id"], {})[int(row["year"])] = float(row["reserved_t_per_year"])
        for row in d.get("supply_orders", []):
            if row.get("ordered_t") is not None:
                plan.orders.setdefault(row["source_id"], {})[int(row["year"])] = float(row["ordered_t"])
        plan.investments = {"ZBO": None, "LUNAR_ISRU": None, "EARTH_NEW_OPTION": None, "EARTH_NEW_EXERCISE": None}
        for row in d.get("investments", []):
            plan.investments[row["investment_id"]] = None if row.get("decision_year") is None else int(row["decision_year"])
        plan.inventory_policy = d.get("inventory_policy") or plan.inventory_policy
        plan.assumptions = {**DEFAULT_ASSUMPTIONS, **(raw.get("assumptions") or {})}
        return plan

    def save(self, path: Path | str, scenario_id: str = "BASE") -> None:
        target = resolve_for_write(path, "план")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_envelope(scenario_id), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path | str) -> "Plan":
        return Plan.from_envelope(json.loads(resolve_for_read(path).read_text(encoding="utf-8")))
