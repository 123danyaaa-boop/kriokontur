"""Проверка расширяемости на копии набора: шестой канал и седьмой год.

Кейс требует показать, что добавление источника и расширение горизонта делается данными и
конфигурацией, без переделки расчётной логики. Обязательное решение при этом продолжает
считаться на исходных данных: копия лежит в отдельной папке и не трогает data/.

    PYTHONPATH=src python scripts/extensibility_demo.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.plan import Plan
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all
from kriokontur.sensitivity import metrics

ROOT = Path(__file__).resolve().parents[1]
SOURCE_X = ('X,Source-X,150,5.4,0.25,0.30,10,14,month,"constant:0.95",2037,TEAM_RESEARCH,'
            '"исследовательский шестой канал, не входит в обязательный расчёт"\n')
YEAR_2041 = "2041,470,300,376,587.5,TEAM_RESEARCH\n"


def main() -> None:
    case, scen = load_case(), load_all()
    plan = Plan.load(ROOT / "configs" / "plans" / "final-candidate.json")
    base = metrics(run(case, scen["BASE"], plan))
    print(f"обязательный расчёт на исходных данных: набор {case.version}, годы {case.years[0]}–{case.years[-1]}, "
          f"каналов {len(case.sources)}, PV {base['pv_mln']:.0f}\n")

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "data"
        shutil.copytree(ROOT / "data", copy)

        # 1. шестой канал: одна строка в supply_sources.csv
        with (copy / "supply_sources.csv").open("a", encoding="utf-8") as fh:
            fh.write(SOURCE_X)
        ext = load_case(copy)
        print(f"1. добавлен канал Source-X: каналов {len(ext.sources)}, версия набора {ext.version}")
        plan_x = auto_plan(ext, scen["BASE"], plan.investments, "with-source-x", "С шестым каналом")
        res_x = run(ext, scen["BASE"], plan_x)
        m_x = metrics(res_x)
        print(f"   автоплан подхватил канал без правок кода: отбор X по годам "
              f"{[round(res_x.year(y).ordered_t.get('X', 0.0)) for y in ext.years]} т, "
              f"PV {m_x['pv_mln']:.0f}, нарушений {int(m_x['hard_violations'])}")

        # 2. седьмой год: одна строка в demand.csv
        with (copy / "demand.csv").open("a", encoding="utf-8") as fh:
            fh.write(YEAR_2041)
        ext2 = load_case(copy)
        plan_y = auto_plan(ext2, scen["BASE"], plan.investments, "horizon-2041", "Горизонт до 2041")
        res_y = run(ext2, scen["BASE"], plan_y)
        m_y = metrics(res_y)
        print(f"\n2. добавлен 2041 год: годы {ext2.years[0]}–{ext2.years[-1]}, месяцев в прогоне {len(res_y.months)}, "
              f"PV {m_y['pv_mln']:.0f}, нарушений {int(m_y['hard_violations'])}")
        print(f"   спрос 2041 {res_y.year(2041).demand_total_t:.0f} т, обслужено "
              f"{res_y.year(2041).sl_total * 100:.2f}%, запас на конец {res_y.year(2041).closing_t:.1f} т")
        print("   допущения расширения: спрос, цены и доступность 2041 года это TEAM_RESEARCH, "
              "лимиты и ограничения кейса на него не распространяются автоматически")

    again = load_case()
    print(f"\n3. исходный набор не изменился: версия {again.version}, годы {again.years[0]}–{again.years[-1]}, "
          f"каналов {len(again.sources)}")


if __name__ == "__main__":
    main()
