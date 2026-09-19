"""Сборка автономного демо: снимок расчёта для фронта без бэкенда.

    PYTHONPATH=src python scripts/build_demo.py
Делает два файла:
    web/snapshot.js                    подхватывается фронтом, если бэкенд не запущен
    ../outputs/kriokontur-demo.html    один файл со встроенным снимком, можно просто открыть
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kriokontur.caseinput import load_case
from kriokontur.engine import run
from kriokontur.horizon import (HorizonConfig, cost_per_served, cumulative_discounted, earth_ceiling,
                                extend_case, first_deficit_year, first_infeasible_year, payback_year)
from kriokontur.plan import Plan
from kriokontur.planner import auto_plan
from kriokontur.scenarios import SCEN_DIR, load_all
from kriokontur.sensitivity import reverse_stress, tornado

ARCH = {
    "earth": ("Только Земля", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035, "ZBO2": 2041}, []),
    "earth2": ("Земля плюс второй поставщик", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                               "ZBO2": 2041, "EARTH_NEW_2": 2041}, ["EARTH_NEW_2"]),
    "isru1": ("Луна, первая очередь", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                       "ZBO2": 2041, "LUNAR_ISRU": 2036}, []),
    "isru2": ("Луна, две очереди", {"ZBO": 2036, "EARTH_NEW_OPTION": 2035, "EARTH_NEW_EXERCISE": 2035,
                                    "ZBO2": 2041, "LUNAR_ISRU": 2036, "LUNAR_ISRU_PHASE2": 2041}, ["LUNAR_ISRU_PHASE2"]),
}


def run_json(res):
    return {
        "plan_id": res.plan_id, "scenario_id": res.scenario_id, "totals": res.totals,
        "assumptions": res.assumptions,
        "years": [{**asdict(y), "sl_total": y.sl_total, "sl_critical": y.sl_critical,
                   "loss_share": y.loss_share} for y in res.years],
        "violations": [v.as_dict() for v in res.violations],
    }


def horizon_json(case, scen, cfg, method, rate=0.08):
    rows, cums = [], {}
    for key, (label, invest, enable) in ARCH.items():
        ext = extend_case(case, cfg, method, ["ZBO2"] + enable)
        plan = auto_plan(ext, scen["BASE"], invest, key, label)
        plan.assumptions["discount_rate"] = rate
        res = run(ext, scen["BASE"], plan)
        cums[key] = cumulative_discounted(res, ext.years[0], rate)
        rows.append({"key": key, "label": label, "pv_2040": cums[key][2040], "pv_last": cums[key][cfg.last_year],
                     "served_t": res.totals["served_t"], "shortage_t": res.totals["shortage_t"],
                     "cost_per_t": cost_per_served(res, ext.years[0], rate)[cfg.last_year],
                     "first_deficit_year": first_deficit_year(res)})
    demo = extend_case(case, cfg, method, ["ZBO2"])
    return {"method": method, "basis": cfg.methods[method]["basis"], "last_year": cfg.last_year,
            "demand": {str(y): demo.demand_total[y] for y in demo.years},
            "earth_ceiling": earth_ceiling(demo),
            "first_infeasible_year": first_infeasible_year(demo, earth_ceiling(demo)),
            "architectures": rows,
            "payback": {"isru1_vs_earth2": payback_year(cums["isru1"], cums["earth2"]),
                        "isru2_vs_earth2": payback_year(cums["isru2"], cums["earth2"]),
                        "isru2_vs_isru1": payback_year(cums["isru2"], cums["isru1"])},
            "notes": cfg.notes}


def main() -> None:
    case, scen, cfg = load_case(), load_all(), HorizonConfig.load()
    plans = {}
    order = {"final-candidate": 0}
    for path in sorted((ROOT / "configs" / "plans").glob("*.json"), key=lambda p: (order.get(p.stem, 1), p.stem)):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("name", path.stem)
        plans[path.stem] = raw
    default = Plan.load(ROOT / "configs" / "plans" / "final-candidate.json")
    runs = {sid: run_json(run(case, scen[sid], default)) for sid in scen}
    snapshot = {
        "case": {"version": case.version, "years": case.years, "units": case.currency_note,
                 "sources": [asdict(s) for s in case.source_list]},
        "scenarios": [{"scenario_id": s.scenario_id, "label": s.label, "status": s.status, "notes": s.notes}
                      for s in scen.values()],
        "plans": plans, "runs": runs,
        "sensitivity": {"tornado": tornado(case, scen["BASE"], default),
                        "reverse": reverse_stress(case, scen["BASE"], default)},
        "horizon": horizon_json(case, scen, cfg, "increment"),
        "horizonAll": {m: horizon_json(case, scen, cfg, m) for m in ("increment", "saturating", "plateau")},
    }
    blob = "window.SNAPSHOT = " + json.dumps(snapshot, ensure_ascii=False) + ";"
    (ROOT / "web" / "snapshot.js").write_text(blob, encoding="utf-8")

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    standalone = html.replace('<script src="/web/snapshot.js" onerror="window.__noSnapshot=true"></script>',
                              "<script>\n" + blob + "\n</script>")
    out = Path("/mnt/user-data/outputs/kriokontur-demo.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(standalone, encoding="utf-8")
    print(f"web/snapshot.js: {len(blob) / 1024:.0f} КБ")
    print(f"{out}: {len(standalone) / 1024:.0f} КБ")


if __name__ == "__main__":
    main()
