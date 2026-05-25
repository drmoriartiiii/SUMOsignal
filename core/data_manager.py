"""
core/data_manager.py — SQLite persistence layer (SQLAlchemy 2.x ORM).

Tables:
  timing_plans      — saved signal timing plans
  flow_history      — historical traffic flow snapshots
  sim_results       — aggregated results from completed simulation runs
  named_scenarios   — user-named combinations of timing plan + flow data

All public methods are synchronous and safe to call from a Qt worker
thread.  The engine uses WAL journal mode for better read concurrency.

Schema migration
----------------
On every startup, _migrate() runs a lightweight ALTER TABLE migration to
add any columns that exist in the ORM but not yet in the physical DB.
This preserves existing data while supporting new fields.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import Column, Float, Integer, String, Text, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config
from core.data_models import FlowData, NamedScenario, SimResult, TimingPlan


# ─── ORM base and table definitions ───────────────────────────────────────────

class _Base(DeclarativeBase):
    pass


class _TimingPlanRow(_Base):
    __tablename__ = "timing_plans"

    plan_id           = Column(String,  primary_key=True)
    name              = Column(String,  default="")
    cycle             = Column(Float,   nullable=False)
    green_times_json  = Column(Text,    nullable=False)
    yellow_times_json = Column(Text,    default="[3.0,3.0,3.0,3.0]")
    phase_order_json  = Column(Text,    nullable=False)
    all_red           = Column(Float,   default=1.0)
    created_at        = Column(String,  nullable=False)
    note              = Column(String,  default="")


class _FlowHistoryRow(_Base):
    __tablename__ = "flow_history"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    flows_json  = Column(Text,   nullable=False)
    Y_total     = Column(Float)
    recorded_at = Column(String, nullable=False)


class _SimResultRow(_Base):
    __tablename__ = "sim_results"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    plan_id             = Column(String)
    scenario_name       = Column(String,  default="")
    sim_duration        = Column(Float)
    avg_delay           = Column(Float)
    avg_queue           = Column(Float)
    throughput          = Column(Integer)
    speed_factor        = Column(Float)
    delay_json          = Column(Text,    default="{}")   # delay_by_approach
    movement_delay_json = Column(Text,    default="{}")   # delay_by_movement
    created_at          = Column(String,  nullable=False)


class _NamedScenarioRow(_Base):
    __tablename__ = "named_scenarios"

    scenario_id = Column(String, primary_key=True)
    name        = Column(String, nullable=False)
    plan_json   = Column(Text,   nullable=False)
    flow_json   = Column(Text,   default="{}")
    created_at  = Column(String, nullable=False)


# ─── WAL-mode helper ───────────────────────────────────────────────────────────

def _enable_wal(dbapi_conn, _connection_record) -> None:
    dbapi_conn.execute("PRAGMA journal_mode=WAL")


# ─── DataManager ──────────────────────────────────────────────────────────────

class DataManager:
    """
    CRUD interface for the local SQLite database.

    Instantiate once and share the single instance across modules::

        dm = DataManager()
        dm.save_plan(plan)
        plan = dm.load_plan("plan_abc123")
        results = dm.list_results()
    """

    # Columns that must be present after migration (table → list of ALTER stmts)
    _MIGRATIONS: Dict[str, List[str]] = {
        "timing_plans": [
            "ALTER TABLE timing_plans ADD COLUMN name TEXT DEFAULT ''",
            "ALTER TABLE timing_plans ADD COLUMN yellow_times_json TEXT DEFAULT '[3.0,3.0,3.0,3.0]'",
        ],
        "sim_results": [
            "ALTER TABLE sim_results ADD COLUMN scenario_name TEXT DEFAULT ''",
            "ALTER TABLE sim_results ADD COLUMN delay_json TEXT DEFAULT '{}'",
            "ALTER TABLE sim_results ADD COLUMN movement_delay_json TEXT DEFAULT '{}'",
        ],
    }

    def __init__(self, db_path: Optional[str] = None) -> None:
        path = db_path or config.DB_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        event.listen(self._engine, "connect", _enable_wal)
        _Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)
        self._migrate()

    def _migrate(self) -> None:
        """Add missing columns to existing tables (safe to re-run)."""
        with self._engine.connect() as conn:
            for table, stmts in self._MIGRATIONS.items():
                # Get existing column names
                cols = {
                    row[1]
                    for row in conn.execute(text(f"PRAGMA table_info({table})"))
                }
                for stmt in stmts:
                    # Extract the column name from the ALTER TABLE statement
                    col_name = stmt.split("ADD COLUMN")[1].strip().split()[0]
                    if col_name not in cols:
                        conn.execute(text(stmt))
            conn.commit()

    # ── TimingPlan CRUD ───────────────────────────────────────────────────────

    def save_plan(self, plan: TimingPlan) -> None:
        """Insert or overwrite a timing plan (upsert by plan_id)."""
        d = plan.to_db_dict()
        with self._Session() as s:
            existing = s.get(_TimingPlanRow, plan.plan_id)
            if existing:
                for k, v in d.items():
                    setattr(existing, k, v)
            else:
                s.add(_TimingPlanRow(**d))
            s.commit()

    def load_plan(self, plan_id: str) -> Optional[TimingPlan]:
        """Return a TimingPlan by plan_id, or None if not found."""
        with self._Session() as s:
            row = s.get(_TimingPlanRow, plan_id)
            if row is None:
                return None
            return self._row_to_plan(row)

    def list_plans(self, limit: int = 100) -> List[TimingPlan]:
        """Return up to `limit` plans ordered newest-first."""
        with self._Session() as s:
            rows = (
                s.query(_TimingPlanRow)
                .order_by(_TimingPlanRow.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._row_to_plan(r) for r in rows]

    def delete_plan(self, plan_id: str) -> bool:
        """Delete a plan by ID. Returns True if the plan existed."""
        with self._Session() as s:
            row = s.get(_TimingPlanRow, plan_id)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    def plan_count(self) -> int:
        with self._Session() as s:
            return s.query(_TimingPlanRow).count()

    @staticmethod
    def _row_to_plan(row: _TimingPlanRow) -> TimingPlan:
        return TimingPlan.from_db_dict({
            "plan_id":           row.plan_id,
            "name":              row.name or "",
            "cycle":             row.cycle,
            "green_times_json":  row.green_times_json,
            "yellow_times_json": row.yellow_times_json or "[3.0,3.0,3.0,3.0]",
            "phase_order_json":  row.phase_order_json,
            "all_red":           row.all_red or 1.0,
            "created_at":        row.created_at or "",
            "note":              row.note or "",
        })

    # ── FlowData history ──────────────────────────────────────────────────────

    def save_flow(self, flow_data: FlowData) -> int:
        """Append a flow snapshot to the history table. Returns new row id."""
        row = _FlowHistoryRow(
            flows_json=flow_data.to_json(),
            Y_total=flow_data.Y,
            recorded_at=datetime.now().isoformat(),
        )
        with self._Session() as s:
            s.add(row)
            s.commit()
            return row.id

    def load_last_flow(self) -> Optional[FlowData]:
        """Return the most recently saved FlowData snapshot, or None."""
        with self._Session() as s:
            row = (
                s.query(_FlowHistoryRow)
                .order_by(_FlowHistoryRow.id.desc())
                .first()
            )
            if row is None:
                return None
            fd = FlowData.from_json(row.flows_json)
            fd.Y = float(row.Y_total or 0.0)
            return fd

    # ── SimResult CRUD ────────────────────────────────────────────────────────

    def save_result(self, result: SimResult) -> int:
        """
        Persist a simulation result and populate result.id in-place.

        Returns the auto-assigned row id.
        """
        row = _SimResultRow(
            plan_id=result.plan_id,
            scenario_name=result.scenario_name,
            sim_duration=result.sim_duration,
            avg_delay=result.avg_delay,
            avg_queue=result.avg_queue,
            throughput=result.throughput,
            speed_factor=result.speed_factor,
            delay_json=json.dumps(result.delay_by_approach),
            movement_delay_json=json.dumps(result.delay_by_movement),
            created_at=result.created_at,
        )
        with self._Session() as s:
            s.add(row)
            s.commit()
            result.id = row.id
            return row.id

    def list_results(self, limit: int = 50) -> List[SimResult]:
        """Return up to `limit` simulation results, newest-first."""
        with self._Session() as s:
            rows = (
                s.query(_SimResultRow)
                .order_by(_SimResultRow.id.desc())
                .limit(limit)
                .all()
            )
            results = []
            for r in rows:
                dba = json.loads(r.delay_json or "{}")
                dbm = json.loads(r.movement_delay_json or "{}")
                results.append(SimResult(
                    id=r.id,
                    plan_id=r.plan_id or "",
                    scenario_name=r.scenario_name or "",
                    sim_duration=r.sim_duration or 0.0,
                    avg_delay=r.avg_delay or 0.0,
                    avg_queue=r.avg_queue or 0.0,
                    throughput=r.throughput or 0,
                    speed_factor=r.speed_factor or 1.0,
                    delay_by_approach=dba,
                    delay_by_movement=dbm,
                    created_at=r.created_at,
                ))
            return results

    def result_count(self) -> int:
        with self._Session() as s:
            return s.query(_SimResultRow).count()

    # ── NamedScenario CRUD ────────────────────────────────────────────────────

    def save_scenario(self, scenario: NamedScenario) -> None:
        """Insert or overwrite a named scenario (upsert by scenario_id)."""
        d = scenario.to_db_dict()
        with self._Session() as s:
            existing = s.get(_NamedScenarioRow, scenario.scenario_id)
            if existing:
                for k, v in d.items():
                    setattr(existing, k, v)
            else:
                s.add(_NamedScenarioRow(**d))
            s.commit()

    def load_scenario(self, scenario_id: str) -> Optional[NamedScenario]:
        """Return a NamedScenario by ID, or None if not found."""
        with self._Session() as s:
            row = s.get(_NamedScenarioRow, scenario_id)
            if row is None:
                return None
            return self._row_to_scenario(row)

    def load_scenario_by_name(self, name: str) -> Optional[NamedScenario]:
        """Return the most recently created scenario with the given name."""
        with self._Session() as s:
            row = (
                s.query(_NamedScenarioRow)
                .filter(_NamedScenarioRow.name == name)
                .order_by(_NamedScenarioRow.created_at.desc())
                .first()
            )
            if row is None:
                return None
            return self._row_to_scenario(row)

    def list_scenarios(self, limit: int = 200) -> List[NamedScenario]:
        """Return up to `limit` scenarios ordered newest-first."""
        with self._Session() as s:
            rows = (
                s.query(_NamedScenarioRow)
                .order_by(_NamedScenarioRow.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._row_to_scenario(r) for r in rows]

    def delete_scenario(self, scenario_id: str) -> bool:
        """Delete a scenario by ID. Returns True if it existed."""
        with self._Session() as s:
            row = s.get(_NamedScenarioRow, scenario_id)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True

    @staticmethod
    def _row_to_scenario(row: _NamedScenarioRow) -> NamedScenario:
        return NamedScenario.from_db_dict({
            "scenario_id": row.scenario_id,
            "name":        row.name,
            "plan_json":   row.plan_json,
            "flow_json":   row.flow_json or "{}",
            "created_at":  row.created_at or "",
        })

    # ── Export ────────────────────────────────────────────────────────────────

    def export_results_csv(self, path: str) -> int:
        """
        Write all simulation results to a CSV file.

        Column definitions
        ------------------
        id                 : 仿真记录自增编号
        plan_id            : 所用配时方案的内部 ID
        scenario_name      : 用户自定义方案名称
        sim_duration_s     : 仿真运行总步数（步长 1s，即仿真持续时间 s）
        avg_delay_s_veh    : 全网平均车辆延误（每辆车在速度 <0.1m/s 状态的累计时间均值，s/辆）
        delay_N_s_veh      : 北进口平均延误（北方向来车在进口边上的等待时间均值，s/辆）
        delay_S_s_veh      : 南进口平均延误
        delay_E_s_veh      : 东进口平均延误
        delay_W_s_veh      : 西进口平均延误
        mvt_N_through_s    : 北进口直行方向平均延误（lane 0，s/辆）
        mvt_N_left_s       : 北进口左转方向平均延误（lane 1，s/辆）
        mvt_N_right_s      : 北进口右转方向平均延误（lane 0 右转，s/辆）
        mvt_S_through_s    : 南进口直行
        mvt_S_left_s       : 南进口左转
        mvt_S_right_s      : 南进口右转
        mvt_E_through_s    : 东进口直行
        mvt_E_left_s       : 东进口左转
        mvt_E_right_s      : 东进口右转
        mvt_W_through_s    : 西进口直行
        mvt_W_left_s       : 西进口左转
        mvt_W_right_s      : 西进口右转
        avg_queue_m        : 全网各进口排队长度均值（辆数×7.5m 估算，m）
        throughput_veh     : 仿真期间完成行程的车辆总数
        speed_factor       : 仿真倍速系数
        created_at         : 记录时间戳

        Returns the number of rows written (0 if no results exist).
        """
        results = self.list_results(limit=100_000)
        if not results:
            return 0

        _DIRS = ["N", "S", "E", "W"]
        _MVTS = ["through", "left", "right"]

        fieldnames = (
            ["id", "plan_id", "scenario_name", "sim_duration_s",
             "avg_delay_s_veh"]
            + [f"delay_{d}_s_veh" for d in _DIRS]
            + [f"mvt_{d}_{m}_s" for d in _DIRS for m in _MVTS]
            + ["avg_queue_m", "throughput_veh", "speed_factor", "created_at"]
        )

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                dba = r.delay_by_approach or {}
                dbm = r.delay_by_movement or {}
                row: dict = {
                    "id":             r.id,
                    "plan_id":        r.plan_id,
                    "scenario_name":  r.scenario_name,
                    "sim_duration_s": round(r.sim_duration, 1),
                    "avg_delay_s_veh": round(r.avg_delay, 2),
                    "avg_queue_m":    round(r.avg_queue, 2),
                    "throughput_veh": r.throughput,
                    "speed_factor":   r.speed_factor,
                    "created_at":     r.created_at,
                }
                for d in _DIRS:
                    row[f"delay_{d}_s_veh"] = dba.get(d, "")
                for d in _DIRS:
                    for m in _MVTS:
                        row[f"mvt_{d}_{m}_s"] = dbm.get(f"{d}_{m}", "")
                writer.writerow(row)
        return len(results)

    def export_plans_csv(self, path: str) -> int:
        """Write all timing plans to a CSV file."""
        plans = self.list_plans(limit=100_000)
        if not plans:
            return 0
        fieldnames = [
            "plan_id", "name", "cycle",
            "NS_through_g_s", "NS_left_g_s", "EW_through_g_s", "EW_left_g_s",
            "NS_through_y_s", "NS_left_y_s", "EW_through_y_s", "EW_left_y_s",
            "all_red_s", "note", "created_at",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for p in plans:
                g = p.green_times
                y = p.yellow_times
                writer.writerow({
                    "plan_id":           p.plan_id,
                    "name":              p.name,
                    "cycle":             p.cycle,
                    "NS_through_g_s":    g[0] if len(g) > 0 else "",
                    "NS_left_g_s":       g[1] if len(g) > 1 else "",
                    "EW_through_g_s":    g[2] if len(g) > 2 else "",
                    "EW_left_g_s":       g[3] if len(g) > 3 else "",
                    "NS_through_y_s":    y[0] if len(y) > 0 else "",
                    "NS_left_y_s":       y[1] if len(y) > 1 else "",
                    "EW_through_y_s":    y[2] if len(y) > 2 else "",
                    "EW_left_y_s":       y[3] if len(y) > 3 else "",
                    "all_red_s":         p.all_red,
                    "note":              p.note,
                    "created_at":        p.created_at,
                })
        return len(plans)

    def export_scenarios_csv(self, path: str) -> int:
        """Write all named scenarios to a CSV file."""
        scenarios = self.list_scenarios()
        if not scenarios:
            return 0
        fieldnames = ["scenario_id", "name",
                      "cycle", "NS_through_g", "NS_left_g", "EW_through_g", "EW_left_g",
                      "created_at"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for sc in scenarios:
                g = sc.plan.green_times
                writer.writerow({
                    "scenario_id":  sc.scenario_id,
                    "name":         sc.name,
                    "cycle":        sc.plan.cycle,
                    "NS_through_g": g[0] if len(g) > 0 else "",
                    "NS_left_g":    g[1] if len(g) > 1 else "",
                    "EW_through_g": g[2] if len(g) > 2 else "",
                    "EW_left_g":    g[3] if len(g) > 3 else "",
                    "created_at":   sc.created_at,
                })
        return len(scenarios)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Release all database connections."""
        self._engine.dispose()
