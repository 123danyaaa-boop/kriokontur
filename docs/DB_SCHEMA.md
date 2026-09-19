# Структура базы данных

SQLite, файл `db/kriokontur.sqlite3`, схема в `db/schema.sql`. Логика не зависит от СУБД:
при переезде на Postgres меняется только строка подключения.

## Три слоя

**CASE_INPUT, только чтение.** `case_version`, `demand`, `supply_source`, `storage_option`,
`investment_option`, `constraint_rule`, `scenario`. Ключ `case_version` это sha256 по файлам
`data/*.csv`. Он попадает в каждый прогон, поэтому всегда видно, на каком наборе считали.

**TEAM_DECISION.** `plan` хранит переносимый конверт организатора целиком, а `plan_reservation`,
`plan_order`, `plan_investment`, `plan_assumption` дают нормализованный вид для запросов и
сравнений. Конверт нужен для повторного открытия, нормализованные таблицы для аналитики.

**Результаты.** `run` (шапка прогона), `run_year` (годовой баланс и деньги), `run_source_year`
(резерв, заказ, поставка и платежи по каналам), `run_month` (помесячный след запаса),
`run_violation` (нарушения), `export` (что и когда выгружали). Отдельно `risk` и `stakeholder`
под реестр рисков и карту сторон.

## Почему так

- Один прогон это строка `run` плюс детали: можно сравнивать BASE и стресс по `plan_id`.
- `run_month` хранит 72 строки на прогон: именно он доказывает, что внутригодового дефицита нет.
- `run_violation` отдельной таблицей: жюри спрашивает «какие ограничения нарушены и когда»,
  на это отвечает один запрос.
- `plan_assumption` рядом с планом: допущение всегда путешествует вместе с числами.
- `risk.affected_parameter` и `risk.scenario_id` связывают реестр рисков с расчётом, а не с текстом.

## Примеры запросов

```sql
-- сравнение сценариев по одному плану
SELECT scenario_id, discounted_cost_mln, sl_total, shortage_t, feasible
FROM run WHERE plan_id = 'robust' ORDER BY created_at DESC;

-- какие ограничения нарушены и в каком году
SELECT code, period, value, limit_value, excess, message
FROM run_violation WHERE run_id = ? ORDER BY period;

-- вклад каналов в закупку за год
SELECT source_id, ordered_t, delivered_t, variable_payment_mln
FROM run_source_year WHERE run_id = ? AND year = 2038 ORDER BY variable_payment_mln DESC;
```
