"""Автоплан: эвристическая стартовая точка, а не оптимизатор.

Логика простая и проверяемая: на каждый год считаем валовую потребность
(спрос плюс прирост резерва, делённые на 1 - loss_rate) и закрываем её каналами
по возрастанию переменной цены в пределах мощности и доступности. Остаток и
запас гибкости отдаём Earth-Flex, аварийный канал резервируем под размер резерва.
Команда правит результат руками, поэтому эвристика намеренно прозрачная.
"""
from __future__ import annotations

from typing import Dict, Optional

from . import rules
from .caseinput import CaseInput
from .engine import MONTHS, availability, months_available, storage_mode
from .plan import Plan
from .scenarios import Scenario

FLEX_BUFFER_SHARE = 0.10  # TEAM_ASSUMPTION: запас гибкой мощности к годовому спросу


def auto_plan(case: CaseInput, scenario: Scenario, investments: Dict[str, Optional[int]],
              plan_id: str = "auto", name: str = "Автоплан") -> Plan:
    plan = Plan(plan_id=plan_id, name=name)
    plan.investments = dict(investments)
    demand = {y: scenario.demand_total(case, y) for y in case.years}
    plan.inventory_policy = {
        "mode": "physical",
        "opening_stock_t": round(rules.reserve_days_to_tonnes(demand[case.years[0]]) + 0.05, 1),
        "target_days": 45,
    }
    avail = availability(case, plan)
    merit = sorted([s for s in case.source_list if s.source_id != "E"], key=lambda s: s.variable_cost_mln_per_t)

    for i, year in enumerate(case.years):
        store = storage_mode(case, plan, year)
        growth = (rules.reserve_days_to_tonnes(demand[case.years[i + 1]]) - rules.reserve_days_to_tonnes(demand[year])
                  if i + 1 < len(case.years) else 0.0)
        gross_need = (demand[year] + growth) / (1.0 - store.loss_rate_on_throughput)
        left = gross_need
        for s in merit:
            on = months_available(avail.get(s.source_id), i)
            if on <= 0:
                plan.reservations.setdefault(s.source_id, {})[year] = 0.0
                continue
            share = scenario.delivery_factor(s.name, s.source_id, year)
            year_cap = s.capacity_t_per_year * on / MONTHS * share
            take = max(0.0, min(left, year_cap))
            reserved = take / (on / MONTHS) / (share or 1.0)
            plan.reservations.setdefault(s.source_id, {})[year] = round(min(reserved, s.capacity_t_per_year), 1)
            left -= take
        flex = max(left, 0.0) + FLEX_BUFFER_SHARE * demand[year]
        plan.reservations.setdefault("B", {})[year] = round(min(case.sources["B"].capacity_t_per_year,
                                                               plan.reservations["B"].get(year, 0.0) + flex), 1)
        plan.reservations.setdefault("E", {})[year] = round(min(case.sources["E"].capacity_t_per_year,
                                                               rules.reserve_days_to_tonnes(demand[year])), 1)
    return plan
