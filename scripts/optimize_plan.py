"""Автоматический подбор плана и сравнение с ручным планом команды.

    PYTHONPATH=src python scripts/optimize_plan.py            только печатает таблицу
    PYTHONPATH=src python scripts/optimize_plan.py --write    ещё и обновляет файлы

По умолчанию скрипт ничего не записывает: проверка жюри не должна оставлять дифф.
С ключом --write пишет results/protocols/11_optimizer.md и .json и сохраняет планы
в configs/plans/optimized-*.json. Время работы решателя в файлы не попадает: оно зависит
от машины, и из-за него каждый прогон давал бы изменения. Время печатается в консоль.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur.caseinput import load_case
from kriokontur.optimizer import OptimizerSettings, optimize_and_verify, verify
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all


def main() -> None:
    ap = argparse.ArgumentParser(description="Автоматический подбор плана и сравнение с ручным планом")
    ap.add_argument("--write", action="store_true",
                    help="записать протокол 11 и планы configs/plans/optimized-*.json")
    args = ap.parse_args()
    case, scen = load_case(), load_all()
    ids = list(scen)
    base = Plan.load(ROOT / "configs" / "plans" / "final-candidate.json")
    ref = verify(case, scen, base, ids)
    variants = [
        ("optimized-control", "Оптимизатор: два контрольных сценария", OptimizerSettings(scenario_ids=["BASE", "MANDATORY_STRESS"])),
        ("optimized-robust-capacity", "Оптимизатор: все сценарии, страховка мощностью", OptimizerSettings(scenario_ids=ids, strategic_stock=False)),
        ("optimized-robust", "Оптимизатор: все сценарии, страховка запасом", OptimizerSettings(scenario_ids=ids)),
    ]
    rows = [("final-candidate", "Ручной план команды", ref, 0.0, base)]
    for key, label, st in variants:
        t = time.time()
        res = optimize_and_verify(case, scen, base, st, report_ids=ids, max_iter=8)
        res.plan.plan_id, res.plan.name = key, label
        if args.write:
            res.plan.save(ROOT / "configs" / "plans" / f"{key}.json", "BASE")
        rows.append((key, label, res.verification, time.time() - t, res.plan))
    lines = ["# Протокол: автоматический подбор плана", "",
             f"Набор данных `{case.version}`, ставка 8 %, проверка каждого плана помесячным движком во всех {len(ids)} сценариях.",
             "Оптимизатор предлагает план, движок решает, исполним ли он. Число нарушений ниже взято из движка, а не из модели.", "",
             "| План | PV BASE, млн | PV стресс, млн | Среднее по сценариям, млн | Худший сценарий, млн | Сценариев с нарушениями | Инвестиции |",
             "|---|---|---|---|---|---|---|"]
    out = {}
    for key, label, v, secs, plan in rows:
        pv = [x["pv_mln"] for x in v.values()]
        bad = sum(1 for x in v.values() if x["hard"])
        inv = ", ".join(f"{k} {y}" for k, y in plan.investments.items() if y)
        lines.append(f"| {label} | {v['BASE']['pv_mln']:,.1f} | {v['MANDATORY_STRESS']['pv_mln']:,.1f} | {statistics.fmean(pv):,.1f} | "
                     f"{max(pv):,.1f} | {bad} из {len(ids)} | {inv} |".replace(",", " "))
        out[key] = {"label": label, "base": v["BASE"]["pv_mln"], "stress": v["MANDATORY_STRESS"]["pv_mln"],
                    "mean": statistics.fmean(pv), "worst": max(pv), "bad": bad,
                    "problems": {k: x["codes"] for k, x in v.items() if x["hard"]}}
        print(f"{label:<50} BASE {v['BASE']['pv_mln']:9.1f}  стресс {v['MANDATORY_STRESS']['pv_mln']:9.1f}  "
              f"нарушений {bad}/{len(ids)}  время {secs:.1f} с")
    lines += ["", "Чтение: план под два контрольных сценария самый дешёвый, но не выдерживает рисковые сценарии.",
              "Разница между ним и робастным планом это цена робастности. Страховка мощностью дешевле страховки запасом."]
    if not args.write:
        print("\nфайлы не менялись; чтобы обновить протокол 11 и планы optimized-*.json, запустите с --write")
        return
    # newline="\n": одинаковые байты на Windows и Linux, иначе git видит изменения в каждом прогоне
    (ROOT / "results" / "protocols" / "11_optimizer.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (ROOT / "results" / "protocols" / "11_optimizer.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                                     encoding="utf-8", newline="\n")
    print("\nзаписаны results/protocols/11_optimizer.md, .json и configs/plans/optimized-*.json")


if __name__ == "__main__":
    main()
