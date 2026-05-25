"""
core/traci_adapter.py — SUMO TraCI interface adapter.

ALL traci calls are isolated in this single module.  SimController and the
GUI must never import traci directly; they use this adapter instead.

Thread-safety contract
----------------------
All public methods (except the `is_connected` property) must be called from
the SAME thread that called connect().  In normal operation that thread is
SimController's worker thread.  The main Qt thread may call connect() /
disconnect() before / after the worker thread runs.

Lazy traci import
-----------------
`import traci` is deferred to connect() so that the rest of the project
can be imported and tested even when SUMO is not installed on the machine.
The adapter stores the traci module as `self._traci` after connect().

Per-movement delay
------------------
delay_by_movement tracks waiting time per 12 movements
(4 directions × {through, left, right}).  Each step, vehicles on incoming
edges are queried for their route ID (e.g. "route_N_through") to determine
their movement.  This costs one getRouteID + getWaitingTime call per vehicle
in the incoming zone, which is acceptable for a small single-intersection net.
"""

from __future__ import annotations

from typing import Dict, Optional

import config
from core.data_models import SimStatus, TimingPlan, build_tl_states

# Maps route_id → movement_key, built once in _build_route_map()
_ROUTE_PREFIX = "route_"


class TraCIAdapter:
    """
    Thin façade over the SUMO TraCI Python API.

    Typical lifecycle::

        adapter = TraCIAdapter()
        adapter.connect()               # launches SUMO, connects TraCI
        adapter.apply_timing(plan)      # installs 4-phase program
        for _ in range(steps):
            adapter.step()
            status = adapter.get_status()
            adapter.add_vehicles(flows)
        adapter.disconnect()
    """

    def __init__(self) -> None:
        self._traci = None           # set in connect()
        self._connected: bool = False
        self._link_map: Dict[str, int] = {}
        self._total_links: int = 0
        self._tl_states: Dict[str, str] = {}
        self._veh_counters: Dict[str, int] = {}
        self._arrived_total: int = 0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def link_map(self) -> Dict[str, int]:
        """Read-only view of the connection→linkIndex mapping (post-connect)."""
        return dict(self._link_map)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def connect(self, use_gui: bool = False) -> None:
        """
        Launch SUMO and open a TraCI connection.

        Uses traci.start() which spawns the subprocess and waits for the
        port to open internally — no manual Popen / sleep needed.

        Args:
            use_gui: launch sumo-gui instead of headless sumo.

        Raises:
            FileNotFoundError: SUMO binary not found.
            RuntimeError: traci could not be imported (SUMO_HOME not set).
        """
        try:
            import traci as _traci
        except ModuleNotFoundError:
            raise RuntimeError(
                "traci module not found.\n"
                "Make sure SUMO is installed and SUMO_HOME points to it.\n"
                f"Current SUMO_HOME: {config.SUMO_HOME!r}"
            )
        self._traci = _traci

        binary = config.SUMO_GUI_BINARY if use_gui else config.SUMO_BINARY
        cmd = [
            binary,
            "-c", config.CFG_FILE,
            "--quit-on-end",    # SUMO exits when sim time is reached
            "--no-step-log",    # suppress per-step console output
        ]
        # NOTE: do NOT add --remote-port here.
        # traci.start() auto-assigns a free port and appends it to cmd.
        _traci.start(cmd, label=config.TRACI_LABEL)

        self._connected = True
        self._arrived_total = 0
        self._veh_counters.clear()

        # Read the actual linkIndex mapping that netconvert assigned.
        self._link_map = self._build_link_map()
        # Pre-compute all state strings from the real mapping.
        self._tl_states = build_tl_states(self._link_map, self._total_links)

    def disconnect(self) -> None:
        """Close the TraCI connection. Safe to call when already disconnected."""
        if not self._connected:
            return
        try:
            self._traci.close()
        except Exception:
            pass
        self._connected = False

    # ── Link-map construction (called once after connect) ─────────────────────

    def _build_link_map(self) -> Dict[str, int]:
        """
        Query SUMO for the controlled-link list and build
        {from_edge + '_' + to_edge : linkIndex}.

        SUMO's getControlledLinks returns a list indexed by linkIndex;
        each element is a list of (from_lane, to_lane, via_lane) tuples.
        Lane IDs have the format "edgeID_laneNum", so we strip the last
        "_N" suffix to recover the edge ID.
        """
        controlled = self._traci.trafficlight.getControlledLinks(config.TL_ID)
        link_map: Dict[str, int] = {}
        for idx, conn_list in enumerate(controlled):
            if not conn_list:
                continue
            from_lane, to_lane, _via = conn_list[0]
            from_edge = from_lane.rsplit("_", 1)[0]
            to_edge   = to_lane.rsplit("_", 1)[0]
            key = f"{from_edge}_{to_edge}"
            # If two lanes share the same (from_edge, to_edge) pair we keep
            # only the first (lowest linkIndex) — sufficient for state strings.
            link_map.setdefault(key, idx)
        self._total_links = len(controlled)
        return link_map

    # ── Signal timing ─────────────────────────────────────────────────────────

    def apply_timing(self, plan: TimingPlan) -> None:
        """
        Push a 4-phase timing plan to junction C via TraCI.

        Builds 12 Phase objects (4 × [green, yellow, all-red]) and calls
        setProgramLogic to replace the running program atomically.  The new
        program starts at phase index 0 (NS through green).

        Per-phase yellow durations from plan.yellow_times are used, allowing
        different clearance intervals per signal phase in manual plans.
        """
        states = self._tl_states
        sequence = [
            (plan.green_times[0], "NS_THROUGH_GREEN", "NS_THROUGH_YELLOW"),
            (plan.green_times[1], "NS_LEFT_GREEN",    "NS_LEFT_YELLOW"),
            (plan.green_times[2], "EW_THROUGH_GREEN", "EW_THROUGH_YELLOW"),
            (plan.green_times[3], "EW_LEFT_GREEN",    "EW_LEFT_YELLOW"),
        ]

        phases = []
        for phase_idx, (green_dur, green_key, yellow_key) in enumerate(sequence):
            g  = int(green_dur)
            y  = int(plan.yellow_times[phase_idx])
            ar = int(plan.all_red)
            # minDur == maxDur == duration → static (non-actuated) phase
            phases.append(
                self._traci.trafficlight.Phase(g,  states[green_key],  g,  g))
            phases.append(
                self._traci.trafficlight.Phase(y,  states[yellow_key], y,  y))
            phases.append(
                self._traci.trafficlight.Phase(ar, states["ALL_RED"],  ar, ar))

        logic = self._traci.trafficlight.Logic(
            programID="python",
            type=0,                 # 0 = static
            currentPhaseIndex=0,
            phases=phases,
        )
        self._traci.trafficlight.setProgramLogic(config.TL_ID, logic)
        self._traci.trafficlight.setProgram(config.TL_ID, "python")

    # ── Per-step simulation control ───────────────────────────────────────────

    def step(self) -> None:
        """Advance the simulation by one step (step-length = 1 s)."""
        self._traci.simulationStep()

    # ── State reading ─────────────────────────────────────────────────────────

    def get_status(self) -> SimStatus:
        """
        Read the current state of the running simulation.

        Queue length estimation
        -----------------------
        SUMO does not expose a direct "queue length in metres" API.
        We approximate it as:  halting_vehicles × 7.5 m
        (5 m vehicle body + 2.5 m minimum gap), consistent with SUMO defaults.

        Average delay
        -------------
        We use the mean *waiting time* of all vehicles currently in the
        network.  Waiting time in SUMO = cumulative time spent at speed < 0.1
        m/s since last movement — a good proxy for signalised-intersection delay.

        Per-movement delay
        ------------------
        For each incoming edge we query all vehicle IDs, look up their route,
        and bin their waiting times into one of the 12 movement buckets
        (N/S/E/W × through/left/right).

        Throughput
        ----------
        We accumulate traci.simulation.getArrivedNumber() across all steps
        because the API returns only the delta per step.
        """
        t = self._traci

        sim_time        = t.simulation.getTime()
        phase_index     = t.trafficlight.getPhase(config.TL_ID)
        next_switch     = t.trafficlight.getNextSwitch(config.TL_ID)
        phase_remaining = max(0.0, next_switch - sim_time)

        # Accumulate arrivals
        self._arrived_total += t.simulation.getArrivedNumber()

        # Vehicle list (fetched once per step)
        veh_ids       = t.vehicle.getIDList()
        vehicle_count = len(veh_ids)

        # Network-wide average waiting time
        if veh_ids:
            avg_delay = (
                sum(t.vehicle.getWaitingTime(v) for v in veh_ids)
                / vehicle_count
            )
        else:
            avg_delay = 0.0

        # Per-approach queue + approach-level delay
        queue_lengths: Dict[str, float] = {}
        delay_by_approach: Dict[str, float] = {}
        for direction, edge_id in config.INCOMING_EDGES.items():
            halting = t.edge.getLastStepHaltingNumber(edge_id)
            queue_lengths[direction] = round(halting * 7.5, 1)
            wait_edge = t.edge.getWaitingTime(edge_id)
            veh_edge  = max(1, t.edge.getLastStepVehicleNumber(edge_id))
            delay_by_approach[direction] = round(wait_edge / veh_edge, 1)

        # Per-movement delay (12 buckets: direction × {through,left,right})
        delay_by_movement = self._calc_movement_delays(t)

        return SimStatus(
            sim_time=sim_time,
            phase_index=phase_index,
            phase_remaining=round(phase_remaining, 1),
            queue_lengths=queue_lengths,
            avg_delay=round(avg_delay, 2),
            delay_by_approach=delay_by_approach,
            delay_by_movement=delay_by_movement,
            throughput=self._arrived_total,
            vehicle_count=vehicle_count,
        )

    # ── Per-movement delay helper ─────────────────────────────────────────────

    def _calc_movement_delays(self, t) -> Dict[str, float]:
        """
        Compute per-movement average waiting time for vehicles currently
        on incoming edges.

        Movement key format: "{DIR}_{movement}" e.g. "N_through", "W_left".
        Each vehicle's movement is inferred from its route ID (set by
        add_vehicles() as "route_{DIR}_{movement}").
        """
        # Accumulate waiting times per movement bucket
        buckets: Dict[str, list] = {k: [] for k in config.ROUTE_IDS.keys()}

        for edge_id in config.INCOMING_EDGES.values():
            try:
                veh_ids = t.edge.getLastStepVehicleIDs(edge_id)
            except Exception:
                continue
            for vid in veh_ids:
                try:
                    route_id = t.vehicle.getRouteID(vid)
                except Exception:
                    continue
                # Route IDs are "route_{movement_key}", e.g. "route_N_through"
                if route_id.startswith(_ROUTE_PREFIX):
                    mvt_key = route_id[len(_ROUTE_PREFIX):]
                    if mvt_key in buckets:
                        try:
                            wait = t.vehicle.getWaitingTime(vid)
                            buckets[mvt_key].append(wait)
                        except Exception:
                            pass

        return {
            k: round(sum(v) / len(v), 1) if v else 0.0
            for k, v in buckets.items()
        }

    # ── Dynamic vehicle generation ────────────────────────────────────────────

    def add_vehicles(self,
                     flows: Dict[str, float],
                     sim_time: float = 0.0,
                     rng=None) -> int:
        """
        Stochastically insert vehicles each simulation step.

        Uses a Bernoulli(p) approximation of a Poisson arrival process:
            p = flow_veh_h / 3600   (probability of ≥1 arrival in 1 second)

        Vehicle IDs are prefixed with "dyn_" to distinguish them from the
        static demand defined in the route file.

        Args:
            flows   : {route_key: veh/h}, e.g. {"N_through": 620.0}.
                      Only keys present in config.ROUTE_IDS are used.
            sim_time: current simulation time (unused, kept for signature
                      compatibility with callers that pass it).
            rng     : optional random.Random for reproducible generation.

        Returns:
            Number of vehicles successfully inserted this step.
        """
        import random as _rng_module
        if rng is None:
            rng = _rng_module.Random()

        inserted = 0
        for route_key, vph in flows.items():
            if vph <= 0:
                continue
            if rng.random() >= vph / 3600.0:
                continue
            route_id = config.ROUTE_IDS.get(route_key)
            if route_id is None:
                continue
            cnt    = self._veh_counters.get(route_key, 0)
            veh_id = f"dyn_{route_key}_{cnt}"
            self._veh_counters[route_key] = cnt + 1
            try:
                self._traci.vehicle.add(
                    veh_id, route_id,
                    typeID="car",
                    depart="now",
                    departLane="best",
                    departSpeed="max",
                )
                inserted += 1
            except Exception:
                # Vehicle insertion can fail if the departure edge is full;
                # silently skip rather than crashing the simulation loop.
                pass
        return inserted
