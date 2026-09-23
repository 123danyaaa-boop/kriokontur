"""Оптимизатор плана: робастная смешанная целочисленная модель поверх годового баланса.

Роль в системе. Помесячный движок (engine.run) остаётся источником истины: он считает
баланс, деньги и нарушения. Оптимизатор только предлагает план, то есть резервирование
мощности по каналам и годам, сроки инвестиций и начальный запас. Предложенный план затем
прогоняется движком во всех сценариях, и результат этой проверки, а не значение целевой
функции, считается ответом. Оптимизатор предлагает, движок проверяет.

Постановка (двухэтапная робастная модель):
    решения первого этапа, общие для всех сценариев:
        Res[s,y]    резервируемая мощность канала s в году y, т/год
        build[i,y]  бинарные решения о сроках инвестиций (ZBO, Lunar-ISRU, опцион Earth-New)
        I0          начальный запас, т
    решения второго этапа, свои в каждом сценарии k:
        Q[s,y,k]    годовой отбор по каналу
        I[y,k]      запас на начало года
    ограничения в каждом сценарии:
        баланс с полным обслуживанием спроса, резерв 45 суток с запасом, ёмкость хранилища
        с учётом месячного пика поступления, потолок потерь стресса, доступность и мощность
        каналов, минимальный отбор, лимиты CAPEX, аварийный канал только как страховка
    цель: взвешенная по сценариям приведённая стоимость.

Модель годовая и приближённая по внутригодовой динамике, поэтому все запасы берутся
с коэффициентом. Если проверка движком находит нарушение, запас увеличивается и задача
решается заново (итерационное уточнение, см. optimize_and_verify).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pulp

from . import rules
from .caseinput import CaseInput
from .engine import MONTHS, months_available, run
from .plan import Plan
from .scenarios import Scenario

INVEST_YEARS = {
    "ZBO": [2036, 2037, 2038, 2039, 2040],
    "LUNAR_ISRU": [2035, 2036, 2037],
    "EARTH_NEW_OPTION": [2035, 2036, 2037, 2038],
    "EARTH_NEW_EXERCISE": [2035, 2036, 2037, 2038],
}


@dataclass
class OptimizerSettings:
    scenario_ids: List[str] = field(default_factory=lambda: ["BASE", "MANDATORY_STRESS"])
    weights: Optional[Dict[str, float]] = None          # по умолчанию поровну
    reserve_margin: float = 1.15                        # запас к 45-дневному резерву на начало года
    capacity_headroom: float = 0.0                      # доп. запас месячной мощности сверх спроса и набора резерва
    peak_share: float = 1.0 / 12.0                      # месячный пик поступления как доля годового
    emergency_reserve_days: float = 45.0                # страховой резерв аварийного канала
    allow_isru: bool = True
    strategic_stock: bool = True                        # False: страховка только мощностью, запас как у диспетчера
    allow_earth_new: bool = True
    time_limit_s: int = 60
    discount_rate: Optional[float] = None


@dataclass
class OptimizerResult:
    status: str
    objective_pv: float
    plan: Plan
    decisions: Dict[str, Optional[int]]
    per_scenario_pv_model: Dict[str, float]
    settings: OptimizerSettings
    iterations: int = 1
    verification: Dict[str, Dict] = field(default_factory=dict)


def _frac_first_month(first_month: Optional[int], year_index: int) -> float:
    return months_available(first_month, year_index) / MONTHS


def build_model(case: CaseInput, scenarios: Dict[str, Scenario], base_plan: Plan,
                settings: OptimizerSettings) -> Tuple[pulp.LpProblem, Dict]:
    years = case.years
    y0 = years[0]
    K = settings.scenario_ids
    w = settings.weights or {k: 1.0 / len(K) for k in K}
    r = settings.discount_rate if settings.discount_rate is not None else float(base_plan.assume("discount_rate"))
    disc = {y: 1.0 / (1.0 + r) ** (y - y0) for y in years}
    src = case.sources
    S = sorted(src)
    base_store, zbo_store = case.storage["BASE"], case.storage["ZBO"]
    lam_b, lam_z = base_store.loss_rate_on_throughput, zbo_store.loss_rate_on_throughput
    prep = float(base_plan.assume("earth_new_prep_months"))
    lag_d = float(base_plan.assume("isru_commissioning_lag_months"))
    d_from = src["D"].available_from_year or 2038

    m = pulp.LpProblem("kriokontur_robust_plan", pulp.LpMinimize)
    V: Dict = {}

    # ---- первый этап: инвестиции -------------------------------------------------
    build = {}
    for inv, ys in INVEST_YEARS.items():
        allowed = (inv != "LUNAR_ISRU" or settings.allow_isru) and \
                  (not inv.startswith("EARTH_NEW") or settings.allow_earth_new)
        for y in ys:
            build[inv, y] = m.add_variable(f"b_{inv}_{y}", cat="Binary") if allowed else 0
        m += pulp.lpSum(build[inv, y] for y in ys) <= 1, f"once_{inv}"
    # реализация опциона не раньше покупки права
    for y in years:
        m += (pulp.lpSum(build["EARTH_NEW_EXERCISE", t] for t in INVEST_YEARS["EARTH_NEW_EXERCISE"] if t <= y)
              <= pulp.lpSum(build["EARTH_NEW_OPTION", t] for t in INVEST_YEARS["EARTH_NEW_OPTION"] if t <= y)), f"opt_before_ex_{y}"
    zbo_active = {y: pulp.lpSum(build["ZBO", t] for t in INVEST_YEARS["ZBO"] if t <= y) for y in years}
    isru_funded = pulp.lpSum(build["LUNAR_ISRU", t] for t in INVEST_YEARS["LUNAR_ISRU"])

    capex = {y: 0 for y in years}
    for y in years:
        capex[y] = (zbo_store.capex_mln * (build["ZBO", y] if ("ZBO", y) in build else 0)
                    + case.investments["LUNAR_ISRU"].exercise_cost_mln * (build["LUNAR_ISRU", y] if ("LUNAR_ISRU", y) in build else 0)
                    + case.investments["EARTH_NEW"].option_fee_mln * (build["EARTH_NEW_OPTION", y] if ("EARTH_NEW_OPTION", y) in build else 0)
                    + case.investments["EARTH_NEW"].exercise_cost_mln * (build["EARTH_NEW_EXERCISE", y] if ("EARTH_NEW_EXERCISE", y) in build else 0))
    m += pulp.lpSum(capex[y] for y in years if y <= 2037) <= case.constraint("CAPEX_2037").value, "capex_2037"
    m += pulp.lpSum(capex[y] for y in years) <= case.constraint("CAPEX_2040").value, "capex_2040"

    # ---- первый этап: резервирование ----------------------------------------------
    Res = {}
    ResC = {}   # Earth-New раскладывается по году реализации опциона, чтобы доля года оставалась линейной
    for s in S:
        for i, y in enumerate(years):
            Res[s, y] = m.add_variable(f"res_{s}_{y}", lowBound=0, upBound=src[s].capacity_t_per_year)
    for t in INVEST_YEARS["EARTH_NEW_EXERCISE"]:
        for y in years:
            ResC[t, y] = m.add_variable(f"resC_{t}_{y}", lowBound=0, upBound=src["C"].capacity_t_per_year)
            b = build["EARTH_NEW_EXERCISE", t]
            m += ResC[t, y] <= src["C"].capacity_t_per_year * (b if not isinstance(b, int) else b), f"resC_link_{t}_{y}"
    for y in years:
        m += Res["C", y] == pulp.lpSum(ResC[t, y] for t in INVEST_YEARS["EARTH_NEW_EXERCISE"]), f"resC_sum_{y}"
        m += Res["D", y] <= src["D"].capacity_t_per_year * isru_funded, f"isru_link_{y}"
    # Начальный запас: правило подготовительного периода в кейсе не задано, поэтому оптимизатору
    # не разрешено «закупать впрок» до 2035 года. Запас ограничен требованием резерва с тем же
    # коэффициентом, что и в политике запаса команды.
    R0 = max(rules.reserve_days_to_tonnes(scenarios[k].demand_total(case, y0)) for k in settings.scenario_ids)
    I0 = m.add_variable("opening_stock", lowBound=R0, upBound=R0 * max(1.0, float(base_plan.assume("reserve_safety_factor") or 1.0)))

    # ---- второй этап: сценарии -------------------------------------------------------
    pv_k = {}
    U: Dict = {}
    for k in K:
        sc = scenarios[k]
        delay = {s: sc.commissioning_delay(src[s].name, s) for s in S}
        first = {s: (None if src[s].available_from_year is None else
                     int(max(0, (src[s].available_from_year - y0) * MONTHS) + delay[s])) for s in ("A", "B", "E")}
        first_d = int(max((d_from - y0) * MONTHS + lag_d, 0) + delay["D"])
        I = {y: m.add_variable(f"I_{k}_{y}", lowBound=0) for y in years + [years[-1] + 1]}
        m += I[years[0]] == I0, f"open_{k}"
        cost_terms = []
        for i, y in enumerate(years):
            dem = sc.demand_total(case, y)
            R = rules.reserve_days_to_tonnes(dem)
            avail, fr = {}, {}
            for s in ("A", "B", "E"):
                fr[s] = _frac_first_month(first[s], i)
                avail[s] = Res[s, y] * fr[s] * sc.capacity_factor(src[s].name, s, y)
            fr["D"] = _frac_first_month(first_d, i)
            avail["D"] = Res["D", y] * fr["D"] * sc.capacity_factor(src["D"].name, "D", y)
            availC_terms, reservedC_terms = [], []
            for t in INVEST_YEARS["EARTH_NEW_EXERCISE"]:
                fm = int((t - y0) * MONTHS + prep + delay["C"])
                f = _frac_first_month(fm, i)
                availC_terms.append(ResC[t, y] * f * sc.capacity_factor(src["C"].name, "C", y))
                reservedC_terms.append(ResC[t, y] * f)
            avail["C"] = pulp.lpSum(availC_terms)
            reserved_period = {s: Res[s, y] * fr[s] for s in ("A", "B", "E", "D")}
            reserved_period["C"] = pulp.lpSum(reservedC_terms)

            Q = {s: m.add_variable(f"Q_{k}_{s}_{y}", lowBound=0) for s in S}
            pay = {s: m.add_variable(f"P_{k}_{s}_{y}", lowBound=0) for s in S}
            for s in S:
                m += Q[s] <= avail[s], f"cap_{k}_{s}_{y}"
                m += pay[s] >= Q[s], f"pay_q_{k}_{s}_{y}"
                m += pay[s] >= src[s].take_or_pay_share * reserved_period[s], f"pay_top_{k}_{s}_{y}"
            # аварийный канал не должен становиться базовым: доля в году не выше порога из допущений
            # (тот же порог, по которому движок считает использование базовым)
            # Точное правило кейса: аварийный канал считается базовым, если его доля в году выше порога,
            # и базовым он не может быть больше двух лет подряд. u = 1 в годы «базового» использования.
            e_share = float(base_plan.assume("emergency_base_share_threshold") or 0.10)
            u = m.add_variable(f"u_emerg_{k}_{y}", cat="Binary")
            m += Q["E"] <= e_share * dem + src["E"].capacity_t_per_year * u, f"emergency_share_{k}_{y}"
            U.setdefault(k, {})[y] = u
            # запас месячной мощности: диспетчер набирает резерв к началу следующего года помесячно,
            # поэтому доступной мощности должно хватать на спрос, прирост резерва и буфер
            if i + 1 < len(years):
                ramp = float(base_plan.assume("reserve_ramp_months") or 9)
                dem_next = sc.demand_total(case, years[i + 1])
                growth = max(0.0, rules.reserve_days_to_tonnes(dem_next) - R) * settings.reserve_margin
                # потери зависят от режима хранилища: с модернизацией 1,2%, без неё 4,5%;
                # разница включается через бинарную переменную режима (линейная запись)
                total_cap = sum(src[s].capacity_t_per_year for s in S)
                m += ((1 - lam_z) * pulp.lpSum(avail[s] for s in S if s != "E")
                      >= dem * (1 + settings.capacity_headroom) + growth * MONTHS / ramp
                      + (lam_b - lam_z) * total_cap * (1 - zbo_active[y])), f"headroom_{k}_{y}"
            # не резервируем мощность в годы, где канал в этом сценарии ещё не введён
            for s in ("A", "B", "E", "D"):
                if fr[s] == 0:
                    m += Res[s, y] == 0, f"no_res_before_avail_{k}_{s}_{y}"
            for t in INVEST_YEARS["EARTH_NEW_EXERCISE"]:
                fm = int((t - y0) * MONTHS + prep + delay["C"])
                if _frac_first_month(fm, i) == 0:
                    m += ResC[t, y] == 0, f"no_resC_before_avail_{k}_{t}_{y}"
            inflow = pulp.lpSum(Q[s] * sc.delivery_factor(src[s].name, s, y) for s in S)
            # потери зависят от режима хранилища: линеаризация произведения бинарной и непрерывной
            Wz = m.add_variable(f"Wz_{k}_{y}", lowBound=0)
            big = sum(src[s].capacity_t_per_year for s in S)
            m += Wz <= big * zbo_active[y], f"wz1_{k}_{y}"
            m += Wz <= inflow, f"wz2_{k}_{y}"
            m += Wz >= inflow - big * (1 - zbo_active[y]), f"wz3_{k}_{y}"
            losses = lam_b * inflow - (lam_b - lam_z) * Wz
            m += I[years[i] + 1] == I[y] + inflow - losses - dem, f"balance_{k}_{y}"
            # на начало горизонта движок проверяет ровно требование, дальше держим запас к нему
            m += I[y] >= R * (1.0 if i == 0 else settings.reserve_margin), f"reserve_{k}_{y}"
            if not settings.strategic_stock and i > 0:
                # без стратегического запаса модель держит запас на уровне цели диспетчера,
                # поэтому защищаться от рисков ей приходится резервом мощности, как в ручном плане
                safety = float(base_plan.assume("reserve_safety_factor") or 1.15)
                m += I[y] <= R * max(safety, settings.reserve_margin) + 2.0, f"no_prebuild_{k}_{y}"
            cap_y = base_store.capacity_t + (zbo_store.capacity_t - base_store.capacity_t) * zbo_active[y]
            m += I[y] + inflow * settings.peak_share <= cap_y, f"storage_{k}_{y}"
            if sc.loss_ceiling(y) is not None and lam_b > sc.loss_ceiling(y):
                m += zbo_active[y] >= 1, f"loss_ceiling_{k}_{y}"
            # страховой аварийный резерв: договорная мощность под срок ожидания поставки
            m += Res["E", y] >= min(src["E"].capacity_t_per_year,
                                    dem * settings.emergency_reserve_days / rules.DAYS_IN_YEAR), f"emerg_res_{k}_{y}"
            price = {s: src[s].variable_cost_mln_per_t * sc.price_factor(src[s].name, s, y) for s in S}
            procurement = pulp.lpSum(price[s] * pay[s] for s in S)
            reservation = pulp.lpSum(src[s].reservation_rate * reserved_period[s] for s in S)
            holding = base_store.holding_cost_mln_per_t_year * (I[y] + I[years[i] + 1]) * 0.5
            opex = zbo_store.fixed_opex_mln_per_year * zbo_active[y]
            if y >= d_from:
                opex = opex + case.investments["LUNAR_ISRU"].fixed_opex_mln_per_year * isru_funded
            opening = src[str(base_plan.assume("opening_stock_source"))].variable_cost_mln_per_t * I0 if i == 0 else 0
            cost_terms.append(disc[y] * (procurement + reservation + holding + opex + capex[y] + opening))
            V[k, y] = {"Q": Q, "I": I[y], "avail": avail}
        pv_k[k] = pulp.lpSum(cost_terms)
        for i in range(len(years) - 2):
            m += U[k][years[i]] + U[k][years[i + 1]] + U[k][years[i + 2]] <= 2, f"emergency_streak_{k}_{years[i]}"
    m += pulp.lpSum(w[k] * pv_k[k] for k in K)
    V.update({"Res": Res, "ResC": ResC, "build": build, "I0": I0, "pv_k": pv_k,
              "demand": {k: {y: scenarios[k].demand_total(case, y) for y in years} for k in K}})
    return m, V


def _to_plan(case: CaseInput, base_plan: Plan, V: Dict, settings: OptimizerSettings) -> Tuple[Plan, Dict]:
    plan = copy.deepcopy(base_plan)
    plan.plan_id = "optimized"
    plan.name = "Оптимизированный план (робастный)"
    decisions = {inv: None for inv in INVEST_YEARS}
    for (inv, y), var in V["build"].items():
        if not isinstance(var, int) and (var.value() or 0) > 0.5:
            decisions[inv] = y
    plan.investments = {"ZBO": decisions["ZBO"], "LUNAR_ISRU": decisions["LUNAR_ISRU"],
                        "EARTH_NEW_OPTION": decisions["EARTH_NEW_OPTION"],
                        "EARTH_NEW_EXERCISE": decisions["EARTH_NEW_EXERCISE"]}
    plan.reservations = {s: {} for s in case.sources}
    for (s, y), var in V["Res"].items():
        plan.reservations[s][y] = round(max(0.0, var.value() or 0.0), 1)
    plan.orders = {}
    plan.inventory_policy = dict(plan.inventory_policy)
    plan.inventory_policy["opening_stock_t"] = round((V["I0"].value() or 0.0) + 0.05, 1)
    # стратегический запас на начало года: максимум по сценариям запланированного моделью запаса,
    # если он выше обычной цели диспетчера. Так близорукий помесячный диспетчер получает то
    # намерение, которое видит многолетняя модель (запас перед провалом мощности).
    targets = {}
    safety = float(plan.assume("reserve_safety_factor") or 1.0)
    for y in case.years[1:]:
        planned = max((V[k, y]["I"].value() or 0.0) for k in settings.scenario_ids)
        normal = max(rules.reserve_days_to_tonnes(V["demand"][k][y]) * safety for k in settings.scenario_ids)
        if planned > normal + 0.5:
            targets[y] = round(planned, 1)
    if targets and settings.strategic_stock:
        plan.inventory_policy["target_opening_t"] = targets
    plan.assumptions = dict(plan.assumptions)
    plan.assumptions["optimizer"] = {
        "method": "robust MILP, CBC", "scenarios": settings.scenario_ids,
        "reserve_margin": settings.reserve_margin, "emergency_reserve_days": settings.emergency_reserve_days,
    }
    return plan, decisions


def bundled_cbc_path() -> Optional[str]:
    """Путь к CBC 2.10.3, встроенному в PuLP 3.x, или None, если его в пакете нет.

    Этим решателем получены все опубликованные числа. Права на исполнение в Linux и macOS
    PuLP выставляет сам при импорте. В PuLP 4.0 встроенный бинарник уберут.
    """
    from pulp.apis import coin_api
    bundled = getattr(coin_api, "pulp_cbc_path", None)
    if not bundled:
        return None
    return coin_api.COIN_CMD.executableExtension(bundled)


def _solver(time_limit_s: Optional[float]) -> pulp.LpSolver:
    """CBC через COIN_CMD, как рекомендует PuLP 3.3 перед переходом на 4.0.

    PULP_CBC_CMD устарел, поэтому тот же встроенный бинарник вызывается через COIN_CMD
    с явным путём. Где искать CBC, по порядку:
        1. CBC 2.10.3, встроенный в PuLP 3.3.2: им получены все опубликованные числа,
           и он воспроизводим на любой машине, потому что PuLP закреплён;
        2. пакет cbcbox, если он установлен (его ставит pip install pulp[cbc]);
        3. cbc в PATH.
    cbcbox и PATH только запасные: cbcbox это +140–180 МБ и сборка из ветки разработки,
    которая сама выбирает вариант по процессору. Вернуться к вопросу при переходе на PuLP 4.
    """
    candidates: List[Optional[str]] = []
    bundled = bundled_cbc_path()
    if bundled:
        candidates.append(bundled)
    try:
        import cbcbox
        candidates.append(cbcbox.cbc_bin_path())
    except ImportError:
        pass
    candidates.append(None)  # COIN_CMD сам ищет cbc в PATH
    for path in candidates:
        solver = pulp.COIN_CMD(msg=False, timeLimit=time_limit_s, path=path)
        if solver.available():
            return solver
    raise RuntimeError("не найден решатель CBC: поставьте зависимости командой "
                       "python -m pip install -r requirements.txt")


def optimize(case: CaseInput, scenarios: Dict[str, Scenario], base_plan: Plan,
             settings: Optional[OptimizerSettings] = None) -> OptimizerResult:
    settings = settings or OptimizerSettings()
    model, V = build_model(case, scenarios, base_plan, settings)
    model.solve(_solver(settings.time_limit_s))
    status = pulp.LpStatus[model.status]
    if status not in ("Optimal", "Not Solved") or model.objective.value() is None:
        return OptimizerResult(status=status, objective_pv=float("nan"), plan=base_plan,
                               decisions={}, per_scenario_pv_model={}, settings=settings)
    plan, decisions = _to_plan(case, base_plan, V, settings)
    per = {k: pulp.value(v) for k, v in V["pv_k"].items()}
    return OptimizerResult(status=status, objective_pv=pulp.value(model.objective), plan=plan,
                           decisions=decisions, per_scenario_pv_model=per, settings=settings)


def verify(case: CaseInput, scenarios: Dict[str, Scenario], plan: Plan, scenario_ids: List[str]) -> Dict[str, Dict]:
    """Проверка предложенного плана помесячным движком: он и только он решает, исполним ли план."""
    out = {}
    for sid in scenario_ids:
        res = run(case, scenarios[sid], plan)
        hard = [v for v in res.violations if v.severity == "hard"]
        out[sid] = {"pv_mln": res.totals["discounted_cost_mln"], "sl_total": res.totals["sl_total"],
                    "shortage_t": res.totals["shortage_t"], "hard": len(hard),
                    "codes": sorted({v.code for v in hard})}
    return out


def optimize_and_verify(case: CaseInput, scenarios: Dict[str, Scenario], base_plan: Plan,
                        settings: Optional[OptimizerSettings] = None,
                        report_ids: Optional[List[str]] = None, max_iter: int = 4) -> OptimizerResult:
    """Решить, проверить движком, при нарушениях увеличить запасы и решить снова.

    Критерий остановки: ноль жёстких нарушений в тех сценариях, под которые план оптимизировали.
    Остальные сценарии из report_ids прогоняются для отчёта и на критерий не влияют.
    """
    settings = copy.deepcopy(settings or OptimizerSettings())
    required = list(settings.scenario_ids)
    verify_ids = list(dict.fromkeys(required + list(report_ids or [])))
    result = None
    for it in range(1, max_iter + 1):
        result = optimize(case, scenarios, base_plan, settings)
        if result.status != "Optimal":
            result.iterations = it
            return result
        result.verification = verify(case, scenarios, result.plan, verify_ids)
        result.iterations = it
        if all(result.verification[sid]["hard"] == 0 for sid in required):
            return result
        settings.reserve_margin = round(settings.reserve_margin + 0.05, 2)
        settings.capacity_headroom = round(settings.capacity_headroom + 0.03, 2)
        settings.emergency_reserve_days = min(120.0, settings.emergency_reserve_days + 15.0)
    return result
