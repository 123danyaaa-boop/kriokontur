@echo off
rem Криоконтур: запуск двойным кликом на Windows.
rem Кодовая страница 65001 нужна, чтобы русские сообщения читались, а не превращались в кашу.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Не найден Python. Поставьте Python 3.10 или новее с https://www.python.org/downloads/
    echo При установке отметьте галочку "Add python.exe to PATH".
    pause
    exit /b 1
)

python run.py
echo.
echo Сервер остановлен. Окно можно закрыть.
pause
