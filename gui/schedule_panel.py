"""
gui/schedule_panel.py — 方案库与分时段调度面板。

布局：

  ┌────────────────────────────────────────────────────────────┐
  │ 📋 方案库 & 调度                                            │
  │ ── 方案库 ──────────────────────────────────────────────── │
  │  保存当前配时+流量为方案:  [方案名称___________] [保存]     │
  │  ┌───────────────────────────────────────────────────────┐ │
  │  │ 方案名称         周期   创建时间         [加载][删除]  │ │
  │  │ 早高峰           107s   2026-05-25       [ ] [ ]      │ │
  │  └───────────────────────────────────────────────────────┘ │
  │ ── 分时段调度 ──────────────────────────────────────────── │
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │  #  开始(s)  结束(s)  方案名称      [↑][↓][删]      │   │
  │  │  1    0      900     早高峰          [ ][ ][ ]       │   │
  │  └─────────────────────────────────────────────────────┘   │
  │  [添加时段]  注：仿真将自动在最后一段结束时停止              │
  └────────────────────────────────────────────────────────────┘

信号:
  save_scenario_requested(name: str)  — 用户请求保存当前方案
  load_scenario_requested(NamedScenario)  — 用户双击加载某方案
  schedule_changed()  — 任何调度表变动（外部用 build_schedule() 读取）
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QDoubleSpinBox, QMessageBox, QFrame,
)

from core.data_models import NamedScenario, ScheduleEntry, TimingPlan, FlowData
from core.data_manager import DataManager


class SchedulePanel(QGroupBox):
    """
    Panel for:
      1. Saving the current timing+flow state as a named scenario.
      2. Browsing / loading / deleting saved scenarios.
      3. Building a time-based dispatch schedule for multi-period simulation.

    External API
    ------------
    save_scenario_requested(name: str) — emitted when user clicks 'Save'
    load_scenario_requested(NamedScenario) — emitted when user double-clicks
    schedule_changed()                 — emitted when schedule table changes
    build_schedule() → List[ScheduleEntry] — read the current schedule table
    refresh_scenario_list()            — reload scenarios from DB
    """

    save_scenario_requested = pyqtSignal(str)           # scenario name
    load_scenario_requested = pyqtSignal(object)        # NamedScenario
    schedule_changed        = pyqtSignal()

    def __init__(self, data_manager: DataManager,
                 parent: QWidget | None = None) -> None:
        super().__init__("📋 方案库 & 调度", parent)
        self._dm = data_manager
        self._scenarios: List[NamedScenario] = []
        self._build_ui()
        self.refresh_scenario_list()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Save row ──────────────────────────────────────────────────────────
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("保存当前方案为:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("方案名称（必填）")
        self._name_edit.setMaximumWidth(160)
        save_row.addWidget(self._name_edit)
        btn_save = QPushButton("保存")
        btn_save.setToolTip("将配时面板当前方案和流量面板当前流量保存为命名方案")
        btn_save.clicked.connect(self._on_save)
        save_row.addWidget(btn_save)
        save_row.addStretch()
        root.addLayout(save_row)

        # ── Scenario library table ────────────────────────────────────────────
        root.addWidget(QLabel("<b>已保存方案：</b>（双击加载至配时面板）"))
        self._sc_table = QTableWidget(0, 5)
        self._sc_table.setHorizontalHeaderLabels(
            ["名称", "周期(s)", "NS直行(s)", "EW直行(s)", "创建时间"])
        self._sc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._sc_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._sc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._sc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._sc_table.setMaximumHeight(130)
        self._sc_table.doubleClicked.connect(self._on_sc_double_click)
        root.addWidget(self._sc_table)

        # Delete scenario button
        sc_btn_row = QHBoxLayout()
        btn_del_sc = QPushButton("删除所选方案")
        btn_del_sc.clicked.connect(self._on_delete_scenario)
        sc_btn_row.addWidget(btn_del_sc)
        sc_btn_row.addStretch()
        root.addLayout(sc_btn_row)

        root.addWidget(_hsep())

        # ── Schedule table ────────────────────────────────────────────────────
        root.addWidget(
            QLabel("<b>分时段调度表</b>（基于仿真时间，s）：")
        )
        self._sched_table = QTableWidget(0, 4)
        self._sched_table.setHorizontalHeaderLabels(
            ["开始(s)", "结束(s)", "方案名称", "操作"])
        self._sched_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._sched_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._sched_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._sched_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._sched_table.setMaximumHeight(150)
        root.addWidget(self._sched_table)

        sched_btn_row = QHBoxLayout()
        btn_add = QPushButton("添加时段")
        btn_add.setToolTip("在调度表末尾添加一个新时段，紧接上一时段")
        btn_add.clicked.connect(self._on_add_entry)
        sched_btn_row.addWidget(btn_add)
        btn_clr = QPushButton("清空调度")
        btn_clr.clicked.connect(self._on_clear_schedule)
        sched_btn_row.addWidget(btn_clr)
        sched_btn_row.addStretch()
        root.addLayout(sched_btn_row)

        info = QLabel(
            "💡 设置调度表后，仿真将在各时段内自动切换配时方案，"
            "并在最后一段结束时自动停止。\n"
            "   不设置调度表则按配时面板的当前方案运行 1 小时。"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#555; font-size:11px;")
        root.addWidget(info)

    # ── Scenario table helpers ────────────────────────────────────────────────

    def refresh_scenario_list(self) -> None:
        """Reload the scenario list from the database."""
        self._scenarios = self._dm.list_scenarios()
        self._sc_table.setRowCount(0)
        for sc in self._scenarios:
            row = self._sc_table.rowCount()
            self._sc_table.insertRow(row)
            g = sc.plan.green_times
            self._sc_table.setItem(row, 0, QTableWidgetItem(sc.name))
            self._sc_table.setItem(row, 1, QTableWidgetItem(f"{sc.plan.cycle:.0f}"))
            self._sc_table.setItem(row, 2, QTableWidgetItem(f"{g[0]:.1f}" if g else ""))
            self._sc_table.setItem(row, 3, QTableWidgetItem(f"{g[2]:.1f}" if len(g) > 2 else ""))
            self._sc_table.setItem(row, 4, QTableWidgetItem(sc.created_at[:10]))

    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "需要名称", "请输入方案名称后再保存。")
            return
        self.save_scenario_requested.emit(name)

    def _on_sc_double_click(self) -> None:
        rows = self._sc_table.selectedItems()
        if not rows:
            return
        idx = self._sc_table.currentRow()
        if 0 <= idx < len(self._scenarios):
            self.load_scenario_requested.emit(self._scenarios[idx])

    def _on_delete_scenario(self) -> None:
        idx = self._sc_table.currentRow()
        if idx < 0 or idx >= len(self._scenarios):
            return
        sc = self._scenarios[idx]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确认删除方案「{sc.name}」？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self._dm.delete_scenario(sc.scenario_id)
            self.refresh_scenario_list()

    # ── Schedule table helpers ────────────────────────────────────────────────

    def _scenario_names(self) -> List[str]:
        return [sc.name for sc in self._scenarios]

    def _on_add_entry(self) -> None:
        """Add a new schedule entry after the last row."""
        n = self._sched_table.rowCount()

        # Default start = end of last row (or 0)
        if n > 0:
            last_end_item = self._sched_table.cellWidget(n - 1, 1)
            start = last_end_item.value() if last_end_item else 0.0
        else:
            start = 0.0
        end = start + 900.0   # default 15-minute slot

        self._insert_schedule_row(n, start, end, "")

    def _insert_schedule_row(self, row: int,
                              start: float, end: float, name: str) -> None:
        self._sched_table.insertRow(row)

        start_sb = QDoubleSpinBox()
        start_sb.setRange(0, 999999)
        start_sb.setValue(start)
        start_sb.setSuffix(" s")
        start_sb.setDecimals(0)
        start_sb.valueChanged.connect(self.schedule_changed.emit)
        self._sched_table.setCellWidget(row, 0, start_sb)

        end_sb = QDoubleSpinBox()
        end_sb.setRange(0, 999999)
        end_sb.setValue(end)
        end_sb.setSuffix(" s")
        end_sb.setDecimals(0)
        end_sb.valueChanged.connect(self.schedule_changed.emit)
        self._sched_table.setCellWidget(row, 1, end_sb)

        combo = QComboBox()
        combo.addItem("（不切换，沿用当前）")
        for sc_name in self._scenario_names():
            combo.addItem(sc_name)
        if name:
            idx = combo.findText(name)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.currentIndexChanged.connect(self.schedule_changed.emit)
        self._sched_table.setCellWidget(row, 2, combo)

        del_btn = QPushButton("删")
        del_btn.setFixedWidth(36)
        del_btn.clicked.connect(lambda _checked, r=row: self._delete_row(r))
        self._sched_table.setCellWidget(row, 3, del_btn)

        self.schedule_changed.emit()

    def _delete_row(self, row: int) -> None:
        self._sched_table.removeRow(row)
        self.schedule_changed.emit()

    def _on_clear_schedule(self) -> None:
        self._sched_table.setRowCount(0)
        self.schedule_changed.emit()

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_schedule(self) -> List[ScheduleEntry]:
        """
        Convert the schedule table into a list of ScheduleEntry objects.

        Entries with no selected scenario (combo index 0 = 'no change') are
        included only as time markers; their plan and flow are None.

        Returns an empty list if the table is empty.
        """
        entries: List[ScheduleEntry] = []
        for row in range(self._sched_table.rowCount()):
            start_w = self._sched_table.cellWidget(row, 0)
            end_w   = self._sched_table.cellWidget(row, 1)
            combo_w = self._sched_table.cellWidget(row, 2)
            if start_w is None or end_w is None or combo_w is None:
                continue

            start = float(start_w.value())
            end   = float(end_w.value())
            sc_name = combo_w.currentText() if combo_w.currentIndex() > 0 else ""

            plan: Optional[TimingPlan] = None
            flow: Optional[FlowData] = None
            if sc_name:
                sc = self._dm.load_scenario_by_name(sc_name)
                if sc:
                    plan = sc.plan
                    flow = sc.flow

            entries.append(ScheduleEntry(
                start_sim_time=start,
                end_sim_time=end,
                scenario_name=sc_name or "（默认方案）",
                plan=plan,
                flow=flow,
            ))
        return sorted(entries, key=lambda e: e.start_sim_time)

    def has_schedule(self) -> bool:
        """True if the schedule table has at least one valid row."""
        return self._sched_table.rowCount() > 0


def _hsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep
