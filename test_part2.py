"""
test_part2.py — Smoke-test for Parts 2 and 3 (no SUMO required).

Covers:
  - core/data_models.py   (Part 2)
  - core/timing_engine.py (Part 2)
  - core/data_manager.py  (Part 2)
  - core/traci_adapter.py (Part 3 — import & structure only, no live SUMO)
  - core/sim_controller.py (Part 3 — import & structure only, no live SUMO)

Run from the project root:
    python test_part2.py
"""

import os, sys, tempfile, traceback

PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"
_results = []

def check(label, fn):
    try:
        result = fn()
        msg = f"  {result}" if result else ""
        print(f"{PASS} {label}{msg}")
        _results.append((label, True, ""))
    except Exception as e:
        print(f"{FAIL} {label}")
        print(f"       {e}")
        traceback.print_exc()
        _results.append((label, False, str(e)))

# ─── 1. Import chain ──────────────────────────────────────────────────────────
print("\n=== 1. Import chain ===")
check("import config",          lambda: __import__("config"))
check("import data_models",     lambda: __import__("core.data_models", fromlist=["*"]))
check("import timing_engine",   lambda: __import__("core.timing_engine", fromlist=["*"]))
check("import data_manager",    lambda: __import__("core.data_manager", fromlist=["*"]))
check("import traci_adapter",   lambda: __import__("core.traci_adapter", fromlist=["*"]))
check("import sim_controller",  lambda: __import__("core.sim_controller", fromlist=["*"]))

# ─── 2. data_models — dataclasses & Webster ───────────────────────────────────
print("\n=== 2. data_models ===")
from core.data_models import (
    FlowData, IntersectionParam, TimingPlan, SimStatus, SimResult, webster,
    build_tl_states, PHASE_LABELS,
)
import config

def test_flowdata():
    fd = FlowData(flows=config.DEFAULT_FLOWS.copy())
    errors = fd.validate()
    assert errors == [], f"validate errors: {errors}"
    fd.compute_critical_ratios(IntersectionParam())
    assert 0 < fd.Y < 1, f"Y={fd.Y} out of range"
    return f"Y={fd.Y:.4f}, ratios={[round(r,4) for r in fd.critical_ratios]}"

def test_webster():
    fd = FlowData(flows=config.DEFAULT_FLOWS.copy())
    plan = webster(fd)
    assert plan.cycle > 0
    assert len(plan.green_times) == 4
    errors = plan.validate()
    assert errors == [], f"plan invalid: {errors}"
    return plan.summary()

def test_timing_plan_serialise():
    fd = FlowData(flows=config.DEFAULT_FLOWS.copy())
    plan = webster(fd)
    d = plan.to_db_dict()
    plan2 = TimingPlan.from_db_dict(d)
    assert plan2.plan_id == plan.plan_id
    assert plan2.cycle == plan.cycle
    assert plan2.green_times == plan.green_times
    return "roundtrip OK"

def test_sim_status():
    s = SimStatus(sim_time=272, phase_index=0, phase_remaining=14,
                  queue_lengths={"N": 42, "S": 30, "E": 18, "W": 27},
                  avg_delay=28.4, throughput=240, vehicle_count=86)
    assert s.sim_time_str == "04:32"
    assert s.green_phase_number == 1
    return f"time={s.sim_time_str} phase={s.green_phase_number}"

def test_build_tl_states():
    # Simulate a known link_map (as if TraCI returned these indices)
    link_map = {
        "n2c_c2s": 0, "n2c_c2e": 1, "n2c_c2w": 8,   # N approach
        "s2c_c2n": 2, "s2c_c2w": 3, "s2c_c2e": 9,   # S approach
        "e2c_c2w": 4, "e2c_c2s": 5, "e2c_c2n": 10,  # E approach
        "w2c_c2e": 6, "w2c_c2n": 7, "w2c_c2s": 11,  # W approach
    }
    states = build_tl_states(link_map, total_links=12)
    assert len(states) == 9, f"expected 9 state keys, got {len(states)}"
    for k, v in states.items():
        assert len(v) == 12, f"{k}: state len={len(v)}, expected 12"
    ns_green = states["NS_THROUGH_GREEN"]
    assert ns_green[0] == "G" and ns_green[2] == "G", f"NS through wrong: {ns_green}"
    assert ns_green[8] == "g" and ns_green[9] == "g", f"right turns wrong: {ns_green}"
    return f"NS_THROUGH_GREEN='{ns_green}'"

def test_negative_flow_rejected():
    fd = FlowData(flows={"N_through": -1, "N_left": 0})
    errors = fd.validate()
    assert any("负" in e for e in errors), f"negative flow not caught: {errors}"
    return "negative flow caught"

