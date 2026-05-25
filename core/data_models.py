"""
core/data_models.py — Core data structures for the SUMO signal timing system.

Defines:
  IntersectionParam  — geometric and timing constants for the intersection
  FlowData           — per-movement traffic volumes + critical-flow-ratio logic
  TimingPlan         — a complete 4-phase fixed-time signal plan
  SimStatus          — real-time snapshot of one simulation step
  SimResult          — aggregated result stored in the database
  ScheduleEntry      — one timed slot in a multi-period signal schedule
  NamedScenario      — named combination of timing plan + flow data

  webster()          — Webster (1958) optimal-cycle algorithm
  build_tl_states()  — SUMO traffic-light state-string builder
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4


# ─── Intersection parameters ───────────────────────────────────────────────────
@dataclass
class IntersectionParam:
    """
    Physical and timing constants that describe the intersection geometry.

    These defaults match the design specification; individual instances can
    override any field before passing to webster().
    """
    sat_flow: float = 1800.0        # saturation flow rate S  (veh/h/lane)
    num_phases: int = 4             # number of signal phases
    loss_time: float = 3.0          # lost time per phase l_i  (s)
    yellow_time: float = 3.0        # yellow clearance interval (s)
    all_red: float = 1.0            # all-red clearance interval (s)
    min_green: float = 10.0         # minimum green time per phase (s)
    max_cycle: float = 180.0        # maximum cycle length cap (s)
    min_cycle: float = 30.0         # minimum cycle length cap (s)
    lanes_per_approach: int = 1     # signal-controlled lanes per movement

    @property
    def total_loss_time(self) -> float:
        """Total cycle lost time  L = n × l_i."""
        return self.num_phases * self.loss_time

    @property
    def intergreen_time(self) -> float:
        """Time between end of green and start of next green (yellow + all-red)."""
        return self.yellow_time + self.all_red


# ─── Traffic flow data ─────────────────────────────────────────────────────────
@dataclass
class FlowData:
    """
    Per-movement traffic volumes (veh/h) at a 4-approach, 4-phase intersection.

    Dictionary key convention: "<Direction>_<movement>"
      Directions : N, S, E, W
      Movements  : through, left, right

    Example::
        FlowData(flows={"N_through": 620, "N_left": 180,
                         "S_through": 580, "S_left": 160,
                         "E_through": 430, "E_left": 120,
                         "W_through": 460, "W_left": 140})
    """
    flows: Dict[str, float] = field(default_factory=dict)

    # Populated by compute_critical_ratios()
    critical_ratios: List[float] = field(default_factory=list)
    Y: float = 0.0   # Σy_i — sum of critical flow ratios

    _VALID_KEYS = frozenset({
        "N_through", "N_left", "N_right",
        "S_through", "S_left", "S_right",
        "E_through", "E_left", "E_right",
        "W_through", "W_left", "W_right",
    })

    def compute_critical_ratios(self, param: IntersectionParam) -> None:
        """
        Derive one critical flow ratio y_i for each of the 4 signal phases.

        Phase assignments (opposing approaches share one phase):
          Phase 1 (NS through) : y1 = max(N_through, S_through) / (S × lanes)
          Phase 2 (NS left)    : y2 = max(N_left,    S_left)    / (S × lanes)
          Phase 3 (EW through) : y3 = max(E_through, W_through) / (S × lanes)
          Phase 4 (EW left)    : y4 = max(E_left,    W_left)    / (S × lanes)
        """
        S = param.sat_flow * param.lanes_per_approach
        f = self.flows
        y1 = max(f.get("N_through", 0.0), f.get("S_through", 0.0)) / S
        y2 = max(f.get("N_left",    0.0), f.get("S_left",    0.0)) / S
        y3 = max(f.get("E_through", 0.0), f.get("W_through", 0.0)) / S
        y4 = max(f.get("E_left",    0.0), f.get("W_left",    0.0)) / S
        self.critical_ratios = [y1, y2, y3, y4]
        self.Y = y1 + y2 + y3 + y4

    def validate(self) -> List[str]:
        """
        Check flow values for basic sanity.

        Returns:
            A (possibly empty) list of human-readable error strings.
        """
        errors: List[str] = []
        for key, val in self.flows.items():
            if key not in self._VALID_KEYS:
                errors.append(f"未知流量键: {key!r}")
            elif not isinstance(val, (int, float)):
                errors.append(f"{key}: 必须是数值 (当前: {val!r})")
            elif val < 0:
                errors.append(f"{key}: 流量不能为负 ({val})")
            elif val > 9999:
                errors.append(f"{key}: 流量超出范围 (最大 9999 veh/h, 当前 {val})")
        return errors

    def to_json(self) -> str:
        return json.dumps(self.flows, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "FlowData":
        return cls(flows=json.loads(s))


# ─── Signal timing plan ────────────────────────────────────────────────────────
@dataclass
class TimingPlan:
    """
    A complete 4-phase fixed-time signal plan.

    green_times[i]   — effective green duration for phase i (seconds).
    yellow_times[i]  — yellow (amber) duration for phase i (seconds).
                       Can vary per phase; drivers can still pass on yellow.
    all_red          — all-red clearance interval, fixed for all phases (s).

    Phase order is always: [NS_through, NS_left, EW_through, EW_left].

    Cycle identity:
        cycle = Σgreen_times + Σyellow_times + num_phases × all_red
    """
    plan_id: str = field(default_factory=lambda: f"plan_{uuid4().hex[:6]}")
    name: str = ""          # user-visible display name (editable, distinct from plan_id)
    cycle: float = 96.0
    green_times: List[float] = field(
        default_factory=lambda: [32.0, 16.0, 22.0, 10.0]
    )
    yellow_times: List[float] = field(
        default_factory=lambda: [3.0, 3.0, 3.0, 3.0]
    )
    all_red: float = 1.0
    phase_order: List[str] = field(
        default_factory=lambda: ["NS_through", "NS_left", "EW_through", "EW_left"]
    )
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    note: str = ""   # "auto" | "auto_oversaturated" | "manual"

    @property
    def num_phases(self) -> int:
        return len(self.green_times)

    @property
    def intergreen(self) -> float:
        """Mean intergreen per phase (for backward-compatible scalar usage)."""
        n = self.num_phases or 1
        return sum(self.yellow_times) / n + self.all_red

    @property
    def intergreen_times(self) -> List[float]:
        """Per-phase intergreen list: yellow_times[i] + all_red."""
        return [y + self.all_red for y in self.yellow_times]

    def validate(self, min_green: float = 10.0) -> List[str]:
        """Return list of constraint-violation messages (empty = valid)."""
        errors: List[str] = []
        if len(self.green_times) != 4:
            errors.append(f"相位数必须为 4，当前: {len(self.green_times)}")
            return errors
        if len(self.yellow_times) != 4:
            errors.append(f"yellow_times 长度必须为 4，当前: {len(self.yellow_times)}")
        for i, g in enumerate(self.green_times):
            if g < min_green:
                errors.append(
                    f"相位 {i+1} 绿灯时长 {g:.1f}s < 最小绿灯 {min_green}s"
                )
        for i, y in enumerate(self.yellow_times):
            if y < 1.0:
                errors.append(f"相位 {i+1} 黄灯时长 {y:.1f}s < 最小 1s")
            if y > 10.0:
                errors.append(f"相位 {i+1} 黄灯时长 {y:.1f}s > 最大 10s")
        expected = (sum(self.green_times)
                    + sum(self.yellow_times)
                    + self.num_phases * self.all_red)
        if abs(expected - self.cycle) > 1.0:
            errors.append(
                f"周期不一致：∑(green+yellow+all_red)={expected:.1f}s ≠ cycle={self.cycle:.1f}s"
            )
        return errors

    # ── Database serialisation helpers ─────────────────────────────────────────
    def to_db_dict(self) -> dict:
        return {
            "plan_id":           self.plan_id,
            "name":              self.name,
            "cycle":             self.cycle,
            "green_times_json":  json.dumps(self.green_times),
            "yellow_times_json": json.dumps(self.yellow_times),
            "phase_order_json":  json.dumps(self.phase_order),
            "all_red":           self.all_red,
            "created_at":        self.created_at,
            "note":              self.note,
        }

    @classmethod
    def from_db_dict(cls, d: dict) -> "TimingPlan":
        # Backward compat: old rows may have 'yellow' (float) instead of yellow_times_json
        if "yellow_times_json" in d and d["yellow_times_json"]:
            yellow_times = json.loads(d["yellow_times_json"])
        elif "yellow" in d:
            y = float(d["yellow"])
            yellow_times = [y, y, y, y]
        else:
            yellow_times = [3.0, 3.0, 3.0, 3.0]

        return cls(
            plan_id=d["plan_id"],
            name=d.get("name", ""),
            cycle=float(d["cycle"]),
            green_times=json.loads(d["green_times_json"]),
            yellow_times=yellow_times,
            phase_order=json.loads(d["phase_order_json"]),
            all_red=float(d.get("all_red", 1.0)),
            created_at=d.get("created_at", ""),
            note=d.get("note", ""),
        )

    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = [f"{n}={g:.0f}s"
                 for n, g in zip(self.phase_order, self.green_times)]
        label = self.name or self.plan_id
        return f"[{label}] C={self.cycle:.0f}s  " + "  ".join(parts)


# ─── Named scenario (timing plan + flow data, saved with a user label) ─────────
@dataclass
class NamedScenario:
    """
    A named combination of a timing plan and flow data.

    Saved to the database so users can recall and schedule scenarios by name.
    """
    name: str                                        # user-visible label
    plan: TimingPlan = field(default_factory=TimingPlan)
    flow: Optional[FlowData] = None
    scenario_id: str = field(default_factory=lambda: f"sc_{uuid4().hex[:6]}")
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_db_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "name":        self.name,
            "plan_json":   json.dumps(self.plan.to_db_dict()),
            "flow_json":   self.flow.to_json() if self.flow else "{}",
            "created_at":  self.created_at,
        }

    @classmethod
    def from_db_dict(cls, d: dict) -> "NamedScenario":
        plan = TimingPlan.from_db_dict(json.loads(d["plan_json"]))
        flow_raw = json.loads(d.get("flow_json", "{}"))
        flow = FlowData(flows=flow_raw) if flow_raw else None
        return cls(
            name=d["name"],
            plan=plan,
            flow=flow,
            scenario_id=d["scenario_id"],
            created_at=d.get("created_at", ""),
        )


# ─── Schedule entry (one time-slot in a multi-period plan) ─────────────────────
@dataclass
class ScheduleEntry:
    """
    One slot in a multi-period signal schedule.

    The SimController applies `plan` (and optionally updates flows) when
    the simulation clock reaches `start_sim_time`.
    """
    start_sim_time: float     # simulation clock (s) at which this entry activates
    end_sim_time: float       # simulation clock (s) at which this entry ends
    scenario_name: str = ""   # display label (for export / status bar)
    plan: Optional[TimingPlan] = None
    flow: Optional[FlowData] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end_sim_time - self.start_sim_time)


# ─── Real-time simulation status ───────────────────────────────────────────────
@dataclass
class SimStatus:
    """
    Snapshot of the simulation state captured at the end of each step.

    Emitted by SimController as a Qt signal payload and displayed in the
    monitor panel.
    """
    sim_time: float = 0.0                   # current simulation clock (s)
    phase_index: int = 0                    # index in tlLogic phase list (0-11)
    phase_remaining: float = 0.0           # time left in current TL phase (s)
    queue_lengths: Dict[str, float] = field(default_factory=dict)
    # {"N": 0.0, "S": 0.0, "E": 0.0, "W": 0.0}  — halting-queue length (m)
    avg_delay: float = 0.0                 # mean network-wide vehicle delay (s/veh)
    delay_by_approach: Dict[str, float] = field(default_factory=dict)
    # {"N": x, "S": x, "E": x, "W": x}  — mean delay per approach (s/veh)
    delay_by_movement: Dict[str, float] = field(default_factory=dict)
    # {"N_through": x, "N_left": x, "N_right": x, ...}  — 12 movement keys
    throughput: int = 0                    # vehicles that completed their route
    vehicle_count: int = 0                 # vehicles currently in network

    @property
    def green_phase_number(self) -> int:
        """Return 1-4 indicating which of the 4 signal phases is active.

        Phase groups: 0-2 → phase 1, 3-5 → phase 2, 6-8 → phase 3, 9-11 → phase 4
        Returns 0 if in a yellow or all-red interval.
        """
        green_indices = {0: 1, 3: 2, 6: 3, 9: 4}
        return green_indices.get(self.phase_index, 0)

    @property
    def sim_time_str(self) -> str:
        """Format simulation time as HH:MM:SS."""
        t = int(self.sim_time)
        h, remainder = divmod(t, 3600)
        m, s = divmod(remainder, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @property
    def total_queue(self) -> float:
        return sum(self.queue_lengths.values())


# ─── Aggregated simulation result (persisted to database) ─────────────────────
@dataclass
class SimResult:
    """Summary statistics from one complete simulation run."""
    plan_id: str = ""
    scenario_name: str = ""      # name of the active scenario (or "—")
    sim_duration: float = 0.0    # simulation steps run (s)
    avg_delay: float = 0.0       # mean network-wide vehicle delay (s/veh)
    avg_queue: float = 0.0       # mean total queue length across all approaches (m)
    throughput: int = 0          # total vehicles that completed route
    speed_factor: float = 1.0    # GUI speed multiplier used
    delay_by_approach: Dict[str, float] = field(default_factory=dict)
    # {"N": x, "S": x, "E": x, "W": x}  — mean delay per approach (s/veh)
    delay_by_movement: Dict[str, float] = field(default_factory=dict)
    # {"N_through": x, "N_left": x, "N_right": x, ...}  — 12 movement keys
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    id: Optional[int] = None     # filled in by DataManager after INSERT


# ─── Webster (1958) optimal-cycle algorithm ────────────────────────────────────
def webster(flow_data: FlowData,
            param: Optional[IntersectionParam] = None) -> TimingPlan:
    """
    Compute a fixed-time signal plan using the Webster optimal-cycle formula.

    Algorithm:
        L     = n × l_i                          (total lost time)
        C_opt = (1.5L + 5) / (1 - Y)            (optimal cycle, uncapped)
        C     = clamp(round(C_opt), min, max)    (practical cycle)
        g_i   = (C - L) × y_i / Y               (green split, before min-green)

    If Y ≥ 1 (over-saturated), the maximum cycle is used with equal splits.

    For automatic plans, yellow_times are fixed at [param.yellow_time]*4
    and all_red is fixed at param.all_red.

    Args:
        flow_data : FlowData instance.  critical_ratios will be computed if
                    not already populated.
        param     : IntersectionParam (uses default values when None).

    Returns:
        A new TimingPlan with note="auto" (or "auto_oversaturated").
    """
    if param is None:
        param = IntersectionParam()

    if not flow_data.critical_ratios:
        flow_data.compute_critical_ratios(param)

    L: float = param.total_loss_time
    Y: float = flow_data.Y

    if Y >= 1.0:
        C = param.max_cycle
        g_raw = [param.max_cycle / param.num_phases] * param.num_phases
        note = "auto_oversaturated"
    else:
        C_raw = (1.5 * L + 5.0) / (1.0 - Y)
        C = float(max(param.min_cycle, min(param.max_cycle, round(C_raw))))
        effective_green = C - L
        if Y > 0:
            g_raw = [effective_green * yi / Y
                     for yi in flow_data.critical_ratios]
        else:
            g_raw = [effective_green / param.num_phases] * param.num_phases
        note = "auto"

    # Apply min-green floor
    green_times = [max(g, param.min_green) for g in g_raw]

    # If clamping caused the sum to exceed effective_green, scale back the
    # phases that are above the minimum, preserving their relative proportions.
    effective_green = C - L
    total_g = sum(green_times)
    if total_g > effective_green + 0.5:
        scalable_idx = [i for i, g in enumerate(green_times)
                        if g > param.min_green]
        if scalable_idx:
            excess = total_g - effective_green
            scalable_sum = sum(green_times[i] - param.min_green
                               for i in scalable_idx)
            if scalable_sum > 0:
                for i in scalable_idx:
                    share = (green_times[i] - param.min_green) / scalable_sum
                    green_times[i] = max(green_times[i] - share * excess,
                                         param.min_green)

    green_times = [round(g, 1) for g in green_times]

    # Auto plans use uniform yellow_times (fixed); intergreen = yellow + all_red
    yellow_times = [param.yellow_time] * param.num_phases
    actual_cycle = round(sum(green_times)
                         + sum(yellow_times)
                         + param.num_phases * param.all_red)

    return TimingPlan(
        cycle=float(actual_cycle),
        green_times=green_times,
        yellow_times=yellow_times,
        all_red=param.all_red,
        note=note,
    )


# ─── Traffic-light state-string builder ────────────────────────────────────────
# linkIndex values are assigned by netconvert at net-generation time and are
# NOT predictable from the input files.  TraCIAdapter.build_link_map() reads
# them from the running simulation via traci.trafficlight.getControlledLinks()
# and passes the resulting dict here to produce correct state strings.

# Symbolic names for each controlled movement class
_MOVE_NS_THROUGH = "NS_through"  # N approach through + S approach through
_MOVE_NS_LEFT    = "NS_left"     # N approach left  + S approach left
_MOVE_EW_THROUGH = "EW_through"  # E approach through + W approach through
_MOVE_EW_LEFT    = "EW_left"     # E approach left  + W approach left
_MOVE_RIGHT      = "right"       # any free right turn (yield)


def build_tl_states(link_map: Dict[str, int],
                    total_links: int) -> Dict[str, str]:
    """
    Construct SUMO traffic-light state strings from a runtime link map.

    Args:
        link_map    : dict mapping movement label → linkIndex,
                      e.g. {"n2c_c2s": 3, "n2c_c2e": 5, ...}
                      Built by TraCIAdapter.build_link_map().
        total_links : total number of controlled connections at junction C.

    Returns:
        Dict[phase_name, state_string], e.g.:
            {"NS_THROUGH_GREEN": "rrrGrrGrgrg...", ...}
    """
    def _state(green_keys: list,
               yellow_keys: list,
               free_keys: list) -> str:
        chars = ["r"] * total_links
        for k in free_keys:
            if k in link_map:
                chars[link_map[k]] = "g"
        for k in green_keys:
            if k in link_map:
                chars[link_map[k]] = "G"
        for k in yellow_keys:
            if k in link_map:
                chars[link_map[k]] = "y"
        return "".join(chars)

    ns_through = ["n2c_c2s", "s2c_c2n"]
    ns_left    = ["n2c_c2e", "s2c_c2w"]
    ew_through = ["e2c_c2w", "w2c_c2e"]
    ew_left    = ["e2c_c2s", "w2c_c2n"]

    # Right-turn connections are everything not in the four controlled groups
    controlled = set(ns_through + ns_left + ew_through + ew_left)
    all_right  = [k for k in link_map if k not in controlled]

    return {
        "NS_THROUGH_GREEN":  _state(ns_through, [],         all_right),
        "NS_THROUGH_YELLOW": _state([],         ns_through, []),
        "NS_LEFT_GREEN":     _state(ns_left,    [],         all_right),
        "NS_LEFT_YELLOW":    _state([],         ns_left,    []),
        "EW_THROUGH_GREEN":  _state(ew_through, [],         all_right),
        "EW_THROUGH_YELLOW": _state([],         ew_through, []),
        "EW_LEFT_GREEN":     _state(ew_left,    [],         all_right),
        "EW_LEFT_YELLOW":    _state([],         ew_left,    []),
        "ALL_RED":           _state([],         [],         []),
    }

# Human-readable labels for the 4 green phases.
# Keyed by the position in TimingPlan.phase_order (0-3), NOT by linkIndex.
PHASE_LABELS: Dict[int, str] = {
    0: "N-S 直行",
    1: "N-S 左转",
    2: "E-W 直行",
    3: "E-W 左转",
}
