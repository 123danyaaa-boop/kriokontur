"""Протоколы контрольных и стресс-тестов одной командой (D-20).

    PYTHONPATH=src python scripts/build_protocols.py

Складывает в results/protocols/ полный пакет для жюри:

    00_environment.md          окружение, версии, отпечаток набора данных
    01_control_cases.md        контрольные примеры V01-V10 и вывод pytest
    02_scenario_protocol.md    прогон финального плана по всем сценариям
    03_stress_protocol.md      протокол обязательного стресса и разложение эффекта
    04_sensitivity.md          обоснованные диапазоны, пороги, двухфакторная сетка
    05_risks.md                реестр рисков, меры, остаточный риск, Монте-Карло с ДИ
    06_dispatch.md             решения до и после наблюдения, цена информации
    07_contracts.md            договорные обязательства и договорный эквивалент резерва
    08_stakeholders.md         последствия для сторон в сценариях и рисках
    <plan>_<scenario>.csv/.xlsx выгрузки по обоим контрольным сценариям

Всё считается тем же движком, что и интерфейс: протокол не пересчитывает ничего по-своему.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur import ENGINE_VERSION, export, paths  # noqa: E402
from kriokontur.caseinput import load_case  # noqa: E402
from kriokontur.contracts import (contract_card, emergency_reserve_check, load_contracts,  # noqa: E402
                                  obligations, obligations_totals)
from kriokontur.engine import run  # noqa: E402
from kriokontur.plan import Plan  # noqa: E402
from kriokontur.reporting import (dispatch_comparison, export_sections, kpi_rows,  # noqa: E402
                                  scenario_comparison, stress_decomposition)
from kriokontur.risks import (evaluate_mitigations, evaluate_risks, load_mitigations,  # noqa: E402
                              load_risks, monte_carlo)
from kriokontur.scenarios import load_all  # noqa: E402
from kriokontur.sensitivity import PARAMS, grid, reverse_stress, switch_point, tornado  # noqa: E402
from kriokontur.stakeholders import impact  # noqa: E402

OUT = paths.RESULTS / "protocols"
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def f(value, digits=2):
    return "—" if value is None else f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def table(header, rows) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out) + "\n"


def write(name: str, title: str, body: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(f"# {title}\n\nСформировано {NOW} командой "
                    f"`PYTHONPATH=src python scripts/build_protocols.py`.\n\n{body}", encoding="utf-8")
    print("  ", path.relative_to(ROOT))
    return path


# --------------------------------------------------------------------------- #
def environment(case) -> None:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=20).stdout.strip()
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        commit = branch = "недоступно"
    rows = [
        ["Отпечаток набора данных (sha256, LF)", f"`{case.version}`"],
        ["Версия движка", f"`{ENGINE_VERSION}`"],
        ["Ветка и коммит", f"`{branch}` / `{commit}`"],
        ["Python", platform.python_version()],
        ["Платформа", f"{platform.system()} {platform.release()}"],
        ["Годы горизонта", f"{case.years[0]}–{case.years[-1]}"],
        ["Каналов снабжения", str(len(case.sources))],
        ["Ограничений в constraints.csv", str(len(case.constraints))],
    ]
    write("00_environment.md", "Протокол: окружение и версия набора",
          "Отпечаток набора считается по контрольным CSV с нормализацией переводов строк, "
          "поэтому совпадает на Windows и Linux.\n\n" + table(["Поле", "Значение"], rows))


def control_cases() -> None:
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"], cwd=ROOT,
                          capture_output=True, text=True)
    tail = "\n".join(proc.stdout.strip().splitlines()[-12:])
    body = ("Контрольные примеры организатора V01–V10 и инварианты движка прогоняются одной "
            "командой `python -m pytest tests -q`. Ожидаемые значения читаются из "
            "`tests/expected_checks.json`, скопированного из `validation/expected_checks.json` "
            "стартового репозитория.\n\n"
            f"Результат: **{'все проверки пройдены' if proc.returncode == 0 else 'есть падения'}**.\n\n"
            "```\n" + tail + "\n```\n")
    write("01_control_cases.md", "Протокол: контрольные примеры и тесты", body)


def scenario_protocol(case, scen, plan) -> None:
    rows = []
    for r in scenario_comparison(case, scen, plan, list(scen)):
        rows.append([r["scenario_id"], scen[r["scenario_id"]].status, f(r["pv_mln"]), f(r["total_mln"]),
                     f(r["sl_total_worst"] * 100) + " %", str(r["sl_total_worst_year"]),
                     f(r["shortage_t"]), f(r["min_closing_t"]), f(r["reserve_margin_min_t"]),
                     str(r["hard_violations"]), r["violation_codes"] or "—"])
    body = ("Цель: показать один и тот же план во всех сценариях контура на единой базе.\n"
            "Условия, которые сохраняются: данные организатора, ставка 8%, база цен 2035, "
            "помесячный шаг, один и тот же план.\n"
            "Критерий нарушения: любое жёсткое нарушение из `data/constraints.csv`.\n\n"
            + table(["Сценарий", "Статус", "PV, млн", "Номинал, млн", "Обслуживание, худший год",
                     "Год", "Дефицит, т", "Мин. запас, т", "Запас над резервом, т",
                     "Жёстких", "Коды"], rows))
    write("02_scenario_protocol.md", f"Протокол: план «{plan.name}» во всех сценариях", body)


def stress_protocol(case, scen, plan) -> None:
    dec = stress_decomposition(case, scen, plan)
    rows = [[c["label"], f(c["delta_pv_mln"]), f(c["delta_shortage_t"]), str(c["hard_violations"])]
            for c in dec["components"]]
    rows.append(["Совместный эффект составляющих", f(dec["interaction_pv_mln"]), "—", "—"])
    rows.append(["**Полный обязательный стресс**", f"**{f(dec['total_delta_pv_mln'])}**",
                 f(dec["delta_shortage_t"]), "—"])
    base = run(case, scen["BASE"], plan)
    stress = run(case, scen["MANDATORY_STRESS"], plan)
    years = [[y.year, f(y.demand_total_t), f(s.demand_total_t), f(y.gross_t), f(s.gross_t),
              f(y.total_cost_mln), f(s.total_cost_mln),
              f(y.sl_total * 100) + " %", f(s.sl_total * 100) + " %"]
             for y, s in zip(base.years, stress.years)]
    body = ("Цель: измерить последствия обязательного стресса и разложить их на составляющие.\n"
            "Параметры стресса и их происхождение: `configs/scenarios/mandatory_stress.yaml`, "
            "статус CASE_INPUT, файл побайтно совпадает с набором организатора.\n"
            "Сохраняемые условия: план, ставка, база цен, шаг расчёта.\n"
            f"Метод разложения: {dec['method']}.\n\n"
            "## Разложение эффекта\n\n"
            + table(["Составляющая", "ΔPV, млн", "Δдефицит, т", "Жёстких нарушений"], rows)
            + "\n## По годам: BASE против обязательного стресса\n\n"
            + table(["Год", "Спрос BASE, т", "Спрос стресс, т", "Поставка BASE, т",
                     "Поставка стресс, т", "Расходы BASE, млн", "Расходы стресс, млн",
                     "SL BASE", "SL стресс"], years))
    write("03_stress_protocol.md", "Протокол обязательного стресс-теста", body)


def sensitivity_protocol(case, scen, plan) -> None:
    ranges = [[p.label, p.unit, f(p.low, 3), f(p.base, 3), f(p.high, 3), p.basis] for p in PARAMS.values()]
    thresholds = [[r["label"], "—" if r["boundary"] is None else f(r["boundary"], 3),
                   r.get("first_violation") or "нет", r["note"]]
                  for r in reverse_stress(case, scen["BASE"], plan, list(PARAMS))]
    torn = [[r["label"], f(r["low_value"], 3), f(r["high_value"], 3), f(r["pv_low"]), f(r["pv_high"]),
             f(r["swing"]), "да" if r["breaks_plan"] else "нет"]
            for r in tornado(case, scen["BASE"], plan)]
    g = grid(case, scen["BASE"], plan, "demand", "core_capacity", steps=5)
    grid_rows = [[f(row[0]["y"], 2)] + ["исполним" if c["feasible"] else "нарушение" for c in row]
                 for row in g["cells"]]
    alt = Plan.load(ROOT / "configs/plans/robust.json")
    switch = [[s["label"], "—" if s["switch_value"] is None else f(s["switch_value"], 3), s["note"]]
              for s in (switch_point(case, scen["MANDATORY_STRESS"], plan, alt, k)
                        for k in ("demand", "price_earth", "core_capacity"))]
    body = ("Цель: показать, при каких условиях план перестаёт быть исполнимым и когда он "
            "уступает альтернативе.\n\n"
            "## Диапазоны параметров и их происхождение\n\n"
            + table(["Параметр", "Ед.", "Низ", "База", "Верх", "Основание диапазона"], ranges)
            + "\n## Обратный стресс: первый порог нарушения\n\n"
            + table(["Параметр", "Порог", "Первое нарушение", "Метод"], thresholds)
            + "\n## Торнадо приведённых расходов\n\n"
            + table(["Параметр", "Низ", "Верх", "PV низ", "PV верх", "Размах", "Ломает план"], torn)
            + f"\n## Двухфакторная сетка: спрос × мощность Earth-Core "
              f"(исполнимо {g['feasible_cells']} из {g['total_cells']})\n\n"
            + table(["Мощность \\ спрос"] + [f(v, 2) for v in g["x"]["values"]], grid_rows)
            + "\n## Порог, после которого выбранная стратегия уступает плану с Луной\n\n"
            + table(["Параметр", "Порог", "Комментарий"], switch))
    write("04_sensitivity.md", "Протокол: чувствительность и пороги", body)


def risk_protocol(case, scen, plan) -> None:
    risks, mitigations = load_risks(), load_mitigations()
    rows = []
    for r in evaluate_risks(case, scen, plan, risks):
        prob = ("—" if r["probability"] is None else f(r["probability"], 2))
        rng = ("—" if r["probability_low"] is None
               else f"{f(r['probability_low'], 2)}…{f(r['probability_high'], 2)}")
        rows.append([r["risk_id"], r["event"], r["scenario_id"], r["reference_scenario_id"], prob, rng,
                     f(r["consequence_pv_mln"]), f(r["delta_shortage_marginal_t"]),
                     f(r["sl_total_worst"] * 100) + " %", str(int(r["hard_violations"])),
                     r["owner"]])
    mrows = [[m["risk_id"], m["mitigation_id"], m["mitigation"], f(m["cost_mln"]), f(m["effect_mln"]),
              f(m["shortage_avoided_t"]), f(m["residual_mln"]), f(m["net_benefit_mln"])]
             for m in evaluate_mitigations(case, scen, plan, risks, mitigations)]
    mc = monte_carlo(case, scen["MANDATORY_STRESS"], plan, trials=400, seed=20260918)
    mc_rows = [
        ["P(жёсткое нарушение)", f(mc.p_hard_violation, 3),
         f"[{f(mc.p_hard_violation_ci[0], 3)}; {f(mc.p_hard_violation_ci[1], 3)}]"],
        ["P(обслуживание ниже нормы)", f(mc.p_service_below_min, 3),
         f"[{f(mc.p_service_below_min_ci[0], 3)}; {f(mc.p_service_below_min_ci[1], 3)}]"],
        ["P(дефицит больше нуля)", f(mc.p_shortage, 3),
         f"[{f(mc.p_shortage_ci[0], 3)}; {f(mc.p_shortage_ci[1], 3)}]"],
        ["PV среднее, млн", f(mc.pv_mean), f"[{f(mc.pv_mean_ci[0])}; {f(mc.pv_mean_ci[1])}]"],
        ["PV 95-й процентиль, млн", f(mc.pv_p95), "—"],
        ["Дефицит 95-й процентиль, т", f(mc.shortage_p95_t), "—"],
    ]
    body = ("Цель: измерить последствия рисков на выбранном плане, посчитать меры и остаточный риск.\n"
            "Каждый риск это изменение конкретного входа модели: сценария или решения плана.\n"
            "Последствие измеряется к опорному сценарию риска: событие на стороне плана сравнивается "
            "с тем же сценарием без изменения плана, событие на стороне сценария — с его родительским "
            "сценарием. Риск поверх обязательного стресса не приписывает себе весь эффект стресса.\n\n"
            "## Реестр рисков\n\n"
            + table(["ID", "Событие", "Сценарий", "Опора", "Вероятность", "Диапазон",
                     "Последствие PV, млн", "Δдефицит, т", "Обслуживание, худший год",
                     "Жёстких", "Владелец"], rows)
            + "\n## Меры: стоимость, эффект, остаточный риск\n\n"
            + table(["Риск", "Мера", "Название", "Стоимость, млн", "Эффект, млн",
                     "Дефицит предотвращён, т", "Остаток, млн", "Чистая выгода, млн"], mrows)
            + f"\n## Вероятностный блок: {mc.trials} прогонов, зерно {mc.seed}\n\n"
            + table(["Показатель", "Оценка", "95% доверительный интервал"], mc_rows)
            + f"\nПервый рабочий год каналов: {mc.first_operating_year}. "
              f"Трактовка: {mc.interpretation}\n\n"
              f"Основание распределения: {mc.assumptions['distribution_basis']}. "
              f"Независимость: {mc.assumptions['independence_basis']}.\n")
    write("05_risks.md", "Протокол: риски, меры и вероятностный блок", body)


def dispatch_protocol(case, scen, plan) -> None:
    rep = dispatch_comparison(case, scen, plan)
    rows = [[r["scenario_id"], f(r["reactive_pv_mln"]), f(r["frozen_pv_mln"]),
             f(r["value_of_information_mln"]), f(r["reactive_shortage_t"]), f(r["frozen_shortage_t"]),
             "да" if r["frozen_feasible"] else "нет"] for r in rep["rows"]]
    reaction = [[k, v] for k, v in rep["reaction_time"].items()]
    body = ("Цель: показать, что решается заранее, что после наблюдения и сколько стоит "
            "оперативная информация.\n\n"
            "Решается заранее: " + "; ".join(rep["decided_in_advance"]) + ".\n\n"
            "Решается после наблюдения: " + "; ".join(rep["decided_after_observation"]) + ".\n\n"
            "## Время реакции по каналам\n\n" + table(["Канал", "Срок и возможность пересмотра"], reaction)
            + "\n## Реактивный график против замороженного\n\n"
            + table(["Сценарий", "PV реактивный, млн", "PV замороженный, млн",
                     "Цена информации, млн", "Дефицит реактивный, т", "Дефицит замороженный, т",
                     "План исполним при заморозке"], rows)
            + "\n" + rep["note"] + "\n")
    write("06_dispatch.md", "Протокол: решения до и после наблюдения", body)


def contract_protocol(case, scen, plan) -> None:
    contracts = load_contracts()
    cards = [[c["contract_id"], c["channel"], c["counterparty"], f(c["capacity_t_per_year"], 0),
              f(c["price_mln_per_t"], 2), f(c["take_or_pay_share"] * 100, 0) + " %", c["lead_time"],
              c["liability"][:70] + "…", c["revision_rule"][:60] + "…"]
             for c in (contract_card(case, x) for x in contracts)]
    out = ""
    for sid in ("BASE", "MANDATORY_STRESS"):
        res = run(case, scen[sid], plan)
        totals = obligations_totals(obligations(case, res, contracts))
        rows = [[t["contract_id"], f(t["contracted_t"]), f(t["offtake_t"]),
                 "—" if t["utilisation"] is None else f(t["utilisation"] * 100, 1) + " %",
                 f(t["payable_t"]), f(t["unused_paid_t"]), f(t["total_payment_mln"])]
                for t in totals.values()]
        control = sum(t["total_payment_mln"] for t in totals.values())
        expected = (res.totals["procurement"] + res.totals["reservation"]
                    - sum(y.opening_stock_cost_mln for y in res.years))
        out += (f"\n## Обязательства в сценарии {sid}\n\n"
                + table(["Договор", "Законтрактовано, т", "Отобрано, т", "Загрузка",
                         "Оплачено по ToP, т", "Не отобрано оплаченного, т", "Платежи, млн"], rows)
                + f"\nСверка: сумма договорных платежей {f(control)} млн против закупок и резерва "
                  f"прогона {f(expected)} млн (расхождение {f(abs(control - expected), 6)}).\n")
    res = run(case, scen["MANDATORY_STRESS"], plan)
    eq = [[c["year"], f(c["required_reserve_t"]), f(c["callable_batch_t"]),
           f(c["max_batch_at_full_capacity_t"]), f(c["waiting_cover_needed_t"]), f(c["opening_t"]),
           "да" if c["equivalent"] else "нет"]
          for c in (emergency_reserve_check(case, plan, y.year, y.demand_total_t, y.opening_t)
                    for y in res.years)]
    body = ("Цель: показать договорную рамку и связь обязательств с расходами и поставками.\n\n"
            "## Карточки договоров\n\n"
            + table(["Договор", "Канал", "Контрагент", "Мощность, т/год", "Цена, млн/т",
                     "Take-or-pay", "Срок поставки", "Ответственность", "Пересмотр"], cards)
            + out
            + "\n## Договорный эквивалент 45-дневного резерва (обязательный стресс)\n\n"
            + table(["Год", "Требуется, т", "Партия по договору, т", "Максимум при полной мощности, т",
                     "Нужен запас на ожидание, т", "Запас на начало, т", "Эквивалент доказан"], eq))
    write("07_contracts.md", "Протокол: договоры и обязательства", body)


def stakeholder_protocol(case, scen, plan) -> None:
    risk_ids = [sid for sid in scen if sid.startswith("TEAM_RISK_")]
    data = impact(case, scen, plan, None, ["BASE", "MANDATORY_STRESS"], "BASE", risk_ids)
    ids = data["scenario_ids"]
    body = "Цель: показать последствия для каждой значимой стороны в стрессе и при реализации рисков.\n"
    for st in data["stakeholders"]:
        rows = []
        for key, first in st["scenarios"][ids[0]].items():
            row = [first["label"] + ", " + first["unit"]]
            for sid in ids:
                m = st["scenarios"][sid][key]
                mark = " ✗" if m["worse"] else ""
                row.append(f(m["value"], 3 if m["unit"] == "доля" else 2) + mark)
            rows.append(row)
        body += (f"\n## {st['name']}\n\n{st['role']}. Получает: {st['gets']}. Платит: {st['pays']}. "
                 f"Несёт риск: {st['bears_risk']}. Требует: {st['requires']}. "
                 f"Договоры: {', '.join(st['obligations'])}.\n\n"
                 + table(["Метрика"] + ids, rows))
    body += "\n✗ отмечает ухудшение относительно базового сценария по направлению метрики.\n"
    write("08_stakeholders.md", "Протокол: последствия для заинтересованных сторон", body)


def exports(case, scen, plan) -> None:
    from kriokontur.risks import load_risks as _load
    for sid in ("BASE", "MANDATORY_STRESS"):
        res = run(case, scen[sid], plan)
        extras = export_sections(case, scen, plan, res, risks=_load())
        stem = f"{plan.plan_id}_{sid}"
        export.write_csv(case, res, scen[sid], OUT / f"{stem}.csv", extras)
        export.write_xlsx(case, res, scen[sid], OUT / f"{stem}.xlsx", extras)
        print(f"   results/protocols/{stem}.csv / .xlsx")
        kpi = kpi_rows(case, res)
        rows = [[k["label"], f(k["value"], 3 if k["unit"] == "доля" else 2), k["unit"],
                 "—" if k["target"] is None else f(k["target"], 3), k["status"] or "—",
                 str(k["period"]), k["formula"]] for k in kpi]
        write(f"09_kpi_{sid}.md", f"Протокол: KPI, сценарий {sid}",
              "KPI считаются из того же прогона, что и выгрузка, и попадают в CSV и XLSX.\n\n"
              + table(["Показатель", "Значение", "Ед.", "Цель", "Статус", "Период", "Формула"], rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=str(ROOT / "configs/plans/final-candidate.json"))
    args = ap.parse_args()
    case, scen, plan = load_case(), load_all(), Plan.load(args.plan)
    print(f"Протоколы для плана «{plan.name}» ({plan.plan_id}), набор {case.version}:")
    environment(case)
    control_cases()
    scenario_protocol(case, scen, plan)
    stress_protocol(case, scen, plan)
    sensitivity_protocol(case, scen, plan)
    risk_protocol(case, scen, plan)
    dispatch_protocol(case, scen, plan)
    contract_protocol(case, scen, plan)
    stakeholder_protocol(case, scen, plan)
    exports(case, scen, plan)
    print("Готово:", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
