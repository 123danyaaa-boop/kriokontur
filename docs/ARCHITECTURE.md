# Архитектура: что откуда подгружается

## Поток данных

```
data/*.csv                 CASE_INPUT организатора, только чтение
configs/scenarios/*.yaml   BASE и MANDATORY_STRESS организатора + наши проверки чувствительности
configs/plans/*.json       TEAM_DECISION в переносимом конверте plan.schema.json
        |
        v
caseinput.load_case()  ->  CaseInput (+ версия набора sha256)
scenarios.load_all()   ->  Scenario (множители спроса, цен, долей поставки, потолок потерь)
plan.Plan.load()       ->  Plan (резервирование, заказы, инвестиции, политика запаса, допущения)
        |
        v
engine.run(case, scenario, plan)
   availability()   когда канал реально доступен
   диспетчер        сколько заказываем в каждом месяце
   баланс           I_end = I_start + Q_delivered - Losses - Q_served
   costs()          закупка, take-or-pay, резервирование, хранение, OPEX, CAPEX, дисконт
   checks.run_checks()  нарушения с кодом, годом, величиной и причиной
        |
        v
RunResult  ->  api (JSON для фронтенда)
           ->  export (CSV и XLSX одним и тем же расчётом)
           ->  db (планы, прогоны, годовые и помесячные строки, нарушения)
```

Ключевое правило: интерфейс, выгрузка и база берут числа из одного и того же `RunResult`.
Ни фронтенд, ни Excel ничего не пересчитывают самостоятельно.

## Модули

| Файл | Отвечает за | Не отвечает за |
|---|---|---|
| `rules.py` | элементарные формулы кейса, чистые функции | состояние, загрузку данных |
| `caseinput.py` | чтение CSV, датаклассы, версия набора | изменение значений организатора |
| `scenarios.py` | множители сценария и потолок потерь | стратегию закупок |
| `plan.py` | решения команды, конверт организатора, допущения | расчёт |
| `engine.py` | помесячный прогон и деньги | проверку ограничений |
| `checks.py` | нарушения по constraints.csv и по структуре плана | починку плана |
| `planner.py` | эвристический стартовый план | оптимизацию |
| `export.py` | CSV и XLSX | собственные вычисления |
| `db.py` | SQLite: планы, прогоны, результаты | бизнес-логику |
| `api.py` | HTTP для фронтенда | расчёт |
| `cli.py` | запуск из терминала | расчёт |

## Как HTML-прототип ложится на Python

Прототип на одной странице (`kriokontur.html`) содержал движок на JavaScript. Теперь он
становится клиентом, а расчёт переезжает в Python. Соответствие один к одному:

| Экран и элемент прототипа | Что вызывает | Что показывает |
|---|---|---|
| Таблица «План», ячейка канал × год | `POST /api/run` с обновлённым конвертом | `years[].ordered_t`, `delivered_t` |
| Переключатель сценария | `POST /api/compare` | два столбца BASE и стресс |
| Панель «Инвестиции» | поле `decisions.investments` | `CAPEX по годам`, лимиты |
| Панель «Итог» | `totals` | PV, расходы, SL, дефицит, потери |
| Экран «Проверки» | `violations` | код, год, величина, причина |
| Экран «Каналы» | `years[].delivered_t` по каналам | толщина потоков |
| Таймлайн и сцена | `months[]` | запас, ёмкость, дефицит по месяцам |
| Кнопка «Выгрузить CSV» | `POST /api/export/csv` | тот же расчёт, что на экране |
| Кнопки «Сохранить» и «Открыть» | `POST /api/plans`, `GET /api/plans/{id}` | план из базы |
| Экран «Контрольные тесты» | `GET /api/selftest` | результат pytest V01–V10 |

Что это даёт: числа перестают жить в браузере, план сохраняется в базе, выгрузка и интерфейс
гарантированно совпадают, а проверить ядро можно без интерфейса, из терминала.

## Запуск

```bash
pip install -r requirements.txt
python -m pytest tests -q                                   # контрольные примеры и инварианты
PYTHONPATH=src python -m kriokontur.cli autoplan --scenario MANDATORY_STRESS --out configs/plans/robust.json
PYTHONPATH=src python -m kriokontur.cli run --plan configs/plans/robust.json --scenario MANDATORY_STRESS
PYTHONPATH=src python -m kriokontur.cli compare --plan configs/plans/robust.json
PYTHONPATH=src python -m kriokontur.cli export --plan configs/plans/robust.json --scenario BASE --out results
PYTHONPATH=src uvicorn kriokontur.api:app --reload --port 8000
```