check("FlowData compute & validate", test_flowdata)
check("Webster algorithm",           test_webster)
check("TimingPlan serialise/restore",test_timing_plan_serialise)
check("SimStatus helpers",           test_sim_status)
check("build_tl_states (12 links)",  test_build_tl_states)
check("Negative flow rejected",      test_negative_flow_rejected)

# ─── 3. timing_engine ─────────────────────────────────────────────────────────
print("\n=== 3. timing_engine ===")
from core.timing_engine import TimingEngine

engine = TimingEngine()

def test_engine_compute():
    fd = FlowData(flows=config.DEFAULT_FLOWS.copy())
    plan = engine.compute(fd)
    assert engine.is_valid(plan)
    return engine.plan_summary(plan)

def test_engine_default_plan():
    plan = engine.default_plan()
    assert plan.cycle == 96
    assert plan.green_times == [32.0, 16.0, 22.0, 10.0]
    return f"C={plan.cycle}s greens={plan.green_times}"

def test_engine_manual_plan():
    plan = engine.plan_from_green_times(30, 12, 20, 10)
    assert engine.is_valid(plan)
    return engine.plan_summary(plan)

def test_engine_adjust_cycle():
    plan  = engine.default_plan()
    plan2 = engine.adjust_to_cycle(plan, 80)
    actual = engine.actual_cycle(plan2)
    assert abs(actual - 80) <= 2.0, f"cycle mismatch: {actual}"
    return f"adjusted C={plan2.cycle}s greens={[round(g,1) for g in plan2.green_times]}"

def test_engine_invalid_plan_detected():
    bad = TimingPlan(cycle=50, green_times=[5, 5, 5, 5])
    errors = engine.validate(bad)
    assert len(errors) > 0
    return f"{len(errors)} error(s) detected"

def test_engine_oversaturated():
    heavy = {k: 1700.0 for k in config.DEFAULT_FLOWS}
    fd = FlowData(flows=heavy)
    plan = engine.compute(fd)
    assert plan.note == "auto_oversaturated"
    return f"C={plan.cycle}s note={plan.note}"

check("engine.compute (Webster)",    test_engine_compute)
check("engine.default_plan (96s)",   test_engine_default_plan)
check("engine.plan_from_green_times",test_engine_manual_plan)
check("engine.adjust_to_cycle(80s)", test_engine_adjust_cycle)
check("engine detects invalid plan", test_engine_invalid_plan_detected)
check("engine handles oversaturated",test_engine_oversaturated)

# ─── 4. data_manager ──────────────────────────────────────────────────────────
print("\n=== 4. data_manager ===")
from core.data_manager import DataManager

# Use a temp DB so we don't pollute data/signal_system.db
_tmp_db = tempfile.mktemp(suffix=".db")
dm = DataManager(db_path=_tmp_db)

def test_dm_save_load_plan():
    fd   = FlowData(flows=config.DEFAULT_FLOWS.copy())
    plan = engine.compute(fd)
    dm.save_plan(plan)
    loaded = dm.load_plan(plan.plan_id)
    assert loaded is not None
    assert loaded.cycle == plan.cycle
    assert loaded.green_times == plan.green_times
    return f"saved & loaded plan_id={plan.plan_id}"

def test_dm_list_plans():
    plans = dm.list_plans()
    assert len(plans) >= 1
    return f"{len(plans)} plan(s) in DB"

def test_dm_upsert_plan():
    plans = dm.list_plans()
    plan = plans[0]
    plan.note = "updated"
    dm.save_plan(plan)
    loaded = dm.load_plan(plan.plan_id)
    assert loaded.note == "updated"
    return "upsert OK"

def test_dm_delete_plan():
    plan = engine.default_plan()
    plan.note = "to_delete"
    dm.save_plan(plan)
    assert dm.load_plan(plan.plan_id) is not None
    ok = dm.delete_plan(plan.plan_id)
    assert ok
    assert dm.load_plan(plan.plan_id) is None
    return "delete OK"

def test_dm_save_flow():
    fd = FlowData(flows=config.DEFAULT_FLOWS.copy())
    fd.compute_critical_ratios(IntersectionParam())
    row_id = dm.save_flow(fd)
    assert isinstance(row_id, int) and row_id > 0
    last = dm.load_last_flow()
    assert last is not None
    assert last.flows == fd.flows
    return f"flow saved (id={row_id}), reloaded OK"

