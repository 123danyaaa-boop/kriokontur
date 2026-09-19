"""Реестр рисков с расчётом последствий, геополитическое событие и Монте-Карло.

    PYTHONPATH=src python scripts/risk_report.py --plan configs/plans/robust.json --trials 300
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.risks import evaluate_risks, geo_event, load_risks, monte_carlo
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="configs/plans/robust.json")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260918)
    args = ap.parse_args()
    case, scen, plan = load_case(), load_all(), Plan.load(args.plan)

    print("РЕЕСТР РИСКОВ: последствие считается как разница прогонов на одном плане\n")
    print(f"{'id':<4}{'событие':<52}{'вер.':>6}{'ΔPV':>9}{'Δдефицит':>10}{'SL':>8}  коды")
    for row in evaluate_risks(case, scen, plan, load_risks()):
        prob = "-" if row.get("probability") is None else f"{row['probability']:.2f}"
        print(f"{row['risk_id']:<4}{row['event'][:50]:<52}{prob:>6}{row['delta_pv_mln']:>9.0f}"
              f"{row['delta_shortage_t']:>10.1f}{row['sl_total'] * 100:>8.2f}  {row['violation_codes'] or '-'}")

    print("\nГЕОПОЛИТИЧЕСКИЙ МОДУЛЬ: событие задаётся параметрами")
    base = scen["BASE"]
    for mult in (1.2, 1.4, 1.6):
        sc = geo_event(case, base, f"рост цены земных каналов ×{mult}", ["A", "B"], 2036, 2039, mult)
        m = metrics(run(case, sc, plan))
        print(f"  ×{mult:<5} PV {m['pv_mln']:>8.0f}  ΔPV {m['pv_mln'] - metrics(run(case, base, plan))['pv_mln']:>8.0f}"
              f"  SL {m['sl_total'] * 100:6.2f}  нарушений {int(m['hard_violations'])}")
    print("  возврат к контрольным ценам: выбрать сценарий BASE, параметры события не сохраняются в CASE_INPUT")

    print(f"\nМОНТЕ-КАРЛО: {args.trials} испытаний, seed {args.seed}")
    for sid in ("BASE", "MANDATORY_STRESS"):
        mc = monte_carlo(case, scen[sid], plan, trials=args.trials, seed=args.seed)
        print(f"  {sid:<18} P(жёсткое нарушение) {mc.p_hard_violation:5.2f}  P(SL<97%) {mc.p_service_below_min:5.2f}"
              f"  PV среднее {mc.pv_mean:7.0f}  p95 {mc.pv_p95:7.0f}  дефицит p95 {mc.shortage_p95_t:6.1f} т")
        print(f"  {'':<18} трактовка: {mc.interpretation}")
        print(f"  {'':<18} частые коды: {mc.worst_codes or 'нет'}")


if __name__ == "__main__":
    main()
