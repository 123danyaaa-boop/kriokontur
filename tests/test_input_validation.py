"""D-03: проверка входа. Неполный, нечисловой или противоречивый план не считается молча.

До правки API отвечал 500 на строку и null, принимал отрицательный начальный запас,
ставку −50%, срок подготовки Earth-New 6 месяцев, неизвестный канал и год вне горизонта.
"""
import copy
import json
from pathlib import Path

import pytest

from kriokontur.caseinput import load_case
from kriokontur.checks import validate_envelope

ENVELOPE = json.loads(Path("configs/plans/final-candidate.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def case():
    return load_case()


def codes(rows):
    return {v.code for v in rows}


def fields(rows):
    return {v.metric for v in rows}


def with_reservation(source_id, year, value):
    env = copy.deepcopy(ENVELOPE)
    rows = [r for r in env["decisions"]["capacity_reservations"]
            if not (r["source_id"] == source_id and r["year"] == year)]
    rows.append({"source_id": source_id, "year": year, "reserved_t_per_year": value})
    env["decisions"]["capacity_reservations"] = rows
    return env


def test_control_plan_is_accepted(case):
    assert validate_envelope(case, ENVELOPE) == []


@pytest.mark.parametrize("value", [-5, "abc", None, float("nan"), float("inf")])
def test_broken_reservation_value_is_rejected(case, value):
    rows = validate_envelope(case, with_reservation("A", 2036, value))
    assert codes(rows) == {"INPUT_INVALID"}
    assert any(v.period == 2036 and "reserved_t_per_year" in v.metric for v in rows)


def test_unknown_source_is_rejected(case):
    rows = validate_envelope(case, with_reservation("Z", 2036, 50))
    assert any("source_id" in v.metric for v in rows)


def test_year_outside_horizon_is_rejected(case):
    rows = validate_envelope(case, with_reservation("A", 2050, 50))
    assert any(v.period == 2050 and "year" in v.metric for v in rows)


def test_missing_decisions_section_is_rejected(case):
    rows = validate_envelope(case, {"plan_id": "empty"})
    assert codes(rows) == {"INPUT_INVALID"}
    assert fields(rows) == {"decisions"}


def test_negative_opening_stock_is_rejected(case):
    env = copy.deepcopy(ENVELOPE)
    env["decisions"]["inventory_policy"]["opening_stock_t"] = -20
    rows = validate_envelope(case, env)
    assert any("opening_stock_t" in v.metric for v in rows)


def test_opening_stock_above_storage_capacity_is_rejected(case):
    """Начальный запас 150 т при ёмкости базового хранилища 70 т физически невозможен."""
    env = copy.deepcopy(ENVELOPE)
    env["decisions"]["inventory_policy"]["opening_stock_t"] = 150
    rows = validate_envelope(case, env)
    assert any("opening_stock_t" in v.metric for v in rows)
    assert any("70" in v.message for v in rows)


@pytest.mark.parametrize("key,value", [
    ("discount_rate", -0.5),
    ("discount_rate", 0.9),
    ("earth_new_prep_months", 6),       # кейс задаёт 18-24
    ("earth_new_prep_months", 36),
    ("isru_commissioning_lag_months", 0),
    ("emergency_lead_steps", 0),
    ("reserve_safety_factor", 5.0),
])
def test_assumption_outside_disclosed_range_is_rejected(case, key, value):
    env = copy.deepcopy(ENVELOPE)
    env["assumptions"][key] = value
    rows = validate_envelope(case, env)
    assert any(v.metric == f"assumptions.{key}" for v in rows), (key, value, [v.metric for v in rows])


@pytest.mark.parametrize("key,value", [
    ("discount_rate", 0.12),
    ("earth_new_prep_months", 24),
    ("isru_commissioning_lag_months", 1),
])
def test_assumption_inside_disclosed_range_is_accepted(case, key, value):
    env = copy.deepcopy(ENVELOPE)
    env["assumptions"][key] = value
    assert validate_envelope(case, env) == []


def test_unknown_investment_is_rejected(case):
    env = copy.deepcopy(ENVELOPE)
    env["decisions"]["investments"] = [{"investment_id": "MARS_ISRU", "decision_year": 2036}]
    rows = validate_envelope(case, env)
    assert any("investment_id" in v.metric for v in rows)


def test_error_message_names_field_and_year(case):
    rows = validate_envelope(case, with_reservation("A", 2036, -5))
    message = rows[0].message
    assert "field=" in message and "year=2036" in message and "value=" in message
