"""SQLite-хранилище: CASE_INPUT, планы, прогоны, нарушения, выгрузки.

База нужна, чтобы план можно было сохранить, открыть заново и сравнить прогоны,
а жюри видело те же числа, что и в интерфейсе. Схема в db/schema.sql.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .caseinput import CaseInput
from .engine import RunResult
from .plan import Plan

from .paths import DB_FILE as DEFAULT_DB, SCHEMA_SQL as SCHEMA, ensure_dirs


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    ensure_dirs()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_case(conn: sqlite3.Connection, case: CaseInput, source: str = "data/*.csv (копия test_oil)") -> None:
    conn.execute("INSERT OR REPLACE INTO case_version(case_version, loaded_at, source, note) VALUES (?,?,?,?)",
                 (case.version, now(), source, "CASE_INPUT, правка кодом запрещена"))
    for y in case.years:
        conn.execute("INSERT OR REPLACE INTO demand VALUES (?,?,?,?,?,?)",
                     (case.version, y, case.demand_total[y], case.demand_critical[y],
                      case.demand_low[y], case.demand_high[y]))
    for s in case.source_list:
        conn.execute("INSERT OR REPLACE INTO supply_source VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (case.version, s.source_id, s.name, s.capacity_t_per_year, s.variable_cost_mln_per_t,
                      s.reservation_rate, s.take_or_pay_share, s.lead_time_min, s.lead_time_max,
                      s.lead_time_unit, s.reliability_profile, s.available_from_year))
    for st in case.storage.values():
        conn.execute("INSERT OR REPLACE INTO storage_option VALUES (?,?,?,?,?,?,?,?,?)",
                     (case.version, st.storage_id, st.name, st.capacity_t, st.loss_rate_on_throughput,
                      st.holding_cost_mln_per_t_year, st.capex_mln, st.fixed_opex_mln_per_year,
                      st.available_from_year))
    for inv in case.investments.values():
        conn.execute("INSERT OR REPLACE INTO investment_option VALUES (?,?,?,?,?,?,?,?)",
                     (case.version, inv.investment_id, inv.name, inv.option_fee_mln, inv.exercise_cost_mln,
                      inv.total_capex_mln, inv.commissioning_rule, inv.fixed_opex_mln_per_year))
    for c in case.constraints.values():
        conn.execute("INSERT OR REPLACE INTO constraint_rule VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (case.version, c.constraint_id, c.metric, c.operator, c.value, c.unit, c.period,
                      c.scenario, c.severity, c.description))
    conn.commit()


def sync_scenarios(conn: sqlite3.Connection, scenarios: dict, scen_dir: Path) -> None:
    for sid, sc in scenarios.items():
        path = next((p for p in Path(scen_dir).glob("*.yaml") if sid.lower() in p.stem.lower()
                     or sid == "BASE" and p.stem == "base"), None)
        conn.execute("INSERT OR REPLACE INTO scenario VALUES (?,?,?,?)",
                     (sid, sc.label, sc.status, path.read_text(encoding="utf-8") if path else ""))
    conn.commit()


def save_plan(conn: sqlite3.Connection, plan: Plan, scenario_id: str = "BASE",
              author: str = "команда") -> str:
    """Сохраняет план вместе со сценарием, под которым он построен.

    Сценарий хранится прямо в переносимом конверте: иначе открытый план молча считается
    в BASE, даже если он собирался под обязательный стресс.
    """
    envelope = json.dumps(plan.to_envelope(scenario_id), ensure_ascii=False)
    conn.execute("""INSERT INTO plan(plan_id, name, author, created_at, updated_at, envelope)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(plan_id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at,
                    envelope=excluded.envelope""",
                 (plan.plan_id, plan.name, author, now(), now(), envelope))
    conn.execute("DELETE FROM plan_reservation WHERE plan_id=?", (plan.plan_id,))
    conn.execute("DELETE FROM plan_order WHERE plan_id=?", (plan.plan_id,))
    conn.execute("DELETE FROM plan_investment WHERE plan_id=?", (plan.plan_id,))
    conn.execute("DELETE FROM plan_assumption WHERE plan_id=?", (plan.plan_id,))
    for sid, years in plan.reservations.items():
        for year, value in years.items():
            conn.execute("INSERT INTO plan_reservation VALUES (?,?,?,?)", (plan.plan_id, sid, year, value))
    for sid, years in plan.orders.items():
        for year, value in years.items():
            conn.execute("INSERT INTO plan_order VALUES (?,?,?,?)", (plan.plan_id, sid, year, value))
    for inv, year in plan.investments.items():
        conn.execute("INSERT INTO plan_investment VALUES (?,?,?)", (plan.plan_id, inv, year))
    for name, value in plan.assumptions.items():
        conn.execute("INSERT INTO plan_assumption VALUES (?,?,?,?,?,?)",
                     (plan.plan_id, name, json.dumps(value, ensure_ascii=False), "", "TEAM_ASSUMPTION", "модель"))
    conn.commit()
    return plan.plan_id


def load_plan(conn: sqlite3.Connection, plan_id: str) -> Optional[Plan]:
    row = conn.execute("SELECT envelope FROM plan WHERE plan_id=?", (plan_id,)).fetchone()
    return Plan.from_envelope(json.loads(row["envelope"])) if row else None


def load_envelope(conn: sqlite3.Connection, plan_id: str) -> Optional[dict]:
    """Конверт как он сохранён: с именем и сценарием, под которым план строился."""
    row = conn.execute("SELECT envelope FROM plan WHERE plan_id=?", (plan_id,)).fetchone()
    return json.loads(row["envelope"]) if row else None


def list_plans(conn: sqlite3.Connection) -> List[dict]:
    rows = []
    for r in conn.execute("SELECT plan_id, name, author, updated_at, envelope FROM plan "
                          "ORDER BY updated_at DESC"):
        item = {k: r[k] for k in ("plan_id", "name", "author", "updated_at")}
        try:
            item["scenario_id"] = json.loads(r["envelope"]).get("scenario_id", "BASE")
        except (ValueError, TypeError):
            item["scenario_id"] = "BASE"
        rows.append(item)
    return rows


def save_run(conn: sqlite3.Connection, res: RunResult, case: CaseInput) -> str:
    run_id = f"{res.plan_id}:{res.scenario_id}:{uuid.uuid4().hex[:8]}"
    conn.execute("""INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (run_id, res.plan_id, res.scenario_id, res.case_version, res.engine_version,
                  float(res.assumptions.get("discount_rate", 0.0)), None, res.created_at, int(res.feasible),
                  res.totals["total_cost_mln"], res.totals["discounted_cost_mln"], res.totals["sl_total"],
                  res.totals["sl_critical"], res.totals["shortage_t"]))
    for y in res.years:
        conn.execute("INSERT INTO run_year VALUES (" + ",".join(["?"] * 25) + ")",
                     (run_id, y.year, y.demand_total_t, y.demand_critical_t, y.opening_t, y.gross_t, y.losses_t,
                      y.served_t, y.served_critical_t, y.shortage_t, y.closing_t, y.avg_stock_t, y.max_stock_t,
                      y.capacity_t, y.reserve_required_t, y.sl_total, y.sl_critical, y.loss_share,
                      y.cost["procurement"], y.cost["reservation"], y.cost["holding"], y.cost["fixed_opex"],
                      y.cost["capex"], y.total_cost_mln, y.discounted_cost_mln))
        for s in case.source_list:
            # платежи берём из прогона, а не пересчитываем: доля года и цена сценария уже учтены там
            conn.execute("INSERT INTO run_source_year VALUES (?,?,?,?,?,?,?,?,?)",
                         (run_id, y.year, s.source_id, y.reserved_t.get(s.source_id, 0.0),
                          y.ordered_t.get(s.source_id, 0.0), y.delivered_t.get(s.source_id, 0.0),
                          y.payable_t.get(s.source_id, 0.0),
                          y.variable_payment_mln.get(s.source_id, 0.0),
                          y.reservation_payment_mln.get(s.source_id, 0.0)))
    for m in res.months:
        conn.execute("INSERT INTO run_month VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (run_id, m.index, m.year, m.month, m.opening_t, m.gross_t, m.losses_t, m.served_t,
                      m.shortage_t, m.closing_t, m.capacity_t, m.target_t))
    for i, v in enumerate(res.violations):
        conn.execute("INSERT INTO run_violation VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (run_id, i, v.code, v.severity, str(v.period), v.metric,
                      None if v.value is None else float(v.value),
                      None if v.limit is None else float(v.limit),
                      None if v.excess is None else float(v.excess), v.message))
    conn.commit()
    return run_id
