@echo off
rem Криоконтур: запуск двойным кликом на Windows.
rem Кодовая страница 65001 нужна, чтобы русские сообщения читались, а не превращались в кашу.
chcp 65001 >nul
cd /d "%~dp0"

rem Если в папке есть виртуальное окружение проекта, запускаем его интерпретатором:
rem в нём стоят закреплённые версии библиотек, системный Python не трогаем.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo Запуск через виртуальное окружение .venv
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Не найден Python. Поставьте Python 3.10 или новее с https://www.python.org/downloads/
        echo При установке отметьте галочку "Add python.exe to PATH".
        pause
        exit /b 1
    )
    set "PY=python"
)

"%PY%" run.py
echo.
echo Сервер остановлен. Окно можно закрыть.
pause
