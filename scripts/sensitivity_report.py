"""Чувствительность, торнадо и обратный стресс для выбранного плана и сценария.

    PYTHONPATH=src python scripts/sensitivity_report.py --plan configs/plans/robust.json --scenario BASE
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import PARAMS, reverse_stress, sweep, tornado


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="configs/plans/robust.json")
    ap.add_argument("--scenario", default="BASE")
    args = ap.parse_args()
    case, scen, plan = load_case(), load_all(), Plan.load(args.plan)
    sc = scen[args.scenario]
    print(f"план {plan.plan_id}, сценарий {args.scenario}\n")

    print("ТОРНАДО: разброс приведённых расходов при отклонении параметра на ±10%")
    print(f"{'параметр':<38}{'низ':>10}{'верх':>10}{'размах':>10}  ломает план")
    for row in tornado(case, sc, plan):
        print(f"{row['label']:<38}{row['pv_low']:>10.0f}{row['pv_high']:>10.0f}{row['swing']:>10.0f}"
              f"  {'да' if row['breaks_plan'] else 'нет'}")

    print("\nЧУВСТВИТЕЛЬНОСТЬ: спрос")
    print(f"{'множитель':>10}{'PV':>10}{'SL общ':>9}{'дефицит':>10}  нарушения")
    for row in sweep(case, sc, plan, "demand", steps=7):
        print(f"{row['value']:>10.2f}{row['pv_mln']:>10.0f}{row['sl_total'] * 100:>9.2f}"
              f"{row['shortage_t']:>10.1f}  {row['codes'] or '-'}")

    print("\nЧУВСТВИТЕЛЬНОСТЬ: фактическая поставка Луны")
    print(f"{'множитель':>10}{'PV':>10}{'SL общ':>9}{'дефицит':>10}  нарушения")
    for row in sweep(case, sc, plan, "isru_delivery", steps=6):
        print(f"{row['value']:>10.2f}{row['pv_mln']:>10.0f}{row['sl_total'] * 100:>9.2f}"
              f"{row['shortage_t']:>10.1f}  {row['codes'] or '-'}")

    print("\nОБРАТНЫЙ СТРЕСС: при каком значении план впервые нарушает ограничение")
    for row in reverse_stress(case, sc, plan):
        if row.get("boundary") is None:
            print(f"  {row['label']:<38} в диапазоне {PARAMS[row['key']].low}–{PARAMS[row['key']].high} держится")
        else:
            print(f"  {row['label']:<38} порог {row['boundary']:.3f} {row['unit']}, "
                  f"последнее рабочее {row['last_feasible']:.3f}, первое нарушение {row['first_violation']}")


if __name__ == "__main__":
    main()
