"""Контрактно-финансовая архитектура: карточки договоров и расчёт обязательств.

Кейс (с. 8 и 13) требует показать контрагента, объём, сроки и lead time, резервирование,
take-or-pay, оплату, ответственность и правила пересмотра, а обязательства связать
с доступностью поставок и расходами.

Разделение источников правды:
    числовые условия канала   -> data/supply_sources.csv (CASE_INPUT, сюда не копируются);
    коммерческая рамка        -> configs/contracts.yaml (TEAM_ASSUMPTION);
    факт исполнения по годам  -> RunResult движка (ничего не пересчитываем).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .caseinput import CaseInput
from .engine import RunResult
from .paths import CONFIGS

CONTRACTS_PATH = CONFIGS / "contracts.yaml"

# поля, которые запрещено задавать в contracts.yaml: они есть в CASE_INPUT
CASE_OWNED_FIELDS = {"capacity_t_per_year", "variable_cost_mln_per_t", "take_or_pay_share",
                     "reservation_rate", "lead_time_min_value", "lead_time_max_value",
                     "available_from_year", "price", "capacity", "take_or_pay"}


@dataclass
class Contract:
    contract_id: str
    source_id: str
    counterparty: str
    subject: str = ""
    term: str = ""
    payment_terms: str = ""
    liability: str = ""
    revision_rule: str = ""
    risk_allocation: str = ""
    flexibility: str = ""
    emergency_batch_policy: str = ""
    status: str = "TEAM_ASSUMPTION"
    extras: Dict = field(default_factory=dict)


def load_contracts(path: Path | str = CONTRACTS_PATH) -> List[Contract]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    known = set(Contract.__dataclass_fields__) - {"extras"}
    out = []
    for row in raw:
        bad = CASE_OWNED_FIELDS & set(row)
        if bad:
            raise ValueError(f"договор {row.get('contract_id')}: поля {sorted(bad)} принадлежат "
                             f"CASE_INPUT и читаются из data/supply_sources.csv, дублировать их нельзя")
        out.append(Contract(**{k: v for k, v in row.items() if k in known},
                            extras={k: v for k, v in row.items() if k not in known}))
    return out


def contract_card(case: CaseInput, contract: Contract) -> Dict:
    """Карточка договора: условия кейса и коммерческая рамка команды в одном месте."""
    s = case.sources[contract.source_id]
    return {
        "contract_id": contract.contract_id,
        "source_id": s.source_id,
        "channel": s.name,
        "counterparty": contract.counterparty,
        "subject": contract.subject,
        "term": contract.term,
        # условия из CASE_INPUT
        "capacity_t_per_year": s.capacity_t_per_year,
        "price_mln_per_t": s.variable_cost_mln_per_t,
        "reservation_rate_mln_per_t_year": s.reservation_rate,
        "take_or_pay_share": s.take_or_pay_share,
        "lead_time": f"{s.lead_time_min:.0f}–{s.lead_time_max:.0f} {s.lead_time_unit}",
        "lead_time_days": s.lead_time_days("max"),
        "available_from_year": s.available_from_year,
        "reliability_profile": s.reliability_profile,
        # коммерческая рамка команды
        "payment_terms": contract.payment_terms,
        "liability": contract.liability,
        "revision_rule": contract.revision_rule,
        "risk_allocation": contract.risk_allocation,
        "flexibility": contract.flexibility,
        "emergency_batch_policy": contract.emergency_batch_policy,
        "status": contract.status,
        "case_fields_source": "data/supply_sources.csv",
    }


def obligations(case: CaseInput, res: RunResult, contracts: Optional[List[Contract]] = None) -> List[Dict]:
    """Договорные обязательства по годам: законтрактовано, отобрано, оплачено, не отобрано.

    Все величины берутся из прогона: движок уже посчитал долю года доступности, минимум
    take-or-pay и платежи. Здесь только раскладка по договорам и итоги.
    """
    contracts = contracts if contracts is not None else load_contracts()
    by_source = {c.source_id: c for c in contracts}
    rows: List[Dict] = []
    for y in res.years:
        for sid, contract in sorted(by_source.items()):
            if sid not in case.sources:
                continue
            reserved = y.reserved_t.get(sid, 0.0)
            contracted = y.contracted_t.get(sid, 0.0)
            offtake = y.ordered_t.get(sid, 0.0)
            payable = y.payable_t.get(sid, 0.0)
            unused = y.unused_paid_t.get(sid, 0.0)
            var_pay = y.variable_payment_mln.get(sid, 0.0)
            res_pay = y.reservation_payment_mln.get(sid, 0.0)
            rows.append({
                "contract_id": contract.contract_id,
                "source_id": sid,
                "counterparty": contract.counterparty,
                "year": y.year,
                "reserved_t_per_year": reserved,
                "contracted_t": contracted,
                "offtake_t": offtake,
                "delivered_t": y.delivered_t.get(sid, 0.0),
                "payable_t": payable,
                "unused_paid_t": unused,
                "take_or_pay_minimum_t": case.sources[sid].take_or_pay_share * contracted,
                "variable_payment_mln": var_pay,
                "reservation_payment_mln": res_pay,
                "total_payment_mln": var_pay + res_pay,
                "utilisation": offtake / contracted if contracted > 1e-9 else None,
            })
    return rows


def obligations_totals(rows: List[Dict]) -> Dict:
    """Итоги по договорам: сверяются с финансовым блоком прогона."""
    out: Dict[str, Dict] = {}
    for r in rows:
        acc = out.setdefault(r["contract_id"], {
            "contract_id": r["contract_id"], "source_id": r["source_id"],
            "counterparty": r["counterparty"], "contracted_t": 0.0, "offtake_t": 0.0,
            "payable_t": 0.0, "unused_paid_t": 0.0, "variable_payment_mln": 0.0,
            "reservation_payment_mln": 0.0, "total_payment_mln": 0.0})
        for key in ("contracted_t", "offtake_t", "payable_t", "unused_paid_t",
                    "variable_payment_mln", "reservation_payment_mln", "total_payment_mln"):
            acc[key] += r[key]
    for acc in out.values():
        acc["utilisation"] = (acc["offtake_t"] / acc["contracted_t"]) if acc["contracted_t"] > 1e-9 else None
    return out


def emergency_reserve_check(case: CaseInput, plan, year: int, demand_t: float, opening_t: float) -> Dict:
    """Договорный эквивалент 45-дневного резерва: объём партии и покрытие ожидания.

    Срок берётся в сутках из данных кейса (6 недель = 42 дня), а не округляется до целых
    месячных шагов: округление давало 61 день ожидания и делало вариант бессмысленным
    по арифметической причине, а не по существу.
    """
    from . import rules

    source = case.sources["E"]
    lead_days = source.lead_time_days("max")
    reserved = plan.reserved("E", year)
    lead_steps = int(plan.assume("emergency_lead_steps"))
    explicit = plan.assume("emergency_batch_t")
    batch_t = min(rules.emergency_batch(reserved, lead_steps, 12, explicit), reserved,
                  source.capacity_t_per_year)
    max_batch_t = min(rules.emergency_batch(source.capacity_t_per_year, lead_steps, 12, explicit),
                      source.capacity_t_per_year)
    required_t = rules.reserve_days_to_tonnes(demand_t)
    waiting_cover_t = rules.reserve_days_to_tonnes(demand_t, lead_days)
    return {
        "year": year,
        "lead_time_days": lead_days,
        "reserved_t_per_year": reserved,
        "batch_policy": "договорный размер партии" if explicit is not None
                        else "месячная доля договора × шаги поставки",
        "callable_batch_t": batch_t,
        "max_batch_at_full_capacity_t": max_batch_t,
        "required_reserve_t": required_t,
        "waiting_cover_needed_t": waiting_cover_t,
        "opening_t": opening_t,
        "volume_ok": batch_t >= required_t - 1e-6,
        "waiting_ok": opening_t >= waiting_cover_t - 1e-6,
        "feasible_at_full_capacity": max_batch_t >= required_t - 1e-6,
        "equivalent": batch_t >= required_t - 1e-6 and opening_t >= waiting_cover_t - 1e-6,
    }
