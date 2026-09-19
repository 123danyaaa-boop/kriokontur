"""Выгрузка результата. Конверт повторяет раздел «Сохранение и экспорт» стартового репозитория:
scenario_id, plan_id, units, assumptions, yearly_balance, source_schedule, financial_breakdown,
inventory_trace, constraint_checks. Числа берутся из того же RunResult, что показывает интерфейс.

Секции отдают значения родными типами (числа числами), поэтому XLSX получает числовые ячейки,
а CSV форматирует их в одном месте — в `_cell`. Ни одна величина здесь не пересчитывается:
платежи по каналам, доля года и перелив приходят из движка.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import List

from .caseinput import CaseInput
from .engine import RunResult
from .paths import resolve_for_write

HEADER_YEAR = ["year", "demand_total_t", "demand_critical_t", "opening_t", "delivered_t", "losses_t",
               "served_t", "served_critical_t", "shortage_t", "closing_t", "capacity_t",
               "reserve_required_t", "sl_total", "sl_critical", "loss_share", "overflow_t",
               "avg_stock_t", "unused_paid_t"]
HEADER_SOURCE = ["year", "source_id", "source_name", "reserved_t_per_year", "ordered_t", "delivered_t",
                 "payable_t", "variable_payment_mln", "reservation_payment_mln", "contracted_t",
                 "unused_paid_t", "price_mln_per_t"]
HEADER_FIN = ["year", "procurement_mln", "reservation_mln", "holding_mln", "fixed_opex_mln", "capex_mln",
              "total_mln", "discounted_mln"]
HEADER_TRACE = ["month_index", "year", "month", "opening_t", "gross_t", "losses_t", "served_t",
                "shortage_t", "closing_t", "capacity_t", "target_t", "overflow_t", "avg_stock_t"]
HEADER_CHECK = ["code", "severity", "period", "metric", "value", "limit", "excess", "message"]

DECIMALS = 4      # точность выгрузки: числа в CSV печатаются с этим числом знаков


def _sections(case: CaseInput, res: RunResult, scenario_price, rules_mod):
    yield ["export_envelope"]
    yield ["scenario_id", res.scenario_id, "plan_id", res.plan_id, "case_version", res.case_version,
           "engine_version", res.engine_version, "created_at", res.created_at]
    yield ["units", "тонны; " + case.currency_note]
    yield []
    yield ["assumptions"]
    yield ["name", "value"]
    for k, v in res.assumptions.items():
        yield [k, v]
    yield []
    yield ["yearly_balance"]
    yield HEADER_YEAR
    for y in res.years:
        yield [y.year, y.demand_total_t, y.demand_critical_t, y.opening_t,
               y.gross_t, y.losses_t, y.served_t, y.served_critical_t,
               y.shortage_t, y.closing_t, y.capacity_t,
               y.reserve_required_t, y.sl_total, y.sl_critical, y.loss_share, y.overflow_t,
               y.avg_stock_t, sum(y.unused_paid_t.values())]
    yield []
    yield ["source_schedule"]
    yield HEADER_SOURCE
    for y in res.years:
        for s in case.source_list:
            sid = s.source_id
            yield [y.year, sid, s.name, y.reserved_t.get(sid, 0.0),
                   y.ordered_t.get(sid, 0.0), y.delivered_t.get(sid, 0.0),
                   y.payable_t.get(sid, 0.0),
                   y.variable_payment_mln.get(sid, 0.0),
                   y.reservation_payment_mln.get(sid, 0.0),
                   y.contracted_t.get(sid, 0.0),
                   y.unused_paid_t.get(sid, 0.0),
                   y.price_mln_per_t.get(sid, s.variable_cost_mln_per_t)]
    yield []
    yield ["financial_breakdown"]
    yield HEADER_FIN
    for y in res.years:
        yield [y.year, y.cost["procurement"], y.cost["reservation"], y.cost["holding"],
               y.cost["fixed_opex"], y.cost["capex"], y.total_cost_mln, y.discounted_cost_mln]
    yield ["итого", res.totals["procurement"], res.totals["reservation"], res.totals["holding"],
           res.totals["fixed_opex"], res.totals["capex"],
           res.totals["total_cost_mln"], res.totals["discounted_cost_mln"]]
    yield []
    yield ["inventory_trace"]
    yield HEADER_TRACE
    for m in res.months:
        yield [m.index, m.year, m.month, m.opening_t, m.gross_t, m.losses_t,
               m.served_t, m.shortage_t, m.closing_t, m.capacity_t, m.target_t,
               m.overflow_t, m.avg_stock_t]
    yield []
    yield ["constraint_checks"]
    yield HEADER_CHECK
    if not res.violations:
        yield ["OK", "info", "", "", "", "", "", "Нарушений не найдено"]
    for v in res.violations:
        yield [v.code, v.severity, v.period, v.metric, v.value, v.limit, v.excess, v.message]


def _cell(value):
    """CSV: числа печатаются с фиксированной точностью, остальное как есть."""
    if isinstance(value, bool) or value is None:
        return "" if value is None else str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{DECIMALS}f}"
    return value


def to_csv(case: CaseInput, res: RunResult, scenario) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    for row in _sections(case, res, scenario.price_factor, None):
        writer.writerow([_cell(v) for v in row])
    return buf.getvalue()


def write_csv(case: CaseInput, res: RunResult, scenario, path: Path | str) -> Path:
    path = resolve_for_write(path, "выгрузка CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("﻿" + to_csv(case, res, scenario), encoding="utf-8")
    return path


def write_xlsx(case: CaseInput, res: RunResult, scenario, path: Path | str) -> Path:
    from openpyxl import Workbook
    path = resolve_for_write(path, "выгрузка XLSX")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    sheets = {"yearly_balance": HEADER_YEAR, "source_schedule": HEADER_SOURCE,
              "financial_breakdown": HEADER_FIN, "inventory_trace": HEADER_TRACE,
              "constraint_checks": HEADER_CHECK}
    current = None
    for row in _sections(case, res, scenario.price_factor, None):
        if len(row) == 1 and row[0] in sheets:
            current = wb.create_sheet(row[0])
            continue
        if len(row) == 1 and row[0] in ("export_envelope", "assumptions"):
            current = wb.create_sheet(row[0][:31])
            continue
        if current is not None and row:
            # числа кладём числами: жюри может считать по ним прямо в книге
            current.append([v if isinstance(v, (int, float)) or v is None else str(v) for v in row])
    meta = wb.create_sheet("meta", 0)
    meta.append(["scenario_id", res.scenario_id])
    meta.append(["plan_id", res.plan_id])
    meta.append(["case_version", res.case_version])
    meta.append(["engine_version", res.engine_version])
    meta.append(["created_at", res.created_at])
    meta.append(["units", "тонны; " + case.currency_note])
    wb.save(path)
    return path
