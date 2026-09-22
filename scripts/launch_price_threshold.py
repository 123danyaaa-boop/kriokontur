"""Порог цены выведения, при котором лунное производство перестаёт окупаться до конца горизонта.

Главный внешний риск лунной части: цена доставки топлива с Земли. Если сверхтяжёлые
многоразовые носители снизят её, преимущество лунного топлива (3,0 против 6,2–8,9 у.е./т)
может исчезнуть. Скрипт ищет делением отрезка множитель земных цен с 2038 года, при котором
первая очередь Луны перестаёт обгонять второго земного поставщика к 2048 году.

    PYTHONPATH=src python scripts/launch_price_threshold.py
Все величины за 2040 годом и сама траектория цен это TEAM_RESEARCH.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.horizon import HorizonConfig, cumulative_discounted, extend_case, payback_year
from kriokontur.planner import auto_plan
from kriokontur.scenarios import load_all

INV = {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035, "ZBO2": 2041}
EARTH = (("Earth-Core", "A"), ("Earth-Flex", "B"), ("Earth-New", "C"), ("Earth-New-2", "C2"), ("Emergency", "E"))


def gain(case, cfg, base, method: str, factor: float, from_year: int = 2038, rate: float = 0.08):
    table = {name: {y: (factor if y >= from_year else 1.0) for y in range(case.years[0], cfg.last_year + 1)}
             for name, _ in EARTH}
    sc = base.derive(f"LAUNCH_x{factor:.3f}", f"цена доставки с Земли ×{factor:.2f} с {from_year}", price_multiplier=table)
    cums = {}
    for key, inv, enable in (("earth2", {**INV, "EARTH_NEW_2": 2041}, ["EARTH_NEW_2"]),
                             ("isru1", {**INV, "LUNAR_ISRU": 2036}, [])):
        ext = extend_case(case, cfg, method, ["ZBO2"] + enable)
        plan = auto_plan(ext, sc, inv, key)
        plan.assumptions["discount_rate"] = rate
        cums[key] = cumulative_discounted(run(ext, sc, plan), case.years[0], rate)
    return cums["earth2"][cfg.last_year] - cums["isru1"][cfg.last_year], payback_year(cums["isru1"], cums["earth2"])


def threshold(case, cfg, base, method: str, lo: float = 0.3, hi: float = 1.0, tol: float = 0.005):
    """Минимальный множитель, при котором выигрыш Луны к концу горизонта ещё неотрицателен."""
    g_hi, _ = gain(case, cfg, base, method, hi)
    g_lo, _ = gain(case, cfg, base, method, lo)
    if g_lo >= 0:
        return lo
    if g_hi < 0:
        return None
    while hi - lo > tol:
        mid = (lo + hi) / 2
        g, _ = gain(case, cfg, base, method, mid)
        lo, hi = (lo, mid) if g >= 0 else (mid, hi)
    return hi


def main() -> None:
    case, scen, cfg = load_case(), load_all(), HorizonConfig.load()
    base = scen["BASE"]
    out = {"horizon": cfg.last_year, "from_year": 2038, "rows": [], "thresholds": {}}
    lines = ["# Протокол: порог цены доставки с Земли для лунного производства", "",
             f"Горизонт до {cfg.last_year} года, ставка 8 %, земные цены умножаются на коэффициент с 2038 года.",
             "Сравнение: первая очередь Луны против второго земного поставщика (честная земная альтернатива).",
             "Все величины за 2040 годом это TEAM_RESEARCH.", "",
             "| Множитель земных цен | Гипотеза спроса | Год перелома | Выигрыш Луны к концу горизонта, млн |",
             "|---|---|---|---|"]
    for f in (1.0, 0.8, 0.7, 0.6, 0.5, 0.3):
        for method in ("increment", "saturating", "plateau"):
            g, py = gain(case, cfg, base, method, f)
            out["rows"].append({"factor": f, "method": method, "payback": py, "gain_mln": round(g, 1)})
            lines.append(f"| {f:.1f} | {method} | {py or 'не окупается'} | {g:,.0f} |".replace(",", " "))
    lines += ["", "## Порог", "", "| Гипотеза спроса | Минимальный множитель земных цен, при котором Луна окупается к концу горизонта |", "|---|---|"]
    for method in ("increment", "saturating", "plateau"):
        t = threshold(case, cfg, base, method)
        out["thresholds"][method] = t
        lines.append(f"| {method} | {'нет порога в диапазоне' if t is None else f'{t:.2f}'} |")
        print(f"{method:<11} порог множителя земных цен: {t}")
    lines += ["", "Чтение: если цена доставки топлива с Земли после 2038 года упадёт ниже порога относительно",
              "уровня кейса, лунное производство не окупится до конца горизонта. Решение о Луне поэтому",
              "принимается как инвестиционные ворота с условием на траекторию цен выведения."]
    (ROOT / "results" / "protocols" / "12_launch_price.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "results" / "protocols" / "12_launch_price.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