def test_dm_save_result():
    plans  = dm.list_plans()
    result = SimResult(plan_id=plans[0].plan_id,
                       sim_duration=600, avg_delay=28.4,
                       avg_queue=35.0,   throughput=380,
                       speed_factor=5.0)
    rid = dm.save_result(result)
    assert rid > 0 and result.id == rid
    results = dm.list_results()
    assert any(r.id == rid for r in results)
    return f"result saved (id={rid})"

def test_dm_export_csv():
    path = _tmp_db.replace(".db", "_results.csv")
    n = dm.export_results_csv(path)
    assert n >= 1
    assert os.path.exists(path)
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    assert len(lines) == n + 1   # header + n data rows
    return f"exported {n} row(s) to {os.path.basename(path)}"

check("dm: save & load plan",    test_dm_save_load_plan)
check("dm: list_plans",          test_dm_list_plans)
check("dm: upsert plan",         test_dm_upsert_plan)
check("dm: delete plan",         test_dm_delete_plan)
check("dm: save & load flow",    test_dm_save_flow)
check("dm: save result",         test_dm_save_result)
check("dm: export results CSV",  test_dm_export_csv)

dm.close()
try:
    os.remove(_tmp_db)
except Exception:
    pass

# ─── 5. traci_adapter — structure tests (no live SUMO) ───────────────────────
print("\n=== 5. traci_adapter structure ===")
from core.traci_adapter import TraCIAdapter

def test_adapter_initial_state():
    a = TraCIAdapter()
    assert not a.is_connected
    assert a.link_map == {}
    return "TraCIAdapter() initial state OK"

def test_adapter_disconnect_safe_when_not_connected():
    a = TraCIAdapter()
    a.disconnect()   # must not raise
    return "disconnect() on unconnected adapter is safe"

check("TraCIAdapter init state",          test_adapter_initial_state)
check("disconnect() when disconnected",   test_adapter_disconnect_safe_when_not_connected)

# ─── 6. sim_controller — structure tests (no live SUMO / no Qt app) ──────────
print("\n=== 6. sim_controller structure ===")
from core.sim_controller import SimController

def test_sim_controller_importable():
    assert hasattr(SimController, "status_updated")
    assert hasattr(SimController, "sim_finished")
    assert hasattr(SimController, "error_occurred")
    assert hasattr(SimController, "set_speed")
    assert hasattr(SimController, "stop")
    assert hasattr(SimController, "request_timing_change")
    return "SimController has required signals and methods"

def test_sim_controller_speed_clamping():
    """set_speed clamps to [SIM_SPEED_MIN, SIM_SPEED_MAX] without a live adapter."""
    import config as _cfg
    # Minimal stub — we only test the speed logic, no QThread.start()
    a   = TraCIAdapter()        # not connected; just a valid object
    _dm = DataManager(db_path=tempfile.mktemp(suffix=".db"))
    fd  = FlowData(flows=_cfg.DEFAULT_FLOWS.copy())
    plan = engine.compute(fd)
    ctrl = SimController(adapter=a, data_manager=_dm, plan=plan)
    ctrl.set_speed(0)   # below min
    assert ctrl.get_current_speed() == _cfg.SIM_SPEED_MIN
    ctrl.set_speed(999) # above max
    assert ctrl.get_current_speed() == _cfg.SIM_SPEED_MAX
    ctrl.set_speed(10)
    assert ctrl.get_current_speed() == 10
    return f"clamped to [{_cfg.SIM_SPEED_MIN}, {_cfg.SIM_SPEED_MAX}], mid=10 OK"

def test_sim_controller_pending_plan_stored():
    import config as _cfg
    a    = TraCIAdapter()
    _dm  = DataManager(db_path=tempfile.mktemp(suffix=".db"))
    plan = engine.default_plan()
    ctrl = SimController(adapter=a, data_manager=_dm, plan=plan)
    new_plan = engine.plan_from_green_times(30, 12, 20, 10)
    ctrl.request_timing_change(new_plan)
    assert ctrl._pending_plan is new_plan
    ctrl.stop()
    assert not ctrl._running
    return "pending plan stored; stop() sets _running=False"

check("SimController signals and methods",    test_sim_controller_importable)
check("set_speed() clamps correctly",         test_sim_controller_speed_clamping)
check("request_timing_change / stop()",       test_sim_controller_pending_plan_stored)

# ─── Summary ──────────────────────────────────────────────────────────────────
print("\n" + "="*50)
passed = sum(1 for _, ok, _ in _results if ok)
failed = sum(1 for _, ok, _ in _results if not ok)
print(f"  {passed} passed  |  {failed} failed  |  {len(_results)} total")
if failed:
    print("\nFailed tests:")
    for label, ok, err in _results:
        if not ok:
            print(f"  - {label}: {err}")
print("="*50)
sys.exit(0 if failed == 0 else 1)
