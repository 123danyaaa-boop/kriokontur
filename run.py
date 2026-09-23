#!/usr/bin/env python3
"""Запуск всего одной командой:  python run.py

Что делает по шагам:
    1. проверяет версию Python;
    2. доустанавливает недостающие библиотеки из requirements.txt;
    3. прогоняет тесты, чтобы расчёт точно не сломан;
    4. считает контрольный план в двух сценариях;
    5. поднимает сервер, сам выбирает свободный порт и открывает браузер.

Полезные ключи:
    python run.py --skip-tests     не запускать тесты
    python run.py --port 8080      начать подбор порта с другого номера
    python run.py --no-browser     не открывать браузер
    python run.py --check          только проверить окружение и посчитать план, без сервера
    python run.py --host 0.0.0.0   открыть API для всей локальной сети (по умолчанию 127.0.0.1)

Адрес можно задать и переменной окружения KRIOKONTUR_HOST; ключ --host важнее переменной.
Контейнер Docker передаёт 0.0.0.0 явно, иначе он недоступен снаружи.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
# pulp нужен кнопке «Оптимизировать»: без него сервер поднимается, но оптимизатор падает
NEEDED = ["yaml", "openpyxl", "fastapi", "uvicorn", "pytest", "pulp"]
DEFAULT_HOST = "127.0.0.1"        # по умолчанию API виден только с этой машины
HOST_ENV = "KRIOKONTUR_HOST"      # явное разрешение слушать другой адрес, например 0.0.0.0 в Docker
ALL_INTERFACES = "0.0.0.0"
PORT_RANGE = range(8000, 8011)   # 8000-8010, как договорились в документации


def resolve_host(cli_host: str | None) -> str:
    """Адрес сервера: ключ --host, затем переменная KRIOKONTUR_HOST, иначе 127.0.0.1."""
    return (cli_host or os.environ.get(HOST_ENV, "") or DEFAULT_HOST).strip()


def browse_host(host: str) -> str:
    """Адрес для ссылки и браузера: на 0.0.0.0 открыть страницу нельзя, берём 127.0.0.1."""
    return DEFAULT_HOST if host == ALL_INTERFACES else host


def _setup_console() -> None:
    """Консоль Windows по умолчанию не в UTF-8, и русские сообщения превращаются в кашу."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


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
    from kriokontur.paths import DEFAULT_PLAN, ensure_dirs
    from kriokontur.plan import Plan
    from kriokontur.scenarios import load_all
    ensure_dirs()
    case, scen = load_case(), load_all()
    plan = Plan.load(DEFAULT_PLAN)
    for sid in ("BASE", "MANDATORY_STRESS"):
        res = run(case, scen[sid], plan)
        hard = sum(1 for v in res.violations if v.severity == "hard")
        print(f"  {sid:<18} приведённые расходы {res.totals['discounted_cost_mln']:.0f} млн, "
              f"обслуживание {res.totals['sl_total'] * 100:.2f}%, жёстких нарушений {hard}")


# --------------------------------------------------------------------------- #
# порт и браузер
# --------------------------------------------------------------------------- #
def port_free(host: str, port: int) -> bool:
    """Порт свободен, если на него удаётся встать самим."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, preferred: int) -> int:
    """Берём запрошенный порт, а если занят — следующий свободный из 8000-8010."""
    candidates = [preferred] + [p for p in PORT_RANGE if p != preferred]
    for port in candidates:
        if port_free(host, port):
            if port != preferred:
                print(f"  порт {preferred} занят, беру свободный {port}")
            return port
    sys.exit(f"все порты {PORT_RANGE.start}-{PORT_RANGE.stop - 1} заняты: "
             f"освободите один или укажите свой ключом --port")


def open_browser_when_ready(url: str, host: str, port: int, timeout: float = 20.0) -> None:
    """Ждём, пока сервер начнёт принимать соединения, и только тогда открываем браузер."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((browse_host(host), port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)
    print(f"  браузер не открыл сам: откройте {url} вручную")


def serve(host: str, port: int, open_browser: bool) -> None:
    step("поднимаю сервер")
    sys.path.insert(0, str(SRC))
    import uvicorn
    url = f"http://{browse_host(host)}:{port}"
    rows = [("Интерфейс оператора:", url),
            ("Документация API:", url + "/docs"),
            ("Остановить сервер:", "Ctrl+C")]
    label_w = max(len(label) for label, _ in rows)
    value_w = max(len(value) for _, value in rows)
    inner = 2 + label_w + 2 + value_w + 2
    print("")
    print("  ┌" + "─" * inner + "┐")
    for label, value in rows:
        print(f"  │  {label:<{label_w}}  {value:<{value_w}}  │")
    print("  └" + "─" * inner + "┘")
    if host == ALL_INTERFACES:
        print(f"  Внимание: сервер слушает {ALL_INTERFACES}, API доступен всем в локальной сети.")
        print(f"  Только для этой машины запускайте без --host и без {HOST_ENV}.")
    elif host != DEFAULT_HOST:
        print(f"  Сервер слушает адрес {host}.")
    print("")
    if open_browser:
        threading.Thread(target=open_browser_when_ready, args=(url, host, port), daemon=True).start()
    try:
        uvicorn.run("kriokontur.api:app", host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    print("\nсервер остановлен")


def main() -> None:
    _setup_console()
    ap = argparse.ArgumentParser(description="Криоконтур: запуск расчётного ядра и интерфейса")
    ap.add_argument("--port", type=int, default=8000, help="с какого порта начинать подбор")
    ap.add_argument("--skip-tests", action="store_true", help="не прогонять тесты перед запуском")
    ap.add_argument("--check", action="store_true", help="только проверка, без сервера")
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    ap.add_argument("--host", default=None,
                    help=f"адрес сервера: по умолчанию {DEFAULT_HOST}, {ALL_INTERFACES} открывает API "
                         f"для локальной сети; можно задать переменной {HOST_ENV}")
    args = ap.parse_args()
    ensure_python()
    ensure_packages()
    if not args.skip_tests:
        run_tests()
    quick_check()
    if args.check:
        print("\nпроверка окончена, сервер не запускался")
        return
    host = resolve_host(args.host)
    serve(host, pick_port(host, args.port), open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
