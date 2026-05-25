"""
core/sim_controller.py — Simulation step loop, running inside a QThread.

Design overview
---------------
SimController owns the simulation lifecycle AFTER TraCIAdapter.connect() has
been called (either from the main thread or from connect_and_start()).

It drives the per-step loop:

    1. Check schedule — if sim_time has reached the next entry's start time,
       apply the new timing plan and update flow_dict.
    2. Apply any pending timing plan change (requested by GUI in main thread).
    3. Advance SUMO by one step via adapter.step().
    4. Optionally insert vehicles via adapter.add_vehicles().
    5. Read state via adapter.get_status() and emit status_updated.
    6. Sleep for (SIM_BASE_DELAY_MS / speed_factor) ms to pace replay speed.
    7. Repeat until stopped, schedule exhausted, or SUMO terminates.

On exit the controller:
    - Builds a SimResult from accumulated statistics.
    - Saves it to the database via DataManager.
    - Closes the TraCI connection (adapter.disconnect()).
    - Emits sim_finished(SimResult).

Thread safety
-------------
Main thread → worker thread communication uses two mechanisms:

    _running        : plain bool — safe under Python's GIL for single-assign.
    _speed          : int guarded by _lock for read-modify-write safety.
    _pending_plan   : Optional[TimingPlan] — GIL-protected single assignment.

Worker thread → main thread communication uses Qt cross-thread signals
(queued connections are created automatically when signal/slot are in
different threads).

Schedule support
----------------
Pass a list of ScheduleEntry to the constructor.  Each entry has:
    start_sim_time  — sim clock when the entry becomes active
    end_sim_time    — sim clock when it ends (determines loop termination)
    plan            — TimingPlan to apply (None = keep current)
    flow            — FlowData to use for vehicle generation (None = keep current)

If no schedule is provided, the controller runs for config.SIM_MAX_STEPS.
"""

from __future__ import annotations

import time
import threading
import random
from typing import Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

import config
from core.data_models import FlowData, ScheduleEntry, SimResult, SimStatus, TimingPlan
from core.traci_adapter import TraCIAdapter
from core.data_manager import DataManager


