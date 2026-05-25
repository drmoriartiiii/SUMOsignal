"""
core/timing_engine.py — Signal timing computation engine.

Wraps the Webster algorithm from data_models and provides plan
management helpers (validation, cycle adjustment, defaults).

Four phases are fully INDEPENDENT:
  Phase 0: N-S 直行  (green_times[0])
  Phase 1: N-S 左转  (green_times[1])
  Phase 2: E-W 直行  (green_times[2])
  Phase 3: E-W 左转  (green_times[3])

Each phase has its own green time and yellow time.
For automatic (Webster) plans, all yellow_times are uniform (param.yellow_time).
For manual plans, the user may set yellow_times independently per phase.
"""

from __future__ import annotations

from typing import List, Optional

import config
from core.data_models import FlowData, IntersectionParam, TimingPlan, webster


class TimingEngine:
    """
    Compute and validate 4-phase fixed-time signal timing plans.

    Usage::
        engine = TimingEngine()
        flow   = FlowData(flows=config.DEFAULT_FLOWS)
        plan   = engine.compute(flow)                      # Webster optimal
        plan   = engine.plan_from_green_times(32, 16, 22, 10)  # manual
        errors = engine.validate(plan)                     # [] if OK
    """

    def __init__(self, param: Optional[IntersectionParam] = None) -> None:
        self._param: IntersectionParam = param or IntersectionParam(
            sat_flow=config.SAT_FLOW_RATE,
            num_phases=config.NUM_PHASES,
            loss_time=config.LOSS_TIME_PER_PHASE,
            yellow_time=config.DEFAULT_YELLOW_TIME,
            all_red=config.DEFAULT_ALL_RED_TIME,
            min_green=config.DEFAULT_MIN_GREEN,
            max_cycle=config.DEFAULT_MAX_CYCLE,
            min_cycle=config.DEFAULT_MIN_CYCLE,
        )

    @property
    def param(self) -> IntersectionParam:
        return self._param

    # ── Main entry point ───────────────────────────────────────────────────────

    def compute(self, flow_data: FlowData) -> TimingPlan:
        """
        Validate flow_data, run Webster, and return an optimal TimingPlan.
        Yellow times are fixed at param.yellow_time (uniform) for auto plans.

        Raises:
            ValueError: if flow_data.validate() returns any errors.
        """
        errors = flow_data.validate()
        if errors:
            raise ValueError("流量数据无效:\n" + "\n".join(errors))
        return webster(flow_data, self._param)

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self, plan: TimingPlan) -> List[str]:
        """Return constraint-violation messages (empty list = valid)."""
        return plan.validate(min_green=self._param.min_green)

    def is_valid(self, plan: TimingPlan) -> bool:
        return len(self.validate(plan)) == 0

    # ── Cycle adjustment ───────────────────────────────────────────────────────

    def intergreen_total(self, plan: TimingPlan) -> float:
        """Total intergreen time = Σyellow_times + num_phases × all_red."""
        return sum(plan.yellow_times) + plan.num_phases * plan.all_red

    def actual_cycle(self, plan: TimingPlan) -> float:
        """Recalculate actual cycle from a plan's current green and yellow times."""
        return sum(plan.green_times) + self.intergreen_total(plan)

    def adjust_to_cycle(self,
                        plan: TimingPlan,
                        target_cycle: float) -> TimingPlan:
        """
        Return a new TimingPlan with green times scaled so the cycle equals
        target_cycle, while preserving yellow_times and respecting min_green.

        Raises:
            ValueError: if target_cycle is too short for min_green constraints.
        """
        ig_total = self.intergreen_total(plan)
        available = target_cycle - ig_total
        min_required = plan.num_phases * self._param.min_green
        if available < min_required:
            raise ValueError(
                f"目标周期 {target_cycle:.0f}s 太短 "
                f"(最短可行周期 = {min_required + ig_total:.0f}s)"
            )
        total_g = sum(plan.green_times)
        if total_g > 0:
            raw = [g / total_g * available for g in plan.green_times]
        else:
            raw = [available / plan.num_phases] * plan.num_phases

        green_times = [max(g, self._param.min_green) for g in raw]

        # If min_green clamping caused the sum to exceed `available`,
        # scale back the phases that are still above the minimum.
        excess = sum(green_times) - available
        if excess > 0.05:
            scalable = [i for i, g in enumerate(green_times)
                        if g > self._param.min_green]
            scalable_sum = sum(green_times[i] - self._param.min_green
                               for i in scalable)
            if scalable_sum > 0:
                for i in scalable:
                    share = (green_times[i] - self._param.min_green) / scalable_sum
                    green_times[i] = max(
                        green_times[i] - share * excess,
                        self._param.min_green,
                    )

        green_times = [round(g, 1) for g in green_times]
        actual = round(sum(green_times) + ig_total)
        return TimingPlan(
            cycle=float(actual),
            green_times=green_times,
            yellow_times=list(plan.yellow_times),
            all_red=plan.all_red,
            note=plan.note,
        )

    # ── Convenience constructors ───────────────────────────────────────────────

    def default_plan(self) -> TimingPlan:
        """Return the default 96 s plan from config (note='manual').
        Yellow times are uniform at param.yellow_time."""
        cfg = config.DEFAULT_GREEN_TIMES
        green = [
            cfg["NS_through"],
            cfg["NS_left"],
            cfg["EW_through"],
            cfg["EW_left"],
        ]
        y = [self._param.yellow_time] * 4
        cycle = (sum(green)
                 + sum(y)
                 + self._param.num_phases * self._param.all_red)
        return TimingPlan(
            cycle=round(cycle),
            green_times=green,
            yellow_times=y,
            all_red=self._param.all_red,
            note="manual",
        )

    def plan_from_green_times(self,
                              ns_through: float,
                              ns_left: float,
                              ew_through: float,
                              ew_left: float,
                              yellow_times: Optional[List[float]] = None,
                              all_red: Optional[float] = None) -> TimingPlan:
        """
        Build a TimingPlan directly from four independent green times.

        Args:
            ns_through, ns_left, ew_through, ew_left: green durations (s).
            yellow_times: per-phase yellow durations.  Defaults to
                          [param.yellow_time]*4 when None.
            all_red:      all-red duration (s).  Defaults to param.all_red.

        Raises:
            ValueError: if the resulting plan fails validation.
        """
        yt = yellow_times if yellow_times is not None else [self._param.yellow_time] * 4
        ar = all_red if all_red is not None else self._param.all_red
        green = [float(ns_through), float(ns_left),
                 float(ew_through), float(ew_left)]
        cycle = sum(green) + sum(yt) + self._param.num_phases * ar
        plan = TimingPlan(
            cycle=round(cycle, 1),
            green_times=[round(v, 1) for v in green],
            yellow_times=[round(v, 1) for v in yt],
            all_red=ar,
            note="manual",
        )
        errors = self.validate(plan)
        if errors:
            raise ValueError("配时方案约束违反:\n" + "\n".join(errors))
        return plan

    # ── Diagnostics ────────────────────────────────────────────────────────────

    def flow_summary(self, flow_data: FlowData) -> str:
        """One-line text summary of flow critical ratios and saturation."""
        if not flow_data.critical_ratios:
            flow_data.compute_critical_ratios(self._param)
        labels = ["NS直", "NS左", "EW直", "EW左"]
        parts = [f"{l}={r:.3f}"
                 for l, r in zip(labels, flow_data.critical_ratios)]
        status = "过饱和" if flow_data.Y >= 1.0 else "正常"
        return f"Y={flow_data.Y:.3f} ({status})  " + "  ".join(parts)

    def plan_summary(self, plan: TimingPlan) -> str:
        """One-line text summary of a timing plan."""
        labels = config.PHASE_DISPLAY_NAMES
        parts = [f"{labels[i]}={g:.0f}s"
                 for i, g in enumerate(plan.green_times)]
        errors = self.validate(plan)
        status = "" if not errors else f"  [!] {errors[0]}"
        return f"C={plan.cycle:.0f}s  " + "  ".join(parts) + status
