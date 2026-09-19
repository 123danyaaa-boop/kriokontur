"""Командная строка: прогон, сравнение сценариев, выгрузка, автоплан.

    python -m kriokontur.cli autoplan --scenario MANDATORY_STRESS --out configs/plans/robust.json
    python -m kriokontur.cli run --plan configs/plans/robust.json --scenario BASE
    python -m kriokontur.cli compare --plan configs/plans/robust.json
    python -m kriokontur.cli export --plan configs/plans/robust.json --scenario MANDATORY_STRESS --out results/
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import db, export
from .caseinput import load_case
from .engine import run
from .plan import Plan
from .planner import auto_plan
from .scenarios import SCEN_DIR, load_all

DEFAULT_INVEST = {"ZBO": 2036, "LUNAR_ISRU": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}


def _print(res) -> None:
    print(f"план {res.plan_id} | сценарий {res.scenario_id} | набор {res.case_version} | {res.engine_version}")
    print(f"{'год':>6}{'спрос':>9}{'поставка':>10}{'потери':>8}{'выдано':>9}{'дефицит':>9}"
          f"{'запас':>8}{'SL общ':>8}{'SL крит':>9}{'расходы':>10}")
    for y in res.years:
        print(f"{y.year:>6}{y.demand_total_t:>9.1f}{y.gross_t:>10.1f}{y.losses_t:>8.1f}{y.served_t:>9.1f}"
              f"{y.shortage_t:>9.1f}{y.closing_t:>8.1f}{y.sl_total * 100:>8.1f}{y.sl_critical * 100:>9.1f}"
              f"{y.total_cost_mln:>10.0f}")
    t = res.totals
    print(f"итого: расходы {t['total_cost_mln']:.0f} млн, приведённые {t['discounted_cost_mln']:.0f} млн, "
          f"на тонну обслуженного спроса {t['cost_per_served_t']:.2f}")
    print(f"обслуживание: общее {t['sl_total'] * 100:.2f}%, критическое {t['sl_critical'] * 100:.2f}%, "
          f"дефицит {t['shortage_t']:.1f} т, потери {t['losses_t']:.1f} т")
    print("план исполним" if res.feasible else "план неисполним")
    for v in res.violations:
        print(f"  [{v.severity}] {v.message}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kriokontur", description="Расчётное ядро топливного узла 2035-2040")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("run", "compare", "export"):
        p = sub.add_parser(name)
        p.add_argument("--plan", required=True)
        p.add_argument("--scenario", default="BASE")
        p.add_argument("--out", default="results")
        p.add_argument("--save-db", action="store_true")
    p = sub.add_parser("autoplan")
    p.add_argument("--scenario", default="MANDATORY_STRESS")
    p.add_argument("--out", default="configs/plans/auto.json")
    p.add_argument("--no-lunar", action="store_true")
    args = parser.parse_args()

    case, scenarios = load_case(), load_all()

    if args.cmd == "autoplan":
        invest = dict(DEFAULT_INVEST)
        if args.no_lunar:
            invest["LUNAR_ISRU"] = None
        plan = auto_plan(case, scenarios[args.scenario], invest, Path(args.out).stem)
        plan.save(args.out, args.scenario)
        print(f"план сохранён: {args.out}")
        return

    plan = Plan.load(args.plan)
    if args.cmd == "run":
        res = run(case, scenarios[args.scenario], plan)
        _print(res)
    elif args.cmd == "compare":
        for sid in scenarios:
            res = run(case, scenarios[sid], plan)
            hard = sum(1 for v in res.violations if v.severity == "hard")
            print(f"{sid:<18} PV {res.totals['discounted_cost_mln']:>8.0f} | SL {res.totals['sl_total'] * 100:6.2f}% "
                  f"| крит {res.totals['sl_critical'] * 100:6.2f}% | дефицит {res.totals['shortage_t']:6.1f} т "
                  f"| жёстких нарушений {hard}")
    elif args.cmd == "export":
        res = run(case, scenarios[args.scenario], plan)
        csv_path = export.write_csv(case, res, scenarios[args.scenario],
                                    Path(args.out) / f"{plan.plan_id}_{args.scenario}.csv")
        xlsx_path = export.write_xlsx(case, res, scenarios[args.scenario],
                                      Path(args.out) / f"{plan.plan_id}_{args.scenario}.xlsx")
        print(f"выгружено: {csv_path}, {xlsx_path}")
    if getattr(args, "save_db", False):
        conn = db.connect()
        db.sync_case(conn, case)
        db.sync_scenarios(conn, scenarios, SCEN_DIR)
        db.save_plan(conn, plan)
        print("run_id:", db.save_run(conn, run(case, scenarios[args.scenario], plan), case))


if __name__ == "__main__":
    main()
