"""Расчёт на перспективу: что делают сегодняшние решения за горизонтом 2040 года.

    PYTHONPATH=src python scripts/horizon_report.py [--method increment] [--rate 0.08]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.horizon import (HorizonConfig, cost_per_served, cumulative_discounted, earth_ceiling,
                                extend_case, first_deficit_year, first_infeasible_year, payback_year)
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import metrics

BASE_INVEST = {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035}
ARCH = {
    "earth": ("Только Земля", {**BASE_INVEST, "ZBO2": 2041}, []),
    "earth2": ("Земля плюс второй поставщик", {**BASE_INVEST, "ZBO2": 2041, "EARTH_NEW_2": 2041}, ["EARTH_NEW_2"]),
    "isru1": ("Луна, первая очередь", {**BASE_INVEST, "ZBO2": 2041, "LUNAR_ISRU": 2036}, []),
    "isru2": ("Луна, две очереди", {**BASE_INVEST, "ZBO2": 2041, "LUNAR_ISRU": 2036,
                                    "LUNAR_ISRU_PHASE2": 2041}, ["LUNAR_ISRU_PHASE2"]),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="increment", choices=["increment", "saturating", "plateau"])
    ap.add_argument("--rate", type=float, default=0.08)
    args = ap.parse_args()

    case, scen, cfg = load_case(), load_all(), HorizonConfig.load()
    base = scen["BASE"]
    print(f"Горизонт до {cfg.last_year}, способ продления спроса: {args.method} "
          f"({cfg.methods[args.method]['basis']})")
    print(f"Ставка {args.rate}. Все величины за 2040 годом это TEAM_RESEARCH.\n")

    demo = extend_case(case, cfg, args.method, ["ZBO2"])
    print("спрос, т/год:", " ".join(f"{y}:{demo.demand_total[y]:.0f}" for y in demo.years))
    ceiling = earth_ceiling(demo)
    print(f"потолок земных каналов A+B+C: {ceiling:.0f} т/год, с аварийным каналом "
          f"{earth_ceiling(demo, True):.0f} т/год")
    print(f"первый год, где земной архитектуры не хватает: {first_infeasible_year(demo, ceiling)}")
    print(f"то же с гипотетическим вторым поставщиком: "
          f"{first_infeasible_year(extend_case(case, cfg, args.method, ['ZBO2', 'EARTH_NEW_2']), ceiling + 130)}\n")

    cums, percost, results = {}, {}, {}
    print(f"{'архитектура':<30}{'PV 2040':>9}{'PV ' + str(cfg.last_year):>9}{'обслужено, т':>14}"
          f"{'дефицит, т':>12}{'млн/т':>8}{'деф. с года':>12}")
    for key, (label, invest, enable) in ARCH.items():
        ext = extend_case(case, cfg, args.method, ["ZBO2"] + enable)
        plan = auto_plan(ext, base, invest, key, label)
        plan.assumptions["discount_rate"] = args.rate
        res = run(ext, base, plan)
        results[key] = res
        cums[key] = cumulative_discounted(res, ext.years[0], args.rate)
        percost[key] = cost_per_served(res, ext.years[0], args.rate)
        m = metrics(res)
        first_def = first_deficit_year(res)
        print(f"{label:<30}{cums[key][2040]:>9.0f}{cums[key][cfg.last_year]:>9.0f}"
              f"{res.totals['served_t']:>14.0f}{m['shortage_t']:>12.1f}"
              f"{percost[key][cfg.last_year]:>8.2f}{str(first_def or 'нет'):>12}")

    print("\nсравнение архитектур, которые действительно закрывают спрос")
    print(f"{'пара':<46}{'год перелома':>14}{'выигрыш к ' + str(cfg.last_year):>18}")
    for key, rival, label in (("isru1", "earth2", "Луна первой очереди против второго земного поставщика"),
                              ("isru2", "earth2", "Две очереди Луны против второго земного поставщика"),
                              ("isru2", "isru1", "Вторая очередь Луны против первой")):
        year = payback_year(cums[key], cums[rival])
        diff = cums[rival][cfg.last_year] - cums[key][cfg.last_year]
        print(f"{label[:44]:<46}{str(year or 'нет'):>14}{diff:>18.0f}")

    print("\nстоимость обслуженной тонны накопленным итогом, млн у.е./т")
    print(f"{'год':>6}" + "".join(f"{ARCH[k][0][:14]:>16}" for k in ARCH))
    for year in (2040, 2043, 2045, cfg.last_year):
        print(f"{year:>6}" + "".join(f"{percost[k][year]:>16.2f}" for k in ARCH))

    print("\nобслуживание по годам, только Земля против двух очередей Луны")
    print(f"{'год':>6}{'спрос':>9}{'Земля выдано':>15}{'Земля дефицит':>15}{'Луна выдано':>14}{'Луна дефицит':>14}")
    for year in results["earth"].years:
        e = results["earth"].year(year.year)
        l = results["isru2"].year(year.year)
        print(f"{year.year:>6}{e.demand_total_t:>9.0f}{e.served_t:>15.0f}{e.shortage_t:>15.1f}"
              f"{l.served_t:>14.0f}{l.shortage_t:>14.1f}")

    print("\nпроверка вывода на трёх гипотезах спроса")
    print(f"{'гипотеза':<14}{'спрос 2045':>12}{'Земля дефицит':>16}{'Луна-2 дефицит':>16}{'перелом':>10}")
    for name in ("increment", "saturating", "plateau"):
        ext_e = extend_case(case, cfg, name, ["ZBO2"])
        ext_l = extend_case(case, cfg, name, ["ZBO2", "LUNAR_ISRU_PHASE2"])
        ext_e2 = extend_case(case, cfg, name, ["ZBO2", "EARTH_NEW_2"])
        pe = auto_plan(ext_e2, base, ARCH["earth2"][1], "e2"); pe.assumptions["discount_rate"] = args.rate
        pl = auto_plan(ext_l, base, ARCH["isru2"][1], "l2"); pl.assumptions["discount_rate"] = args.rate
        re_, rl = run(ext_e2, base, pe), run(ext_l, base, pl)
        ce = cumulative_discounted(re_, ext_e2.years[0], args.rate)
        cl = cumulative_discounted(rl, ext_l.years[0], args.rate)
        year = payback_year(cl, ce)
        print(f"{name:<14}{ext_e.demand_total[2045]:>12.0f}{re_.totals['shortage_t']:>16.1f}"
              f"{rl.totals['shortage_t']:>16.1f}{str(year or 'нет'):>10}")

    print("\nграницы вывода")
    for note in cfg.notes:
        print(f"  {note}")


if __name__ == "__main__":
    main()