class SimController(QThread):
    """
    Worker thread that drives the SUMO simulation step loop.

    Signals
    -------
    status_updated(SimStatus)
        Emitted after every simulation step with the latest snapshot.
    sim_finished(SimResult)
        Emitted once when the loop ends; carries aggregated statistics.
    error_occurred(str)
        Emitted when an unexpected exception is raised inside run().
    """

    status_updated = pyqtSignal(object)   # payload: SimStatus
    sim_finished   = pyqtSignal(object)   # payload: SimResult
    error_occurred = pyqtSignal(str)

    def __init__(self,
                 adapter:       TraCIAdapter,
                 data_manager:  DataManager,
                 plan:          TimingPlan,
                 flows:         Optional[FlowData] = None,
                 auto_vehicles: bool = True,
                 schedule:      Optional[List[ScheduleEntry]] = None,
                 scenario_name: str = "",
                 parent=None) -> None:
        """
        Args:
            adapter       : Connected TraCIAdapter (connect() already called).
            data_manager  : DataManager for persisting SimResult on finish.
            plan          : Initial timing plan (already applied via adapter).
            flows         : Traffic flows for dynamic vehicle generation.
                            None → use config.DEFAULT_FLOWS.
            auto_vehicles : If True, call add_vehicles() each step.
            schedule      : Optional list of ScheduleEntry objects for time-
                            based plan switching.  Sorted by start_sim_time
                            internally.
            scenario_name : Label recorded in the SimResult for this run.
        """
        super().__init__(parent)
        self._adapter       = adapter
        self._dm            = data_manager
        self._plan          = plan
        self._flows         = flows
        self._auto_vehicles = auto_vehicles
        self._scenario_name = scenario_name

        # Sorted schedule; None means single-plan run
        if schedule:
            self._schedule: List[ScheduleEntry] = sorted(
                schedule, key=lambda e: e.start_sim_time)
            self._schedule_idx = 0
            # Max steps driven by schedule end time
            last = self._schedule[-1].end_sim_time
            self._max_steps = int(last) if last > 0 else config.SIM_MAX_STEPS
        else:
            self._schedule = []
            self._schedule_idx = 0
            self._max_steps = config.SIM_MAX_STEPS

        self._running               = False
        self._speed: int            = config.SIM_SPEED_DEFAULT
        self._lock                  = threading.Lock()
        self._pending_plan: Optional[TimingPlan] = None

        # Running accumulators for final SimResult
        self._delay_samples: list  = []
        self._queue_samples: list  = []
        self._approach_delay_acc: Dict[str, list] = {d: [] for d in ("N", "S", "E", "W")}
        self._movement_delay_acc: Dict[str, list] = {k: [] for k in config.ROUTE_IDS}
        self._last_status: Optional[SimStatus] = None

    # ── Main-thread control interface ─────────────────────────────────────────

    def set_speed(self, factor: int) -> None:
        """Change simulation replay speed (1–20×). Thread-safe."""
        with self._lock:
            self._speed = max(config.SIM_SPEED_MIN,
                              min(config.SIM_SPEED_MAX, int(factor)))

    def stop(self) -> None:
        """Ask the simulation loop to exit after the current step."""
        self._running = False

    def request_timing_change(self, plan: TimingPlan) -> None:
        """
        Queue a new timing plan to be applied at the start of the next step.
        Thread-safe under CPython's GIL (single object-ref assignment).
        """
        self._pending_plan = plan

    def get_current_speed(self) -> int:
        with self._lock:
            return self._speed

    # ── QThread entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        """Simulation loop. Runs in the worker thread; use QThread.start()."""
        self._running = True
        rng = random.Random()

        flow_dict: Dict[str, float] = (
            self._flows.flows if self._flows is not None
            else config.DEFAULT_FLOWS
        )
        steps_done  = 0
        fatal_error = False

        try:
            while self._running and steps_done < self._max_steps:
                # ── Schedule: switch plan/flow if time has come ──────────────
                if self._schedule and self._schedule_idx < len(self._schedule):
                    entry = self._schedule[self._schedule_idx]
                    # Use steps_done as sim time proxy (step_length = 1 s)
                    if steps_done >= entry.start_sim_time:
                        if entry.plan is not None:
                            self._adapter.apply_timing(entry.plan)
                            self._plan = entry.plan
                        if entry.flow is not None:
                            flow_dict = entry.flow.flows
                        self._schedule_idx += 1

                # ── Apply pending GUI timing change ──────────────────────────
                pending = self._pending_plan
                if pending is not None:
                    self._adapter.apply_timing(pending)
                    self._plan   = pending
                    self._pending_plan = None

                # ── Advance simulation ───────────────────────────────────────
                try:
                    self._adapter.step()
                except Exception as exc:
                    msg = str(exc).lower()
                    if "closed" in msg or "connection" in msg or "end" in msg:
                        break
                    raise

                steps_done += 1

                # ── Optional dynamic vehicle insertion ───────────────────────
                if self._auto_vehicles:
                    self._adapter.add_vehicles(
                        flow_dict, sim_time=float(steps_done), rng=rng)

                # ── Collect state ────────────────────────────────────────────
                try:
                    status = self._adapter.get_status()
                except Exception:
                    break

                self._last_status = status
                self._delay_samples.append(status.avg_delay)
                self._queue_samples.append(status.total_queue)
                for d, v in status.delay_by_approach.items():
                    if d in self._approach_delay_acc:
                        self._approach_delay_acc[d].append(v)
                for k, v in status.delay_by_movement.items():
                    if k in self._movement_delay_acc:
                        self._movement_delay_acc[k].append(v)

                self.status_updated.emit(status)

                # ── Pacing ───────────────────────────────────────────────────
                with self._lock:
                    speed = self._speed
                time.sleep(config.SIM_BASE_DELAY_MS / (speed * 1000.0))

        except Exception as exc:
            fatal_error = True
            self.error_occurred.emit(str(exc))

        finally:
            self._adapter.disconnect()
            if not fatal_error:
                result = self._build_result(steps_done)
                try:
                    self._dm.save_result(result)
                except Exception:
                    pass
                self.sim_finished.emit(result)

    # ── Result construction ───────────────────────────────────────────────────

    def _build_result(self, steps: int) -> SimResult:
        """Aggregate per-step samples into a single SimResult."""
        n = len(self._delay_samples)
        avg_delay = sum(self._delay_samples) / n if n else 0.0
        avg_queue = sum(self._queue_samples) / n if n else 0.0

        throughput = (
            self._last_status.throughput if self._last_status is not None else 0
        )

        delay_by_approach = {
            d: round(sum(vals) / len(vals), 1) if vals else 0.0
            for d, vals in self._approach_delay_acc.items()
        }
        delay_by_movement = {
            k: round(sum(vals) / len(vals), 1) if vals else 0.0
            for k, vals in self._movement_delay_acc.items()
        }

        with self._lock:
            speed = self._speed

        return SimResult(
            plan_id=self._plan.plan_id,
            scenario_name=self._scenario_name,
            sim_duration=float(steps),
            avg_delay=round(avg_delay, 2),
            avg_queue=round(avg_queue, 2),
            throughput=throughput,
            speed_factor=float(speed),
            delay_by_approach=delay_by_approach,
            delay_by_movement=delay_by_movement,
        )
