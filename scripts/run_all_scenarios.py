"""Прогон плана по всем сценариям кейса и команды.

    PYTHONPATH=src python scripts/run_all_scenarios.py [--plan configs/plans/robust.json]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all

ORDER = ["BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND", "TEAM_COMBINED_STRESS_HIGH",
         "TEAM_RISK_EARTH_NEW_DELAY", "TEAM_RISK_ISRU_UNDERPERFORMANCE", "TEAM_RISK_CORE_OUTAGE",
         "TEAM_GEO_PRICE_SHOCK", "TEAM_GEO_WITH_STRESS"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="configs/plans/robust.json")
    args = ap.parse_args()
    case, scen, plan = load_case(), load_all(), Plan.load(args.plan)
    print(f"план {plan.plan_id} | набор {case.version} | ставка {plan.assume('discount_rate')}\n")
    head = f"{'сценарий':<32}{'статус':<18}{'PV':>8}{'SL общ':>8}{'SL крит':>9}{'дефицит':>9}{'жёстких':>9}  коды"
    print(head)
    print("-" * (len(head) + 20))
    for sid in ORDER + [s for s in scen if s not in ORDER]:
        sc = scen[sid]
        res = run(case, sc, plan)
        hard = [v for v in res.violations if v.severity == "hard"]
        codes = ",".join(sorted({v.code for v in hard})) or "-"
        print(f"{sid:<32}{sc.status:<18}{res.totals['discounted_cost_mln']:>8.0f}"
              f"{res.totals['sl_total'] * 100:>8.2f}{res.totals['sl_critical'] * 100:>9.2f}"
              f"{res.totals['shortage_t']:>9.1f}{len(hard):>9}  {codes}")


if __name__ == "__main__":
    main()
