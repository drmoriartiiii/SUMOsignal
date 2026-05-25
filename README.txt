SumoSignal — Intersection Signal Timing Tool
=============================================

A PyQt5 desktop tool for designing, simulating, and evaluating fixed-time
signal plans at isolated intersections using SUMO and the Webster algorithm.


REQUIREMENTS
------------
Python       3.10+
SUMO         1.19+  (set SUMO_HOME environment variable)
PyQt5        5.15+
SQLAlchemy   2.x
python-docx  (optional, for report generation)

Install Python dependencies:
    pip install -r requirements.txt


SUMO INSTALLATION
-----------------
Download from: https://eclipse.dev/sumo/
After installing, set the environment variable:
    SUMO_HOME = C:\Program Files (x86)\Eclipse\Sumo   (adjust to your path)

Generate the road network (run once):
    cd sumo_net
    generate_net.bat


RUN
---
    python main.py


FEATURES
--------
- Webster auto-timing (4-phase isolated intersection)
- Manual phase editing with per-phase yellow time adjustment
- Real-time SUMO/TraCI simulation with optional sumo-gui visualization
- Live monitoring: phase, queue length, per-movement delay (12 movements)
- Named scenario library (save / load timing + flow combos)
- Time-based schedule: auto-switch plans at defined simulation times
- CSV export: results, timing plans, named scenarios
- SQLite persistence with automatic schema migration


PROJECT STRUCTURE
-----------------
main.py               Entry point
config.py             Global constants and SUMO path detection
core/
  data_models.py      Dataclasses (TimingPlan, FlowData, SimResult, ...)
  timing_engine.py    Webster algorithm engine
  data_manager.py     SQLite persistence (SQLAlchemy ORM)
  traci_adapter.py    SUMO TraCI wrapper
  sim_controller.py   Simulation QThread
gui/
  flow_panel.py       Traffic flow input panel
  timing_panel.py     Signal plan editor panel
  monitor_panel.py    Real-time monitoring panel
  schedule_panel.py   Named scenario library + time schedule panel
  main_window.py      Main window integration
sumo_net/             SUMO network XML files
test_part2.py         Unit tests (30 cases, no SUMO required)
HANDOFF.md            Developer handoff notes and change log
