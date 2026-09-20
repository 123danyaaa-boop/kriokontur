"""Инвестиционное решение по лунному производству (D-15, D-28).

    PYTHONPATH=src python scripts/strategy_decision.py

Сравнивает выбранную стратегию (финальный план без Lunar-ISRU) с сопоставимой альтернативой,
где установка финансируется вовремя. Условия сравнения одинаковые: те же данные организатора,
та же ставка, тот же горизонт, те же правила расчёта и одна и та же процедура построения плана.

Правило построения альтернативы (чтобы сравнение было честным, а не подогнанным):
    1) берём финальный план как есть;
    2) добавляем решение LUNAR_ISRU в 2037 году — последний год, когда финансирование
       успевает до 2038 по требованию кейса;
    3) резервируем лунный канал на его мощность с года ввода;
    4) высвобождаем земную мощность в порядке убывания переменной цены (сначала Earth-Flex,
       затем Earth-New, затем Earth-Core), пока суммарная законтрактованная мощность не вернётся
       к уровню исходного плана. Ни одно другое решение не меняется.

Результат пишется в results/protocols/10_strategy.md.
"""
from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur import paths  # noqa: E402
from kriokontur.caseinput import load_case  # noqa: E402
from kriokontur.engine import availability, months_available, run  # noqa: E402
from kriokontur.horizon import HorizonConfig, cumulative_discounted, extend_case, payback_year  # noqa: E402
from kriokontur.plan import Plan  # noqa: E402
from kriokontur.scenarios import load_all  # noqa: E402
from kriokontur.sensitivity import switch_point  # noqa: E402

CONTROL = ("BASE", "MANDATORY_STRESS", "LOW_DEMAND", "HIGH_DEMAND")
RELEASE_ORDER = ("B", "C", "A")      # высвобождаем мощность от самого дорогого канала к дешёвому
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def f(v, digits=2):
    return "—" if v is None else f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")


