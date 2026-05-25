"""
config.py — Global configuration for the SUMO Fixed-Signal Timing System.

All other modules import constants from here; nothing in this file imports
from the rest of the project (no circular dependencies).
"""

from __future__ import annotations

import os
import sys

# ─── SUMO environment detection ───────────────────────────────────────────────
SUMO_HOME: str = os.environ.get("SUMO_HOME", "")

if not SUMO_HOME:
    _CANDIDATES = [
        r"C:\Program Files (x86)\Eclipse\Sumo",
        r"C:\Program Files\Eclipse\Sumo",
        r"C:\Sumo",
        r"D:\Sumo",
    ]
    for _c in _CANDIDATES:
        if os.path.isdir(_c):
            SUMO_HOME = _c
            os.environ["SUMO_HOME"] = SUMO_HOME
            break

if SUMO_HOME:
    _tools = os.path.join(SUMO_HOME, "tools")
    if _tools not in sys.path:
        sys.path.append(_tools)

# ─── SUMO binary paths ─────────────────────────────────────────────────────────
_bin = os.path.join(SUMO_HOME, "bin") if SUMO_HOME else ""

SUMO_BINARY: str = os.path.join(_bin, "sumo.exe") if os.path.isfile(
    os.path.join(_bin, "sumo.exe")) else "sumo"

SUMO_GUI_BINARY: str = os.path.join(_bin, "sumo-gui.exe") if os.path.isfile(
    os.path.join(_bin, "sumo-gui.exe")) else "sumo-gui"

# ─── File paths ────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
SUMO_NET_DIR: str = os.path.join(BASE_DIR, "sumo_net")
DATA_DIR: str = os.path.join(BASE_DIR, "data")

NET_FILE: str = os.path.join(SUMO_NET_DIR, "intersection.net.xml")
ROU_FILE: str = os.path.join(SUMO_NET_DIR, "intersection.rou.xml")
CFG_FILE: str = os.path.join(SUMO_NET_DIR, "intersection.sumocfg")
DB_PATH: str = os.path.join(DATA_DIR, "signal_system.db")

# ─── TraCI connection ──────────────────────────────────────────────────────────
TRACI_PORT: int = 8813
TRACI_HOST: str = "localhost"
TRACI_LABEL: str = "sumo_signal"

# ─── Simulation parameters ─────────────────────────────────────────────────────
SIM_STEP_LENGTH: float = 1.0      # length of one simulation step (s)
SIM_MAX_STEPS: int = 3600         # maximum steps before auto-stop (= 1 h)
SIM_SPEED_DEFAULT: int = 5        # default speed multiplier (1–20)
SIM_SPEED_MIN: int = 1
SIM_SPEED_MAX: int = 20

# Delay between simulation steps at speed factor 1× (ms).
# Actual delay = SIM_BASE_DELAY_MS / speed_factor
SIM_BASE_DELAY_MS: int = 1000

# ─── Intersection topology ─────────────────────────────────────────────────────
TL_ID: str = "C"               # SUMO traffic-light junction ID
TL_PROGRAM_ID: str = "default" # programID in the tlLogic XML

# Phase sequence constants.
# The 4-phase plan is encoded as groups of 3 SUMO phases:
#   [GREEN, YELLOW, ALL_RED] × 4 = 12 total phases
# Phase *index* within a TimingPlan.green_times list (0-3):
PHASE_IDX_NS_THROUGH: int = 0
PHASE_IDX_NS_LEFT:    int = 1
PHASE_IDX_EW_THROUGH: int = 2
PHASE_IDX_EW_LEFT:    int = 3
NUM_SIGNAL_PHASES:    int = 4

PHASE_DISPLAY_NAMES: dict[int, str] = {
    PHASE_IDX_NS_THROUGH: "N-S 直行",
    PHASE_IDX_NS_LEFT:    "N-S 左转",
    PHASE_IDX_EW_THROUGH: "E-W 直行",
    PHASE_IDX_EW_LEFT:    "E-W 左转",
}

# NOTE: SUMO linkIndex values for each connection at junction C are assigned
# by netconvert at build time and cannot be predicted from source files.
# TraCIAdapter.build_link_map() reads them at runtime via TraCI and passes
# the resulting dict to core.data_models.build_tl_states() to produce
# correct per-phase state strings.

# Edge IDs for each approach / departure direction
INCOMING_EDGES: dict[str, str] = {
    "N": "n2c",
    "S": "s2c",
    "E": "e2c",
    "W": "w2c",
}
OUTGOING_EDGES: dict[str, str] = {
    "N": "c2n",
    "S": "c2s",
    "E": "c2e",
    "W": "c2w",
}

# Route IDs used by SimController.add_vehicles()
ROUTE_IDS: dict[str, str] = {
    "N_through": "route_N_through",
    "N_left":    "route_N_left",
    "N_right":   "route_N_right",
    "S_through": "route_S_through",
    "S_left":    "route_S_left",
    "S_right":   "route_S_right",
    "E_through": "route_E_through",
    "E_left":    "route_E_left",
    "E_right":   "route_E_right",
    "W_through": "route_W_through",
    "W_left":    "route_W_left",
    "W_right":   "route_W_right",
}

# ─── Signal timing constraints ─────────────────────────────────────────────────
DEFAULT_YELLOW_TIME: float = 3.0   # yellow duration (s) — fixed
DEFAULT_ALL_RED_TIME: float = 1.0  # all-red clearance (s) — fixed
DEFAULT_MIN_GREEN: float = 10.0    # minimum green per phase (s)
DEFAULT_MAX_CYCLE: float = 180.0   # maximum allowed cycle length (s)
DEFAULT_MIN_CYCLE: float = 30.0    # minimum allowed cycle length (s)

# Default green times matching the initial tll.xml (cycle = 96 s)
DEFAULT_GREEN_TIMES: dict[str, float] = {
    "NS_through": 32.0,
    "NS_left":    16.0,
    "EW_through": 22.0,
    "EW_left":    10.0,
}

# ─── Webster algorithm parameters ──────────────────────────────────────────────
SAT_FLOW_RATE: float = 1800.0     # saturation flow rate S (veh/h/lane)
NUM_PHASES: int = 4               # number of signal phases
LOSS_TIME_PER_PHASE: float = 3.0  # lost time per phase l_i (s)

# ─── Default traffic flows (veh/h) — design-document example ──────────────────
DEFAULT_FLOWS: dict[str, float] = {
    "N_through": 620.0,
    "N_left":    180.0,
    "S_through": 580.0,
    "S_left":    160.0,
    "E_through": 430.0,
    "E_left":    120.0,
    "W_through": 460.0,
    "W_left":    140.0,
}

# ─── GUI parameters ────────────────────────────────────────────────────────────
WINDOW_TITLE: str = "SUMO 固定信号配时系统 v1.0 — 四相位交叉口"
MONITOR_REFRESH_MS: int = 500   # GUI status-panel refresh interval (ms)
FLOW_INPUT_MAX: float = 9999.0  # maximum accepted flow value (veh/h)
