# Криоконтур: цифровой контур топливного узла 2035–2040

Решение кейса «Топливный космоконтур 2035» (КосмоХакатон 2026). Расчётное ядро на Python,
контрольные данные организатора без правок, проверки ограничений, тесты, база данных,
выгрузка CSV и XLSX, HTTP-интерфейс для фронтенда.

## Быстрый старт

### Docker (рекомендовано)

```bash
docker compose up -d
```

Приложение запуститься и будет доступно на http://localhost:8000.

### Скрипт / Python

```bash
python run.py                 # проверит окружение, прогонит тесты, поднимет сервер и откроет браузер
```

Без терминала: двойной клик по  `start.bat` (Windows), `start.command` (macOS) или `start.sh` (Linux).

Скрипт сам подбирает свободный порт из диапазона 8000–8010 и открывает интерфейс в браузере,
когда сервер готов. Ключи: `--check`, `--skip-tests`, `--port`, `--no-browser`.

Проект работает из любой рабочей директории: `python ~/kriokontur/run.py --check` посчитает
то же самое. Корень ищется по маркеру или берётся из переменной `KRIOKONTUR_HOME`,
все пути выводятся из него в `src/kriokontur/paths.py`, и наружу проект ничего не пишет.

Подробная инструкция со скриншотами команд: `docs/QUICKSTART.md`.

## Вручную

```bash
pip install -r requirements.txt
python -m pytest tests -q                 # 46 тестов, включая контрольные примеры V01-V10
PYTHONPATH=src python scripts/compare_strategies.py
PYTHONPATH=src python -m kriokontur.cli run --plan configs/plans/robust.json --scenario MANDATORY_STRESS
PYTHONPATH=src uvicorn kriokontur.api:app --reload --port 8000
```

## Где что лежит

```
run.py              точка входа
docker-compose.yaml
Dockerfile
start.bat           cкрипт запуска для Windows
start.command       cкрипт запуска для macOS
start.sh            cкрипт запуска для Linux
web/                интерфейс оператора: index.html плюс snapshot.js для автономного демо
data/               CASE_INPUT организатора (копия test_oil/data) + PROVENANCE.md
configs/            сценарии (BASE, MANDATORY_STRESS, LOW/HIGH_DEMAND) и планы команды
src/kriokontur/     расчётное ядро: paths, rules, caseinput, scenarios, plan, engine, checks, planner, export, db, api, cli
db/                 schema.sql и файл SQLite
tests/              контрольные примеры V01-V10 и инварианты движка
results/            выгрузки CSV и XLSX
docs/               QUICKSTART.md, MATH_MODEL.md, SCENARIOS.md, HORIZON.md, ARCHITECTURE.md, DB_SCHEMA.md, WORKLOG.md
scripts/            сравнение стратегий, все сценарии, чувствительность, риски, расширяемость
```

## Порядок проверки для эксперта

1. `python -m pytest tests -q` — контрольные примеры и инварианты.
2. `cli run --plan configs/plans/base-only.json --scenario BASE` — план исполним.
3. `cli run --plan configs/plans/base-only.json --scenario MANDATORY_STRESS` — тот же план
   в обязательном стрессе: нарушения с годом, величиной и причиной.
4. `cli compare --plan configs/plans/robust.json` — четыре сценария на одной базе.
5. `cli export --plan configs/plans/robust.json --scenario MANDATORY_STRESS --out results`
   — CSV и XLSX, числа совпадают с выводом на экране.
6. `python scripts/run_all_scenarios.py --plan configs/plans/final-candidate.json` — все десять
   сценариев, финальный кандидат проходит их без жёстких нарушений.
7. `python scripts/sensitivity_report.py` — торнадо, развёртки и обратный стресс.
8. `python scripts/risk_report.py` — реестр рисков, геополитическое событие, Монте-Карло.
9. `python scripts/extensibility_demo.py` — шестой канал и 2041 год на копии набора.
10. `python scripts/horizon_report.py --method increment` — расчёт на перспективу до 2048 года:
    потолок земной архитектуры, год окупаемости лунной установки, проверка на трёх гипотезах спроса.

## Интерфейс оператора

Всё считает бэкенд, фронтенд только рисует ответы API. В живом режиме работают сегменты
инвестиций, режим резерва, ставка дисконтирования, ввод объёмов в таблице плана, выбор
сценария и плана, автоплан, сохранение и открытие планов, сброс к пресету, выгрузки CSV
и XLSX, чувствительность, риски с Монте-Карло, геополитическое событие и расчёт
на перспективу на трёх гипотезах спроса.

Страница сама ищет API: сначала свой origin, затем `127.0.0.1` на портах 8000–8002.
Если бэкенда нет, интерфейс переходит в режим демо-снимка: наверху висит плашка с причиной,
а каждый элемент, которому нужен пересчёт, выключен и объясняет это всплывающей подсказкой.
Как только бэкенд появится, страница переключится в живой режим сама, без перезагрузки.
Любой ответ 4xx или 5xx показывается видимой плашкой с текстом из поля `message`,
а для 422 ещё и кодом нарушения с годом.