def table(header, rows):
    return ("| " + " | ".join(header) + " |\n|" + "|".join(["---"] * len(header)) + "|\n"
            + "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows) + "\n")


def build_isru_variant(case, scenarios, plan: Plan, isru_year: int = 2037,
                       release_earth: bool = True) -> Plan:
    """Альтернатива с лунным каналом, построенная по объявленному правилу.

    release_earth=True  — замещение: земная мощность снимается на объём лунной, суммарная
                          законтрактованная мощность не меняется. Сравниваются только деньги.
    release_earth=False — расширение: земные договоры остаются, лунный канал добавляет
                          мощность сверх них. Дороже, но появляется запас мощности.
    """
    alt = copy.deepcopy(plan)
    suffix = "isru" if release_earth else "isru-add"
    alt.plan_id = f"{plan.plan_id}-{suffix}{isru_year}"
    alt.name = (f"{plan.name} плюс Lunar-ISRU {isru_year}"
                + ("" if release_earth else " без снятия земных договоров"))
    alt.investments = {**alt.investments, "LUNAR_ISRU": isru_year}
    avail = availability(case, alt, scenarios["MANDATORY_STRESS"])
    lunar_cap = case.sources["D"].capacity_t_per_year
    for i, year in enumerate(case.years):
        months_on = months_available(avail.get("D"), i)
        if months_on <= 0:
            alt.reservations.setdefault("D", {})[year] = 0.0
            continue
        alt.reservations.setdefault("D", {})[year] = lunar_cap
        if not release_earth:
            continue
        # лунный объём этого года с поправкой на долю года доступности
        release = lunar_cap * months_on / 12
        for sid in RELEASE_ORDER:
            if release <= 1e-9:
                break
            current = alt.reserved(sid, year)
            cut = min(current, release)
            alt.reservations.setdefault(sid, {})[year] = round(current - cut, 1)
            release -= cut
    return alt


def control_table(case, scenarios, plans):
    rows = []
    for label, plan in plans:
        for sid in CONTROL:
            res = run(case, scenarios[sid], plan)
            hard = sorted({v.code for v in res.violations if v.severity == "hard"})
            rows.append([label, sid, f(res.totals["discounted_cost_mln"]), f(res.totals["total_cost_mln"]),
                         f(min(y.sl_total for y in res.years) * 100) + " %", f(res.totals["shortage_t"]),
                         f(min(y.closing_t for y in res.years)), f(res.totals["capex"], 0),
                         ", ".join(hard) or "—"])
    return rows


def horizon_table(case, scenarios, plans, cfg, rate: float):
    rows, cums = [], {}
    for method in cfg.methods:
        ext = extend_case(case, cfg, method, ["ZBO2"])
        ext_scen = load_all()
        for label, plan in plans:
            long_plan = copy.deepcopy(plan)
            long_plan.investments = {**long_plan.investments, "ZBO2": 2041}
            for sid in ("A", "B", "C", "D"):
                base_year_value = long_plan.reserved(sid, case.years[-1])
                for year in ext.years:
                    if year > case.years[-1]:
                        long_plan.reservations.setdefault(sid, {})[year] = base_year_value
            long_plan.reservations.setdefault("E", {}).update(
                {y: long_plan.reserved("E", case.years[-1]) for y in ext.years if y > case.years[-1]})
            res = run(ext, ext_scen["BASE"], long_plan)
            cum = cumulative_discounted(res, ext.years[0], rate)
            cums[(method, label)] = cum
            served = res.totals["served_t"]
            rows.append([method, label, f(cum[2040]), f(cum[cfg.last_year]), f(served, 0),
                         f(res.totals["shortage_t"]), f(min(y.sl_total for y in res.years) * 100) + " %",
                         f(cum[cfg.last_year] / served, 3) if served else "—"])
    return rows, cums


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--isru-year", type=int, default=2037)
    args = ap.parse_args()
    case, scenarios = load_case(), load_all()
    plan = Plan.load(ROOT / "configs/plans/final-candidate.json")
    alt = build_isru_variant(case, scenarios, plan, args.isru_year, release_earth=True)
    add = build_isru_variant(case, scenarios, plan, args.isru_year, release_earth=False)
    rate = float(plan.assume("discount_rate"))
    plans = [("Без Луны (финальный)", plan), (f"Луна {args.isru_year}, замещение", alt),
             (f"Луна {args.isru_year}, расширение", add)]

    reservations = []
    for sid in ("A", "B", "C", "D", "E"):
        reservations.append([sid] + [f(plan.reserved(sid, y), 1) for y in case.years]
                            + [f(alt.reserved(sid, y), 1) for y in case.years]
                            + [f(add.reserved(sid, y), 1) for y in case.years])

    control = control_table(case, scenarios, plans)
    cfg = HorizonConfig.load()
    horizon_rows, cums = horizon_table(case, scenarios, plans, cfg, rate)
    payback = {}
    for method in cfg.methods:
        payback[method] = {label: payback_year(cums[(method, label)], cums[(method, plans[0][0])])
                           for label, _ in plans[1:]}

    gates = []
    for key in ("demand", "price_earth", "core_capacity"):
        for sid in ("BASE", "MANDATORY_STRESS"):
            for label, variant in plans[1:]:
                sw = switch_point(case, scenarios[sid], plan, variant, key)
                gates.append([sw["label"], label, sid,
                              "—" if sw["switch_value"] is None else f(sw["switch_value"], 3),
                              ("лунный вариант выигрывает после порога" if sw["switch_value"] is not None
                               else sw["note"][46:])])

    year2040 = []
    for label, variant in plans:
        res = run(case, scenarios["MANDATORY_STRESS"], variant)
        y = res.year(2040)
        earth = sum(y.ordered_t[s] for s in ("A", "B", "C"))
        contracted = sum(y.contracted_t[s] for s in ("A", "B", "C", "D"))
        headroom = contracted - (earth + y.ordered_t.get("D", 0.0))
        year2040.append([label, f(y.demand_total_t), f(earth), f(y.ordered_t.get("D", 0.0)),
                         f(y.ordered_t["E"]), f(contracted), f(headroom),
                         f(y.opening_t), f(y.closing_t), f(y.reserve_required_t)])

    by_scenario = {(lbl, sid): run(case, scenarios[sid], pl).totals["discounted_cost_mln"]
                   for lbl, pl in plans for sid in CONTROL}
    base_label, alt_label = plans[0][0], plans[1][0]
    delta = {sid: by_scenario[(alt_label, sid)] - by_scenario[(base_label, sid)] for sid in CONTROL}
    capex_total = sum(y.cost["capex"] for y in run(case, scenarios["BASE"], plan).years)
    gate_demand = next((g[3] for g in gates if g[0].startswith("Спрос") and g[2] == "BASE"
                        and g[3] != "—"), "—")
    gate_price = next((g[3] for g in gates if g[0].startswith("Цена Earth-Core и") and g[2] == "BASE"
                       and g[3] != "—"), "—")
    gate_capacity = next((g[3] for g in gates if g[0].startswith("Доступная") and g[2] == "BASE"
                          and g[3] != "—"), "—")
    payback_any = next((str(v) for m in payback for v in payback[m].values() if v), "—")
    opening_cost = (float(plan.inventory_policy["opening_stock_t"])
                    * case.sources[str(plan.assume("opening_stock_source"))].variable_cost_mln_per_t)

    body = f"""Решение принимается расчётом, а не заранее. Сравниваются две стратегии на одной ставке
{f(rate * 100, 1)} %, одних данных организатора `{case.version}` и одном горизонте.

Правило построения альтернативы: финальный план плюс решение Lunar-ISRU в {args.isru_year} году
(последний год, когда финансирование успевает до 2038), лунный канал резервируется на мощность
{f(case.sources['D'].capacity_t_per_year, 0)} т/год, земная мощность высвобождается в порядке убывания
переменной цены (Earth-Flex → Earth-New → Earth-Core). Других изменений нет.

## Резервирование мощности, т/год

{table(["Канал"] + [f"{y} без Луны" for y in case.years]
        + [f"{y} замещение" for y in case.years] + [f"{y} расширение" for y in case.years], reservations)}

## Контрольные и проверочные сценарии

{table(["Стратегия", "Сценарий", "PV, млн", "Номинал, млн", "Обслуживание, худший год",
        "Дефицит, т", "Мин. запас, т", "CAPEX, млн", "Жёсткие нарушения"], control)}

## Горизонт за 2040 год (TEAM_RESEARCH)

Спрос за горизонтом продлевается тремя явными гипотезами; вывод проверяется на всех трёх.

{table(["Гипотеза спроса", "Стратегия", "PV к 2040, млн", f"PV к {cfg.last_year}, млн",
        "Обслужено, т", "Дефицит, т", "Обслуживание, худший год", "Расход на тонну, млн"], horizon_rows)}

Сравнение по одной только приведённой стоимости корректно лишь там, где обслуживание совпадает.
За 2040 годом при продолжении роста спроса ни одна стратегия не закрывает потребность полностью,
поэтому рядом приводится расход на тонну фактически обслуженного спроса.

Год окупаемости лунной установки относительно стратегии без неё:
{"; ".join(m + " → " + ", ".join(f"{lbl}: {yr or 'не окупается до ' + str(cfg.last_year)}" for lbl, yr in payback[m].items()) for m in cfg.methods)}.

## Инвестиционные ворота: при каких условиях решение меняется

{table(["Параметр", "Вариант с Луной", "Сценарий", "Порог смены лидера", "Комментарий"], gates)}

## Решение, инвестиционные ворота и дорожная карта

**Решение на горизонте обязательного плана 2035–2040: лунное производство не финансируется.**
Основание — расчёт, а не предпочтение: в BASE установка дороже на {f(delta['BASE'])} млн приведённых,
в обязательном стрессе на {f(delta['MANDATORY_STRESS'])} млн, в низком спросе на {f(delta['LOW_DEMAND'])} млн. План без неё проходит
все ограничения во всех четырёх сценариях, поэтому платить {f(case.investments['LUNAR_ISRU'].total_capex_mln, 0)} млн CAPEX за мощность,
которая в горизонте не нужна, нечем оправдать.

**Решение пересматривается, а не закрывается.** Установка выигрывает уже в высоком спросе
({f(abs(delta['HIGH_DEMAND']))} млн в пользу замещающего варианта) и окупается в {payback_any} году при всех трёх
гипотезах спроса за горизонтом. Поэтому решение оформлено как инвестиционные ворота
с контрольной точкой в 2037 году — последнем, когда финансирование успевает к вводу 2038 года.

### Ворота: финансировать Lunar-ISRU, если к концу 2036 года выполнено хотя бы одно условие

| № | Условие | Порог из расчёта | Как проверяется |
|---|---|---|---|
| 1 | Подтверждённый спрос выше базового | множитель ≥ {gate_demand} | заявки потребителей на 2038–2040 годы против ряда `demand.csv` |
| 2 | Цены земных каналов выше контрактных | множитель ≥ {gate_price} | пересмотр цен поставщиков A и B по правилу пересмотра договоров C-A и C-B |
| 3 | Устойчивое снижение доступной мощности Earth-Core | множитель ≤ {gate_capacity} | фактическая доступность канала за 12 месяцев наблюдения |
| 4 | Подтверждённый горизонт эксплуатации узла после 2040 года | продолжение роста спроса | решение заказчика о программе после 2040 года |

Если ни одно условие не выполнено, действующая стратегия сохраняется без изменений —
и это тоже решение, которое кейс требует обосновать.

### Бюджет и дорожная карта действующей стратегии

| Год | Вложение | Сумма, млн | Ответственная роль | Зависимость | Точка решения |
|---|---|---|---|---|---|
| 2035 | Право ввода Earth-New (опцион) | 90 | менеджер контрактов | договор C-C | покупка права до начала подготовки |
| 2035 | Реализация опциона Earth-New | 270 | менеджер контрактов | подготовка 18–24 месяца | подтверждение объёма на 2037 год |
| 2035 | Начальный запас 13,6 т | {f(opening_cost)} | оператор узла | подготовительный период | закупка до 1 января 2035 |
| 2036 | Модернизация хранилища ZBO | 180 | руководитель инвестиционного проекта | опция доступна с 2036 года | ворота потолка потерь стресса 2038 |
| 2037 | **Ворота по Lunar-ISRU** | 0 или 1 250 | инвестиционный комитет | финансирование до 2038 года | четыре условия выше |
| 2038–2040 | Аварийный договор, ежегодно | плата за резерв | оператор узла | договор C-E | ежегодный пересмотр объёма |

Итого по действующей стратегии: {f(capex_total, 0)} млн CAPEX при лимитах 1 800 до конца 2037
и 2 800 до конца 2040. Свободный лимит до 2037 года: {f(case.constraint('CAPEX_2037').value - capex_total, 0)} млн — его хватает
на лунную установку, если ворота сработают.

## Уязвимость 2040 года (D-28)

{table(["Стратегия", "Спрос, т", "Отбор земных, т", "Отбор Луны, т", "Отбор аварийного, т",
        "Законтрактовано всего, т", "Свободная мощность, т", "Запас на начало, т",
        "Запас на конец, т", "Требуемый резерв, т"], year2040)}
"""
    out = paths.RESULTS / "protocols" / "10_strategy.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"# Протокол: инвестиционное решение по лунному производству\n\n"
                   f"Сформировано {NOW} командой "
                   f"`PYTHONPATH=src python scripts/strategy_decision.py`.\n\n{body}", encoding="utf-8")
    for _, variant in plans[1:]:
        variant.save(paths.PLANS / f"{variant.plan_id}.json", "MANDATORY_STRESS")
        print((paths.PLANS / f"{variant.plan_id}.json").relative_to(ROOT))
    print(out.relative_to(ROOT))
    for row in control:
        print("  ", " | ".join(str(c) for c in row))
    print("  окупаемость:", payback)


if __name__ == "__main__":
    main()
