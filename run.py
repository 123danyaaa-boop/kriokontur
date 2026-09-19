#!/usr/bin/env python3
"""Запуск всего одной командой:  python run.py

Что делает по шагам:
    1. проверяет версию Python;
    2. доустанавливает недостающие библиотеки из requirements.txt;
    3. прогоняет тесты, чтобы расчёт точно не сломан;
    4. поднимает сервер и печатает адрес, который надо открыть в браузере.

Полезные ключи:
    python run.py --skip-tests     не запускать тесты
    python run.py --port 8080      другой порт, если 8000 занят
    python run.py --check          только проверить окружение и посчитать план, без сервера
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
NEEDED = ["yaml", "openpyxl", "fastapi", "uvicorn", "pytest"]


def step(text: str) -> None:
    print(f"\n[ШАГ] {text}")


def ensure_python() -> None:
    step("проверяю Python")
    if sys.version_info < (3, 10):
        sys.exit(f"нужен Python 3.10 или новее, у вас {sys.version.split()[0]}")
    print(f"  ок, Python {sys.version.split()[0]}")


def ensure_packages() -> None:
    step("проверяю библиотеки")
    missing = []
    for mod in NEEDED:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        print("  всё на месте")
        return
    print(f"  не хватает: {', '.join(missing)}. Ставлю из requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    if subprocess.call(cmd) != 0:
        print("  не получилось поставить обычным способом, пробую с --break-system-packages")
        subprocess.check_call(cmd + ["--break-system-packages"])


def run_tests() -> None:
    step("прогоняю тесты расчёта")
    code = subprocess.call([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT)
    if code != 0:
        sys.exit("тесты не прошли: сервер не поднимаю, сначала чиним расчёт")


def quick_check() -> None:
    step("считаю контрольный план в двух сценариях")
    sys.path.insert(0, str(SRC))
    from kriokontur.caseinput import load_case
    from kriokontur.engine import run
    from kriokontur.plan import Plan
    from kriokontur.scenarios import load_all
    case, scen = load_case(), load_all()
    plan = Plan.load(ROOT / "configs" / "plans" / "final-candidate.json")
    for sid in ("BASE", "MANDATORY_STRESS"):
        res = run(case, scen[sid], plan)
        hard = sum(1 for v in res.violations if v.severity == "hard")
        print(f"  {sid:<18} приведённые расходы {res.totals['discounted_cost_mln']:.0f} млн, "
              f"обслуживание {res.totals['sl_total'] * 100:.2f}%, жёстких нарушений {hard}")


def serve(port: int) -> None:
    step("поднимаю сервер")
    sys.path.insert(0, str(SRC))
    import uvicorn
    print(f"\n  Откройте в браузере:  http://127.0.0.1:{port}")
    print("  Документация API:     http://127.0.0.1:{}/docs".format(port))
    print("  Остановить сервер:    Ctrl+C\n")
    uvicorn.run("kriokontur.api:app", host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    ensure_python()
    ensure_packages()
    if not args.skip_tests:
        run_tests()
    quick_check()
    if args.check:
        print("\nпроверка окончена, сервер не запускался")
        return
    serve(args.port)


if __name__ == "__main__":
    main()
