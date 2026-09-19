"""Криоконтур: расчётное ядро кейса «Топливный космоконтур 2035».

Слои:
    rules      элементарные формулы кейса (проверяются тестами V01-V10)
    caseinput  загрузка CASE_INPUT из data/*.csv
    scenarios  BASE, MANDATORY_STRESS и проверки чувствительности из configs/scenarios/*.yaml
    plan       TEAM_DECISION: резервирование, заказы, инвестиции, политика запаса
    engine     помесячный расчёт: поставки, баланс, деньги
    checks     проверка ограничений из data/constraints.csv
    planner    эвристический автоплан (стартовая точка, не оптимизатор)
    export     выгрузка CSV и XLSX
    db         SQLite: планы, прогоны, результаты, нарушения
    api        HTTP-интерфейс для фронтенда
"""
__version__ = "0.2.0"
ENGINE_VERSION = "kriokontur-engine/0.2.0"
