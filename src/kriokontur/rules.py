"""Элементарные правила кейса. Только чистые функции, никакого состояния.

Каждая функция соответствует пункту docs/CALCULATION_RULES.md стартового
репозитория организатора и проверяется контрольным примером V01-V10.
"""
from __future__ import annotations

DAYS_IN_YEAR = 365  # CASE_INPUT: организатор считает учебный год равным 365 дням
MONTHS_IN_YEAR = 12


def months_available(avail_month, year_index: int, months_in_year: int = MONTHS_IN_YEAR) -> int:
    """Сколько месяцев года канал доступен: база для period_fraction в плате за резерв,
    в проверке договорного объёма и в пропорции постоянного OPEX. Единая формула для
    движка, проверок и выгрузки, чтобы доля года считалась в одном месте."""
    if avail_month is None:
        return 0
    start, end = year_index * months_in_year, (year_index + 1) * months_in_year
    return int(max(0, end - max(start, avail_month)))


def closing_inventory(opening_t: float, delivered_t: float, losses_t: float, served_t: float) -> float:
    """V01. I_end = I_start + Q_delivered - Losses - Q_served (тонны)."""
    return opening_t + delivered_t - losses_t - served_t


def shortage(demand_t: float, served_t: float) -> float:
    """V02. Shortage = max(0, Demand - Q_served). Отрицательный запас не используется."""
    return max(0.0, demand_t - served_t)


def losses_on_throughput(gross_inflow_t: float, loss_rate: float) -> float:
    """V06. Losses = Throughput * loss_rate, начисляется один раз на валовое поступление."""
    return gross_inflow_t * loss_rate


def payable_volume(ordered_t: float, take_or_pay_share: float, reserved_period_t: float) -> float:
    """V03. Q_pay = max(Q_order, take_or_pay_share * Q_reserved_period)."""
    return max(ordered_t, take_or_pay_share * reserved_period_t)


def variable_payment(price_mln_per_t: float, ordered_t: float, take_or_pay_share: float,
                     reserved_period_t: float) -> float:
    """V03/V04. VariablePayment = price * Q_pay. Минимум ToP уже внутри max(), второй раз не добавляем."""
    return price_mln_per_t * payable_volume(ordered_t, take_or_pay_share, reserved_period_t)


def reservation_payment(reservation_rate: float, annual_reserved_t_per_year: float,
                        period_fraction: float) -> float:
    """V05. ReservationPayment = rate * annual_reserved_capacity * period_fraction."""
    return reservation_rate * annual_reserved_t_per_year * period_fraction


def reserve_days_to_tonnes(annual_demand_t: float, days: int = 45) -> float:
    """V07. R_y = D_y * days / 365."""
    return annual_demand_t * days / DAYS_IN_YEAR


def service_level(served_t: float, demand_t: float) -> float:
    """SL = served / demand. При нулевом спросе считаем обслуживание полным (граничный случай раскрыт)."""
    if demand_t <= 0:
        return 1.0
    return served_t / demand_t


def actual_delivery(planned_t: float, actual_delivery_share: float) -> float:
    """V10. Фактическая поставка сценария. Повторно на reliability не умножается."""
    return planned_t * actual_delivery_share


def discount(cash_flow: float, year: int, base_year: int, rate: float) -> float:
    """PV_t = CF_t / (1 + r)^(t - t0). Ставка и момент приведения раскрываются в допущениях."""
    return cash_flow / ((1.0 + rate) ** (year - base_year))
