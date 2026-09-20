"""D-19: документация не может разойтись с продуктом незаметно.

Числа в README генерируются скриптом `scripts/sync_docs.py` из фактических прогонов.
Этот тест запускает тот же скрипт в режиме проверки: если документация устарела, тест падает.
Отдельно проверяется, что в документах не осталось утверждений, снятых аудитом.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]


def test_key_numbers_in_docs_match_the_model():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/sync_docs.py"), "--check"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, (
        "числа в документации разошлись с расчётом, обновите их командой "
        "`PYTHONPATH=src python scripts/sync_docs.py`:\n" + proc.stdout + proc.stderr)


def test_readme_has_generated_blocks():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("case-version", "final-plan", "test-count"):
        assert f"KEY_NUMBERS:начало {name}" in text, name
        assert f"KEY_NUMBERS:конец {name}" in text, name


# Историческая ссылка вида «было 8 885,96 → стало 8 918,71» допустима и нужна: она объясняет,
# почему числа изменились. Запрещено только выдавать старое число за действующее.
HISTORY_MARKERS = ("→", "->", "было", "до исправления", "прежн")


@pytest.mark.parametrize("phrase", [
    "8 885,96", "8 886", "8886",                  # PV BASE до исправления платы за хранение
    "10 239,33", "10 239", "10239",               # PV стресса до того же исправления
    "f7d7e963",                                   # отпечаток набора до нормализации CRLF
])
def test_no_superseded_numbers_presented_as_current(phrase):
    """Старые числа аудита не должны стоять в документации как действующие."""
    for path in DOCS:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if phrase not in line:
                continue
            assert any(m in line.lower() for m in HISTORY_MARKERS), (
                f"{path.name}:{i}: устаревшее число «{phrase}» подано как действующее: {line.strip()}")


def test_no_unsubstantiated_cross_check_claim():
    """Утверждение «расхождение с JS-прототипом меньше 0,1%» снято: артефакта сверки нет."""
    pattern = re.compile(r"(javascript|js)[^.]{0,200}?0[,.]1\s?%", re.I | re.S)
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, (
            f"{path.name}: утверждение о сверке с JavaScript без артефакта: {match.group(0)[:120]}")


def test_assumption_table_matches_defaults():
    """Таблица допущений в MATH_MODEL обязана совпадать со значениями по умолчанию в коде."""
    sys.path.insert(0, str(ROOT / "src"))
    from kriokontur.plan import DEFAULT_ASSUMPTIONS

    text = (ROOT / "docs/MATH_MODEL.md").read_text(encoding="utf-8")
    checks = {
        "discount_rate": "0,08",
        "reserve_safety_factor": "1,15",
        "reserve_ramp_months": "9",
        "emergency_base_share_threshold": "0,10",
        "storage_commissioning_lag_months": "0",
    }
    for key, printed in checks.items():
        row = next((line for line in text.splitlines() if f"`{key}`" in line), None)
        assert row, f"{key}: строки нет в таблице допущений"
        assert printed in row, f"{key}: в документе не {printed}, а «{row.strip()}»"
        assert key in DEFAULT_ASSUMPTIONS


def test_every_documented_endpoint_exists():
    """Список эндпоинтов в документации не должен обещать несуществующее."""
    sys.path.insert(0, str(ROOT / "src"))
    from kriokontur.api import app

    real = {getattr(r, "path", "") for r in app.routes}
    text = "\n".join(p.read_text(encoding="utf-8") for p in DOCS)
    documented = set(re.findall(r"`?(/api/[a-z0-9\-/{}_]+)`?", text))
    missing = {p for p in documented if p not in real and "{" not in p}
    assert not missing, f"в документации упомянуты несуществующие эндпоинты: {sorted(missing)}"
