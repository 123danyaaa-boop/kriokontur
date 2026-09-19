"""Сценарии: контрольные от организатора и исследовательские от команды.

Три статуса, которые нельзя смешивать:
    CASE_INPUT        BASE и MANDATORY_STRESS, меняются только организатором;
    TEAM_SENSITIVITY  проверки чувствительности на данных организатора (низкий и высокий спрос);
    TEAM_RESEARCH     наши риски, геополитика и комбинации, всегда с явным правилом сочетания.

Сценарий не правит CASE_INPUT, он накладывает на него множители:
    demand_multiplier / critical_demand_multiplier  спрос года
    variable_price_multiplier                       переменная цена канала по годам
    actual_delivery_share                           фактическая доля поставки канала
    capacity_multiplier                             доступная мощность канала (0 = канал стоит)
    commissioning_delay_months                      сдвиг ввода канала
    loss_ceiling                                    потолок отношения потерь к обороту

base_scenario подмешивает другой сценарий: так строится комбинированный тест
«обязательный стресс плюс высокий спрос», который кейс требует объявлять явно.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .caseinput import CaseInput

from .paths import SCENARIOS as SCEN_DIR  # единая точка правды по путям, см. paths.py
CONTROL_IDS = ("BASE", "MANDATORY_STRESS")


@dataclass
class Scenario:
    scenario_id: str
    label: str
    status: str
    demand_series: str = "base"
    base_scenario: Optional[str] = None
    demand_multiplier: Dict = field(default_factory=dict)
    critical_multiplier: Dict = field(default_factory=dict)
    price_multiplier: Dict[str, Dict] = field(default_factory=dict)
    delivery_share: Dict[str, Dict] = field(default_factory=dict)
    capacity_multiplier: Dict[str, Dict] = field(default_factory=dict)
    commissioning_delay_months: Dict[str, float] = field(default_factory=dict)
    loss_ceiling_from_year: Optional[int] = None
    loss_ceiling_value: Optional[float] = None
    combination_rule: str = ""
    notes: List[str] = field(default_factory=list)

    def _series(self, case: CaseInput):
        return {"base": case.demand_total, "low": case.demand_low, "high": case.demand_high}[self.demand_series]

    def demand_total(self, case: CaseInput, year: int) -> float:
        mult = self.demand_multiplier.get(year, self.demand_multiplier.get("default", 1.0))
        return self._series(case)[year] * mult

    def demand_critical(self, case: CaseInput, year: int) -> float:
        share = case.demand_critical[year] / case.demand_total[year]
        mult = self.critical_multiplier.get(year, self.critical_multiplier.get("default", 1.0))
        return self._series(case)[year] * share * mult

    def _lookup(self, table: Dict[str, Dict], source_name: str, source_id: str, year: int, default: float) -> float:
        for key in (source_name, source_id, "default"):
            sub = table.get(key)
            if isinstance(sub, dict):
                return float(sub.get(year, sub.get("default", default)))
        return default

    def price_factor(self, source_name: str, source_id: str, year: int) -> float:
        return self._lookup(self.price_multiplier, source_name, source_id, year, 1.0)

    def delivery_factor(self, source_name: str, source_id: str, year: int) -> float:
        return self._lookup(self.delivery_share, source_name, source_id, year, 1.0)

    def capacity_factor(self, source_name: str, source_id: str, year: int) -> float:
        return self._lookup(self.capacity_multiplier, source_name, source_id, year, 1.0)

    def commissioning_delay(self, source_name: str, source_id: str) -> float:
        for key in (source_name, source_id):
            if key in self.commissioning_delay_months:
                return float(self.commissioning_delay_months[key])
        return 0.0

    def loss_ceiling(self, year: int) -> Optional[float]:
        if self.loss_ceiling_from_year is None:
            return None
        return self.loss_ceiling_value if year >= self.loss_ceiling_from_year else None

    def derive(self, scenario_id: str, label: str, **overrides) -> "Scenario":
        """Копия сценария с изменениями: используется чувствительностью, обратным стрессом и Монте-Карло."""
        return replace(self, scenario_id=scenario_id, label=label, status="TEAM_RESEARCH", **overrides)

    @property
    def is_control(self) -> bool:
        return self.scenario_id in CONTROL_IDS


def _tables(raw) -> Dict:
    if not isinstance(raw, dict):
        return {}
    out: Dict = {}
    for key, value in raw.items():
        k = int(key) if str(key).isdigit() else key
        out[k] = ({int(y) if str(y).isdigit() else y: float(v) for y, v in value.items()}
                  if isinstance(value, dict) else float(value))
    return out


def _nested_only(table: Dict) -> Dict[str, Dict]:
    nested = {k: v for k, v in table.items() if isinstance(v, dict)}
    if not nested and "default" in table:
        return {"default": {"default": table["default"]}}
    return nested


def load_scenario(path: Path | str) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    ceiling = raw.get("loss_ceiling") or {}
    return Scenario(
        scenario_id=raw["scenario_id"],
        label=raw.get("label_ru", raw["scenario_id"]),
        status=raw.get("status", "CASE_INPUT"),
        demand_series=raw.get("demand_series", "base"),
        base_scenario=raw.get("base_scenario"),
        demand_multiplier=_tables(raw.get("demand_multiplier") or {}),
        critical_multiplier=_tables(raw.get("critical_demand_multiplier") or raw.get("demand_multiplier") or {}),
        price_multiplier=_nested_only(_tables(raw.get("variable_price_multiplier") or {})),
        delivery_share=_nested_only(_tables(raw.get("actual_delivery_share") or {})),
        capacity_multiplier=_nested_only(_tables(raw.get("capacity_multiplier") or {})),
        commissioning_delay_months={k: float(v) for k, v in (raw.get("commissioning_delay_months") or {}).items()},
        loss_ceiling_from_year=ceiling.get("from_year") if ceiling.get("enabled") else None,
        loss_ceiling_value=ceiling.get("max_losses_divided_by_throughput") if ceiling.get("enabled") else None,
        combination_rule=raw.get("combination_rule", ""),
        notes=raw.get("notes", []),
    )


def _merge(parent: Scenario, child: Scenario) -> Scenario:
    """Ребёнок перекрывает родителя. Один и тот же эффект не начисляется дважды."""
    def nested(a: Dict, b: Dict) -> Dict:
        out = {k: dict(v) for k, v in a.items()}
        for key, table in b.items():
            out.setdefault(key, {}).update(table)
        return out

    return Scenario(
        scenario_id=child.scenario_id, label=child.label, status=child.status,
        demand_series=child.demand_series if child.demand_series != "base" else parent.demand_series,
        base_scenario=child.base_scenario,
        demand_multiplier={**parent.demand_multiplier, **child.demand_multiplier},
        critical_multiplier={**parent.critical_multiplier, **child.critical_multiplier},
        price_multiplier=nested(parent.price_multiplier, child.price_multiplier),
        delivery_share=nested(parent.delivery_share, child.delivery_share),
        capacity_multiplier=nested(parent.capacity_multiplier, child.capacity_multiplier),
        commissioning_delay_months={**parent.commissioning_delay_months, **child.commissioning_delay_months},
        loss_ceiling_from_year=child.loss_ceiling_from_year if child.loss_ceiling_from_year is not None else parent.loss_ceiling_from_year,
        loss_ceiling_value=child.loss_ceiling_value if child.loss_ceiling_value is not None else parent.loss_ceiling_value,
        combination_rule=child.combination_rule or parent.combination_rule,
        notes=parent.notes + child.notes,
    )


def load_all(scen_dir: Path | str = SCEN_DIR) -> Dict[str, Scenario]:
    raw: Dict[str, Scenario] = {}
    for path in sorted(Path(scen_dir).glob("*.yaml")):
        sc = load_scenario(path)
        raw[sc.scenario_id] = sc
    resolved: Dict[str, Scenario] = {}

    def resolve(sid: str, seen: tuple = ()) -> Scenario:
        if sid in resolved:
            return resolved[sid]
        if sid in seen:
            raise ValueError("циклическое наследование сценариев: " + " -> ".join(seen + (sid,)))
        sc = raw[sid]
        if sc.base_scenario:
            sc = _merge(resolve(sc.base_scenario, seen + (sid,)), sc)
        resolved[sid] = sc
        return sc

    for sid in list(raw):
        resolve(sid)
    return resolved
