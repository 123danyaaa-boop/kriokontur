"""Контрольные примеры V01-V10 организатора.

Ожидаемые значения читаются из tests/expected_checks.json, скопированного
из validation/expected_checks.json стартового репозитория, а не зашиты в тест.
"""
import json
from pathlib import Path

import pytest

from kriokontur import rules
from kriokontur.caseinput import load_case
from kriokontur.checks import validate_plan
from kriokontur.plan import Plan
from kriokontur.scenarios import load_all

EXPECTED = {row["case_id"]: row["expected"] for row in
            json.loads((Path(__file__).parent / "expected_checks.json").read_text(encoding="utf-8"))}


def test_v01_material_balance():
    assert rules.closing_inventory(10, 30, 2, 25) == EXPECTED["V01"]["closing_inventory_t"]


def test_v02_shortage_is_not_negative_inventory():
    served = min(10, 0 + 8 - 0)
    closing = rules.closing_inventory(0, 8, 0, served)
    assert served == EXPECTED["V02"]["served_t"]
    assert rules.shortage(10, served) == EXPECTED["V02"]["shortage_t"]
    assert closing == EXPECTED["V02"]["closing_inventory_t"]


def test_v03_take_or_pay_minimum():
    assert rules.payable_volume(50, 0.70, 100) == EXPECTED["V03"]["payable_volume_t"]
    assert rules.variable_payment(2, 50, 0.70, 100) == EXPECTED["V03"]["variable_payment_mln"]


def test_v04_take_or_pay_is_not_charged_twice():
    payment = rules.variable_payment(2, 50, 0.70, 100)
    assert payment == EXPECTED["V04"]["variable_payment_mln"]
    assert payment != 2 * EXPECTED["V04"]["variable_payment_mln"]


def test_v05_reservation_proration():
    assert rules.reservation_payment(0.4, 100, 0.5) == EXPECTED["V05"]["reservation_payment_mln"]


def test_v06_losses_once_on_throughput():
    assert rules.losses_on_throughput(20, 0.05) == EXPECTED["V06"]["losses_t"]


def test_v07_reserve_45_days():
    assert rules.reserve_days_to_tonnes(365) == EXPECTED["V07"]["reserve_t"]


def test_v08_capacity_exceeded():
    """V08 организатора: синтетический канал capacity = 10 т/год, reserved = 12 т/год, excess = 2 т.

    Раньше тест брал реальный канал B (110 против 120) и добавлял к ожиданию +8, чтобы сойтись
    с эталоном. Теперь воспроизводится именно контрольный пример: данные кейса не трогаем,
    а синтетический источник создаём копией записи с мощностью 10 т/год.
    """
    from dataclasses import replace

    case = load_case()
    case.sources = dict(case.sources)
    case.sources["V08"] = replace(case.sources["B"], source_id="V08", name="V08-synthetic",
                                  capacity_t_per_year=10.0)
    plan = Plan(plan_id="v08")
    plan.reservations["V08"] = {2035: 12.0}
    viol = [v for v in validate_plan(case, plan) if v.code == EXPECTED["V08"]["violation"]]
    assert viol, "нарушение CAPACITY_EXCEEDED не сформировано"
    assert viol[0].excess == pytest.approx(EXPECTED["V08"]["excess_t"])
    assert viol[0].period == 2035 and viol[0].limit == 10.0


def test_v09_critical_demand_is_nested():
    case = load_case()
    base = load_all()["BASE"]
    assert base.demand_total(case, 2035) == EXPECTED["V09"]["total_demand_t"]
    assert base.demand_critical(case, 2035) < base.demand_total(case, 2035)


def test_v10_stress_delivery_without_reliability():
    assert rules.actual_delivery(20, 0.5) == EXPECTED["V10"]["actual_delivery_t"]
    assert rules.actual_delivery(20, 0.5) != 20 * 0.5 * 0.8
