"""Сравнение стратегий на единой базе: один прогон на каждую пару план × сценарий.

    PYTHONPATH=src python scripts/compare_strategies.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INVEST_FULL = {"ZBO": 2036, "LUNAR_ISRU": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2036}
STRATEGIES = {
    "base-only": ("План под BASE", INVEST_FULL, "BASE"),
    "robust": ("Устойчивый план", INVEST_FULL, "MANDATORY_STRESS"),
    "no-lunar": ("Без Луны", {**INVEST_FULL, "LUNAR_ISRU": None}, "MANDATORY_STRESS"),
    "no-earth-new": ("Без нового поставщика", {**INVEST_FULL, "EARTH_NEW_OPTION": None,
                                                "EARTH_NEW_EXERCISE": None}, "MANDATORY_STRESS"),
    "no-zbo": ("Без модернизации хранилища", {**INVEST_FULL, "ZBO": None}, "MANDATORY_STRESS"),
}


def main() -> None:
    case, scen = load_case(), load_all()
    print(f"набор данных {case.version}; деньги в млн у.е. в ценах 2035 года\n")
    header = f"{"стратегия":<28}{'сценарий':<18}{'PV':>8}{'расходы':>9}{'SL общ':>8}{'SL крит':>9}{'дефицит':>9}{'наруш':>7}"
    print(header)
    print("-" * len(header))
    for key, (name, invest, built_for) in STRATEGIES.items():
        plan = auto_plan(case, scen[built_for], invest, key, name)
        for sid in ("BASE", "MANDATORY_STRESS"):
            res = run(case, scen[sid], plan)
            hard = sum(1 for v in res.violations if v.severity == "hard")
            print(f"{name:<28}{sid:<18}{res.totals['discounted_cost_mln']:>8.0f}"
                  f"{res.totals['total_cost_mln']:>9.0f}{res.totals['sl_total'] * 100:>8.2f}"
                  f"{res.totals['sl_critical'] * 100:>9.2f}{res.totals['shortage_t']:>9.1f}{hard:>7}")
        print()


if __name__ == "__main__":
    main()
