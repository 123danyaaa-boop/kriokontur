"""Синхронизация чисел в документации с фактическими прогонами (D-19).

    PYTHONPATH=src python scripts/sync_docs.py            перезаписать блоки
    PYTHONPATH=src python scripts/sync_docs.py --check    только проверить (код возврата 1 при расхождении)

В документах есть размеченные блоки:

    <!-- KEY_NUMBERS:начало имя -->
    ... содержимое, которое генерируется этим скриптом ...
    <!-- KEY_NUMBERS:конец имя -->

Скрипт пересчитывает числа движком и переписывает содержимое блоков. Тест
`tests/test_docs_numbers.py` запускает тот же расчёт в режиме проверки, поэтому документация
не может разойтись с продуктом незаметно: расхождение валит тесты.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur.caseinput import load_case  # noqa: E402
from kriokontur.engine import run  # noqa: E402
from kriokontur.plan import Plan  # noqa: E402
from kriokontur.scenarios import load_all  # noqa: E402

CONTROL = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")
BLOCK = re.compile(r"(<!-- KEY_NUMBERS:начало (?P<name>[\w-]+) -->\n)(?P<body>.*?)(<!-- KEY_NUMBERS:конец (?P=name) -->)",
                   re.S)


def f(v, digits=2):
    return f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")


def test_count() -> int:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--collect-only"],
                          cwd=ROOT, capture_output=True, text=True)
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    return int(match.group(1)) if match else 0


def blocks() -> dict:
    case, scen = load_case(), load_all()
    plan = Plan.load(ROOT / "configs/plans/final-candidate.json")
    res = {sid: run(case, scen[sid], plan) for sid in CONTROL}
    base, stress = res["BASE"], res["MANDATORY_STRESS"]
    count = test_count()

    plan_totals = "\n".join(
        f"| {sid} | {f(res[sid].totals['discounted_cost_mln'])} | {f(res[sid].totals['total_cost_mln'])} | "
        f"{f(min(y.sl_total for y in res[sid].years) * 100)} % | {f(res[sid].totals['shortage_t'])} | "
        f"{sum(1 for v in res[sid].violations if v.severity == 'hard')} |"
        for sid in CONTROL)

    structure = "\n".join(
        f"| {label} | {f(base.totals[key])} | {f(stress.totals[key])} |"
        for key, label in (("procurement", "Закупки"), ("reservation", "Плата за резерв"),
                           ("holding", "Хранение"), ("fixed_opex", "Постоянный OPEX"),
                           ("capex", "CAPEX"), ("total_cost_mln", "Итого номиналом"),
                           ("discounted_cost_mln", "Итого приведённых")))

    stock = "\n".join(
        f"| {y.year} | {f(y.opening_t)} | {f(y.reserve_required_t)} | {f(y.closing_t)} | "
        f"{f(s.opening_t)} | {f(s.reserve_required_t)} | {f(s.closing_t)} |"
        for y, s in zip(base.years, stress.years))

    return {
        "case-version": f"Набор данных организатора: `{case.version}` (sha256 по контрольным CSV, "
                        f"переводы строк нормализованы).\n",
        "test-count": f"Тестов в наборе: **{count}**, включая контрольные примеры V01–V10, "
                      f"инварианты движка, проверки договоров, рисков и выгрузки.\n",
        "final-plan": ("Финальный план «" + plan.name + "», ставка "
                       + f(float(plan.assume("discount_rate")) * 100, 1) + " %, база цен 2035 года:\n\n"
                       + "| Сценарий | Приведённые, млн | Номинал, млн | Обслуживание, худший год | "
                         "Дефицит, т | Жёстких нарушений |\n|---|---|---|---|---|---|\n"
                       + plan_totals + "\n"),
        "cost-structure": ("| Статья | BASE, млн | Обязательный стресс, млн |\n|---|---|---|\n"
                           + structure + "\n"),
        "stock-profile": ("| Год | Запас на начало BASE, т | Норма BASE, т | Конец года BASE, т | "
                          "Запас на начало стресс, т | Норма стресс, т | Конец года стресс, т |\n"
                          "|---|---|---|---|---|---|---|\n" + stock + "\n"),
    }


def apply(check_only: bool) -> int:
    data = blocks()
    problems = []
    for path in sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        if "KEY_NUMBERS:начало" not in text:
            continue

        def repl(m):
            name = m.group("name")
            if name not in data:
                problems.append(f"{path.name}: неизвестный блок {name}")
                return m.group(0)
            return m.group(1) + data[name] + m.group(4)

        updated = BLOCK.sub(repl, text)
        if updated != text:
            if check_only:
                problems.append(f"{path.relative_to(ROOT)}: числа устарели")
            else:
                # newline="\n": иначе на Windows README уходит в CRLF и git status не чистый
                path.write_text(updated, encoding="utf-8", newline="\n")
                print("обновлён", path.relative_to(ROOT))
    if problems:
        print("\n".join(problems))
        return 1
    print("документация синхронизирована" if not check_only else "документация актуальна")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    sys.exit(apply(ap.parse_args().check))


if __name__ == "__main__":
    main()
