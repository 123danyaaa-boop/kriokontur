"""Выгрузка результата. Конверт повторяет раздел «Сохранение и экспорт» стартового репозитория:
scenario_id, plan_id, units, assumptions, yearly_balance, source_schedule, financial_breakdown,
inventory_trace, constraint_checks. Числа берутся из того же RunResult, что показывает интерфейс.
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
               "reserve_required_t", "sl_total", "sl_critical", "loss_share"]
HEADER_SOURCE = ["year", "source_id", "source_name", "reserved_t_per_year", "ordered_t", "delivered_t",
                 "payable_t", "variable_payment_mln", "reservation_payment_mln"]
HEADER_FIN = ["year", "procurement_mln", "reservation_mln", "holding_mln", "fixed_opex_mln", "capex_mln",
              "total_mln", "discounted_mln"]
HEADER_TRACE = ["month_index", "year", "month", "opening_t", "gross_t", "losses_t", "served_t",
                "shortage_t", "closing_t", "capacity_t", "target_t"]
HEADER_CHECK = ["code", "severity", "period", "metric", "value", "limit", "excess", "message"]


def _sections(case: CaseInput, res: RunResult, scenario_price, rules_mod):
    from . import rules as R
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
        yield [y.year, f"{y.demand_total_t:.2f}", f"{y.demand_critical_t:.2f}", f"{y.opening_t:.2f}",
               f"{y.gross_t:.2f}", f"{y.losses_t:.2f}", f"{y.served_t:.2f}", f"{y.served_critical_t:.2f}",
               f"{y.shortage_t:.2f}", f"{y.closing_t:.2f}", f"{y.capacity_t:.0f}",
               f"{y.reserve_required_t:.2f}", f"{y.sl_total:.4f}", f"{y.sl_critical:.4f}", f"{y.loss_share:.4f}"]
    yield []
    yield ["source_schedule"]
    yield HEADER_SOURCE
    for y in res.years:
        for s in case.source_list:
            price = s.variable_cost_mln_per_t * scenario_price(s.name, s.source_id, y.year)
            yield [y.year, s.source_id, s.name, f"{y.reserved_t.get(s.source_id, 0):.2f}",
                   f"{y.ordered_t.get(s.source_id, 0):.2f}", f"{y.delivered_t.get(s.source_id, 0):.2f}",
                   f"{y.payable_t.get(s.source_id, 0):.2f}",
                   f"{price * y.payable_t.get(s.source_id, 0):.2f}",
                   f"{s.reservation_rate * y.reserved_t.get(s.source_id, 0):.2f}"]
    yield []
    yield ["financial_breakdown"]
    yield HEADER_FIN
    for y in res.years:
        yield [y.year, f"{y.cost['procurement']:.2f}", f"{y.cost['reservation']:.2f}", f"{y.cost['holding']:.2f}",
               f"{y.cost['fixed_opex']:.2f}", f"{y.cost['capex']:.2f}", f"{y.total_cost_mln:.2f}",
               f"{y.discounted_cost_mln:.2f}"]
    yield ["итого", f"{res.totals['procurement']:.2f}", f"{res.totals['reservation']:.2f}",
           f"{res.totals['holding']:.2f}", f"{res.totals['fixed_opex']:.2f}", f"{res.totals['capex']:.2f}",
           f"{res.totals['total_cost_mln']:.2f}", f"{res.totals['discounted_cost_mln']:.2f}"]
    yield []
    yield ["inventory_trace"]
    yield HEADER_TRACE
    for m in res.months:
        yield [m.index, m.year, m.month, f"{m.opening_t:.3f}", f"{m.gross_t:.3f}", f"{m.losses_t:.3f}",
               f"{m.served_t:.3f}", f"{m.shortage_t:.3f}", f"{m.closing_t:.3f}", f"{m.capacity_t:.0f}",
               f"{m.target_t:.3f}"]
    yield []
    yield ["constraint_checks"]
    yield HEADER_CHECK
    if not res.violations:
        yield ["OK", "info", "", "", "", "", "", "Нарушений не найдено"]
    for v in res.violations:
        yield [v.code, v.severity, v.period, v.metric, v.value, v.limit, v.excess, v.message]


def to_csv(case: CaseInput, res: RunResult, scenario) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    for row in _sections(case, res, scenario.price_factor, None):
        writer.writerow(row)
    return buf.getvalue()


def write_csv(case: CaseInput, res: RunResult, scenario, path: Path | str) -> Path:
    path = resolve_for_write(path, "выгрузка CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\ufeff" + to_csv(case, res, scenario), encoding="utf-8")
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
            current.append(list(row))
    meta = wb.create_sheet("meta", 0)
    meta.append(["scenario_id", res.scenario_id])
    meta.append(["plan_id", res.plan_id])
    meta.append(["case_version", res.case_version])
    meta.append(["engine_version", res.engine_version])
    meta.append(["created_at", res.created_at])
    meta.append(["units", "тонны; " + case.currency_note])
    wb.save(path)
    return path
