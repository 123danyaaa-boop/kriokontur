#!/bin/bash
# Криоконтур: запуск двойным кликом на macOS.
# Переходим в папку самого скрипта, чтобы запуск не зависел от того, откуда его кликнули.
cd "$(dirname "$0")" || exit 1

# Если в папке есть виртуальное окружение проекта, запускаем его интерпретатором:
# в нём стоят закреплённые версии библиотек, системный Python не трогаем.
if [ -x ".venv/bin/python" ]; then
    PY=.venv/bin/python
    echo "Запуск через виртуальное окружение .venv"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Не найден Python. Поставьте Python 3.10 или новее с https://www.python.org/downloads/"
    echo "Нажмите Enter, чтобы закрыть окно."
    read -r _
    exit 1
fi

"$PY" run.py
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo ""
    echo "Запуск завершился с ошибкой (код $STATUS). Текст ошибки выше."
    echo "Нажмите Enter, чтобы закрыть окно."
    read -r _
fi
