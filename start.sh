#!/bin/bash

# Переходим в директорию, где лежит сам скрипт
cd "$(dirname "$0")"

# Проверяем наличие python3 в системе
if ! command -v python3 &> /dev/null; then
    echo "Не найден Python."
    echo "Установите Python 3.10 или новее с помощью вашего пакетного менеджера (например: sudo apt install python3)."
    echo ""
    read -p "Нажмите Enter для выхода..."
    exit 1
fi

# Запуск основного скрипта
python3 run.py

echo ""
echo "Сервер остановлен. Окно можно закрыть."
read -p "Нажмите Enter для закрытия..."
