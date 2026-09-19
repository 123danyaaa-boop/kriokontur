-- Криоконтур: структура данных расчётного контура (SQLite, переносится на Postgres без изменений логики).
-- Три слоя: CASE_INPUT (данные организатора, только чтение), TEAM_DECISION (планы),
-- результаты прогонов (годовые, помесячные, нарушения, выгрузки).

PRAGMA foreign_keys = ON;

-- ------------------------- CASE_INPUT ------------------------------------
CREATE TABLE IF NOT EXISTS case_version (
    case_version TEXT PRIMARY KEY,              -- sha256 по data/*.csv, первые 16 символов
    loaded_at    TEXT NOT NULL,
    source       TEXT NOT NULL,                 -- откуда пришли файлы
    note         TEXT
);

CREATE TABLE IF NOT EXISTS demand (
    case_version    TEXT NOT NULL REFERENCES case_version(case_version),
    year            INTEGER NOT NULL,
    base_total_t    REAL NOT NULL,
    base_critical_t REAL NOT NULL,
    low_total_t     REAL NOT NULL,
    high_total_t    REAL NOT NULL,
    PRIMARY KEY (case_version, year)
);

CREATE TABLE IF NOT EXISTS supply_source (
    case_version        TEXT NOT NULL REFERENCES case_version(case_version),
    source_id           TEXT NOT NULL,
    name                TEXT NOT NULL,
    capacity_t_per_year REAL NOT NULL,
    variable_cost_mln_per_t REAL NOT NULL,
    reservation_rate    REAL NOT NULL,
    take_or_pay_share   REAL NOT NULL,
    lead_time_min       REAL NOT NULL,
    lead_time_max       REAL NOT NULL,
    lead_time_unit      TEXT NOT NULL,
    reliability_profile TEXT,
    available_from_year INTEGER,
    PRIMARY KEY (case_version, source_id)
);

CREATE TABLE IF NOT EXISTS storage_option (
    case_version TEXT NOT NULL REFERENCES case_version(case_version),
    storage_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    capacity_t   REAL NOT NULL,
    loss_rate_on_throughput REAL NOT NULL,
    holding_cost_mln_per_t_year REAL NOT NULL,
    capex_mln    REAL NOT NULL,
    fixed_opex_mln_per_year REAL NOT NULL,
    available_from_year INTEGER,
    PRIMARY KEY (case_version, storage_id)
);

CREATE TABLE IF NOT EXISTS investment_option (
    case_version      TEXT NOT NULL REFERENCES case_version(case_version),
    investment_id     TEXT NOT NULL,
    name              TEXT NOT NULL,
    option_fee_mln    REAL NOT NULL,
    exercise_cost_mln REAL NOT NULL,
    total_capex_mln   REAL NOT NULL,
    commissioning_rule TEXT,
    fixed_opex_mln_per_year REAL NOT NULL,
    PRIMARY KEY (case_version, investment_id)
);

CREATE TABLE IF NOT EXISTS constraint_rule (
    case_version  TEXT NOT NULL REFERENCES case_version(case_version),
    constraint_id TEXT NOT NULL,
    metric        TEXT NOT NULL,
    operator      TEXT NOT NULL,
    value         REAL NOT NULL,
    unit          TEXT,
    period        TEXT,
    scenario      TEXT,
    severity      TEXT,
    description   TEXT,
    PRIMARY KEY (case_version, constraint_id)
);

CREATE TABLE IF NOT EXISTS scenario (
    scenario_id TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    status      TEXT NOT NULL,                  -- CASE_INPUT | TEAM_SENSITIVITY | TEAM_RESEARCH
    config_yaml TEXT NOT NULL
);

-- ------------------------- TEAM_DECISION ---------------------------------
CREATE TABLE IF NOT EXISTS plan (
    plan_id    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    author     TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    envelope   TEXT NOT NULL                    -- JSON по schemas/plan.schema.json организатора
);

CREATE TABLE IF NOT EXISTS plan_reservation (
    plan_id   TEXT NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    year      INTEGER NOT NULL,
    reserved_t_per_year REAL NOT NULL CHECK (reserved_t_per_year >= 0),
    PRIMARY KEY (plan_id, source_id, year)
);

CREATE TABLE IF NOT EXISTS plan_order (
    plan_id   TEXT NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    year      INTEGER NOT NULL,
    ordered_t REAL CHECK (ordered_t IS NULL OR ordered_t >= 0),
    PRIMARY KEY (plan_id, source_id, year)
);

CREATE TABLE IF NOT EXISTS plan_investment (
    plan_id       TEXT NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    investment_id TEXT NOT NULL,                -- ZBO | LUNAR_ISRU | EARTH_NEW_OPTION | EARTH_NEW_EXERCISE
    decision_year INTEGER,
    PRIMARY KEY (plan_id, investment_id)
);

CREATE TABLE IF NOT EXISTS plan_assumption (
    plan_id TEXT NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    value   TEXT NOT NULL,
    unit    TEXT,
    basis   TEXT,                               -- обоснование или ссылка на источник
    scope   TEXT,                               -- где действует допущение
    PRIMARY KEY (plan_id, name)
);

-- ------------------------- РЕЗУЛЬТАТЫ ------------------------------------
CREATE TABLE IF NOT EXISTS run (
    run_id        TEXT PRIMARY KEY,
    plan_id       TEXT NOT NULL REFERENCES plan(plan_id) ON DELETE CASCADE,
    scenario_id   TEXT NOT NULL REFERENCES scenario(scenario_id),
    case_version  TEXT NOT NULL REFERENCES case_version(case_version),
    engine_version TEXT NOT NULL,
    discount_rate REAL NOT NULL,
    seed          INTEGER,
    created_at    TEXT NOT NULL,
    feasible      INTEGER NOT NULL,
    total_cost_mln REAL NOT NULL,
    discounted_cost_mln REAL NOT NULL,
    sl_total      REAL NOT NULL,
    sl_critical   REAL NOT NULL,
    shortage_t    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS run_year (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    year   INTEGER NOT NULL,
    demand_total_t REAL, demand_critical_t REAL,
    opening_t REAL, delivered_t REAL, losses_t REAL,
    served_t REAL, served_critical_t REAL, shortage_t REAL, closing_t REAL,
    avg_stock_t REAL, max_stock_t REAL, capacity_t REAL, reserve_required_t REAL,
    sl_total REAL, sl_critical REAL, loss_share REAL,
    cost_procurement REAL, cost_reservation REAL, cost_holding REAL,
    cost_fixed_opex REAL, cost_capex REAL, total_cost_mln REAL, discounted_cost_mln REAL,
    PRIMARY KEY (run_id, year)
);

CREATE TABLE IF NOT EXISTS run_source_year (
    run_id    TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    year      INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    reserved_t REAL, ordered_t REAL, delivered_t REAL, payable_t REAL,
    variable_payment_mln REAL, reservation_payment_mln REAL,
    PRIMARY KEY (run_id, year, source_id)
);

CREATE TABLE IF NOT EXISTS run_month (
    run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    month_index INTEGER NOT NULL,
    year INTEGER, month INTEGER,
    opening_t REAL, gross_t REAL, losses_t REAL, served_t REAL,
    shortage_t REAL, closing_t REAL, capacity_t REAL, target_t REAL,
    PRIMARY KEY (run_id, month_index)
);

CREATE TABLE IF NOT EXISTS run_violation (
    run_id   TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    code     TEXT NOT NULL,
    severity TEXT NOT NULL,
    period   TEXT,
    metric   TEXT,
    value    REAL,
    limit_value REAL,
    excess   REAL,
    message  TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

-- ------------------------- РИСКИ И СТОРОНЫ --------------------------------
CREATE TABLE IF NOT EXISTS risk (
    risk_id TEXT PRIMARY KEY,
    plan_id TEXT REFERENCES plan(plan_id) ON DELETE CASCADE,
    event TEXT NOT NULL,
    cause TEXT,
    affected_parameter TEXT,                    -- какой вход модели меняется
    period TEXT,
    probability_basis TEXT,                     -- вероятность, диапазон или сценарное допущение
    scenario_id TEXT,                           -- TEAM_* сценарий, которым риск считается
    physical_consequence_t REAL,
    financial_consequence_mln REAL,
    service_consequence REAL,
    dependencies TEXT,
    owner TEXT,
    mitigation TEXT,
    residual_note TEXT
);

CREATE TABLE IF NOT EXISTS stakeholder (
    stakeholder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    interest TEXT,
    metric TEXT,
    obligation TEXT,
    risk_borne TEXT
);

CREATE TABLE IF NOT EXISTS export (
    export_id  TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    format     TEXT NOT NULL,
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_plan ON run(plan_id, scenario_id);
CREATE INDEX IF NOT EXISTS idx_violation_code ON run_violation(code);
