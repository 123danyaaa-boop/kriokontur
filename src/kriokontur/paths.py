"""Единая точка правды по путям проекта.

Раньше каждый модуль считал корень сам через ``Path(__file__).parents[2]``. Это ломалось
при переносе папки, при установке пакета и при запуске из чужой рабочей директории.
Теперь корень ищется один раз и все остальные пути выводятся из него.

Как определяется корень (в порядке приоритета):
    1. переменная окружения ``KRIOKONTUR_HOME``;
    2. подъём вверх от этого файла до папки с маркером;
    3. подъём вверх от текущей рабочей директории до папки с маркером;
    4. запасной вариант ``src/kriokontur/..`` — на случай нестандартной раскладки.

Маркер корня: одновременно файл ``requirements.txt`` и папка ``data``.

Правило проекта: наружу от корня ничего не пишется. Для проверки есть ``inside_home``
и ``ensure_inside_home``; относительные пути разворачиваются от корня, а не от текущей
рабочей директории, поэтому ``python ~/kriokontur/run.py`` работает из любого места.

Чтение чужого набора данных по явно переданному абсолютному пути разрешено: на этом
держится требование расширяемости (копия набора во временной папке в
``scripts/extensibility_demo.py`` и в тестах). Запись наружу — ошибка.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "HOME", "DATA", "CONFIGS", "SCENARIOS", "PLANS", "DB", "RESULTS", "WEB", "DOCS", "SRC", "TESTS",
    "SCHEMA_SQL", "DB_FILE", "HORIZON_CONFIG", "RISKS_CONFIG", "DEFAULT_PLAN",
    "ensure_dirs", "inside_home", "ensure_inside_home", "resolve_for_read", "resolve_for_write",
]


def _is_home(folder: Path) -> bool:
    """Папка похожа на корень проекта, если в ней есть requirements.txt и папка data."""
    return (folder / "requirements.txt").is_file() and (folder / "data").is_dir()


def _find_home() -> Path:
    env = os.environ.get("KRIOKONTUR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for folder in here.parents:
        if _is_home(folder):
            return folder
    cwd = Path.cwd().resolve()
    for folder in (cwd, *cwd.parents):
        if _is_home(folder):
            return folder
    return here.parents[2]


# --------------------------------------------------------------------------- #
# корень и все папки проекта
# --------------------------------------------------------------------------- #
HOME: Path = _find_home()

SRC: Path = HOME / "src"
DATA: Path = HOME / "data"
CONFIGS: Path = HOME / "configs"
SCENARIOS: Path = CONFIGS / "scenarios"
PLANS: Path = CONFIGS / "plans"
DB: Path = HOME / "db"
RESULTS: Path = HOME / "results"
WEB: Path = HOME / "web"
DOCS: Path = HOME / "docs"
TESTS: Path = HOME / "tests"

# отдельные файлы, к которым обращаются модули
SCHEMA_SQL: Path = DB / "schema.sql"
DB_FILE: Path = DB / "kriokontur.sqlite3"
HORIZON_CONFIG: Path = CONFIGS / "horizon.yaml"
RISKS_CONFIG: Path = CONFIGS / "risks.yaml"
DEFAULT_PLAN: Path = PLANS / "final-candidate.json"

# папки, которые создаются сами: остальные приходят вместе с проектом
_WRITABLE = (DB, RESULTS)


def ensure_dirs() -> None:
    """Создаёт недостающие папки, в которые проект пишет."""
    for folder in _WRITABLE:
        folder.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# охрана границ корня
# --------------------------------------------------------------------------- #
def inside_home(path: Path | str) -> bool:
    """Лежит ли путь внутри корня проекта."""
    try:
        Path(path).expanduser().resolve().relative_to(HOME)
    except ValueError:
        return False
    return True


def ensure_inside_home(path: Path | str, what: str = "файл") -> Path:
    """Проверяет, что запись не уходит за пределы корня, и возвращает абсолютный путь."""
    resolved = Path(path).expanduser().resolve()
    if not inside_home(resolved):
        raise ValueError(
            f"{what} пишется за пределы рабочей папки проекта: {resolved}. "
            f"Разрешена только запись внутрь {HOME}"
        )
    return resolved


def resolve_for_read(path: Path | str) -> Path:
    """Путь для чтения.

    Абсолютный берётся как есть (так читается копия набора во временной папке).
    Относительный сначала пробуется от текущей директории, а если там ничего нет —
    разворачивается от корня проекта.
    """
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    if raw.exists():
        return raw.resolve()
    return (HOME / raw).resolve()


def resolve_for_write(path: Path | str, what: str = "файл") -> Path:
    """Путь для записи: относительный разворачивается от корня, выход наружу запрещён."""
    raw = Path(path).expanduser()
    target = raw if raw.is_absolute() else HOME / raw
    return ensure_inside_home(target, what)
