"""
gui/timing_panel.py — 配时方案展示与编辑面板。

布局（始终处于编辑状态，无模式切换）：

  ┌──────────────────────────────────────────────────────┐
  │ 🚦 配时方案                                           │
  │  方案名称: [__________]   周期: 107 s  损失: 12 s    │
  │  ─────────────────────────────────────────────────── │
  │  相位          绿灯（s）  黄灯（s）                   │
  │  N-S 直行      [ 40 ↕]   [ 3.0 ↕]                   │
  │  N-S 左转      [ 12 ↕]   [ 3.0 ↕]                   │
  │  E-W 直行      [ 30 ↕]   [ 3.0 ↕]                   │
  │  E-W 左转      [ 10 ↕]   [ 3.0 ↕]                   │
  │  全红（固定）: 1.0 s                                  │
  │  ⚠ 验证提示（红色，有错时出现）                        │
  │  [下发方案至 SUMO]                                    │
  └──────────────────────────────────────────────────────┘

注：黄灯时间在「自动配时」中固定为 3 s；手动配时可按相位独立调整。
    全红时间恒定为 1 s（不可调整）。

信号：
  plan_applied(TimingPlan)  — 点击「下发方案至 SUMO」后发射
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QDoubleSpinBox, QLineEdit, QPushButton, QGroupBox,
    QMessageBox, QFrame,
)

import config
from core.data_models import TimingPlan
from core.timing_engine import TimingEngine

# Phase display order: (display label, green_times index)
_PHASES = [
    ("N-S 直行", 0),
    ("N-S 左转", 1),
    ("E-W 直行", 2),
    ("E-W 左转", 3),
]


class TimingPanel(QGroupBox):
    """
    Four-phase timing plan editor — always in edit mode.

    External API:
        plan_applied              — signal emitted on 'Apply' click
        display_plan(plan)        — update panel to show the given plan
        current_plan()            — return the plan currently in the spinboxes
    """

    plan_applied = pyqtSignal(object)   # payload: TimingPlan

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("🚦 配时方案", parent)
        self._engine   = TimingEngine()
        self._plan     = self._engine.default_plan()
        self._green_sbs: dict[int, QDoubleSpinBox] = {}
        self._yellow_sbs: dict[int, QDoubleSpinBox] = {}
        self._build_ui()
        self._sync_from_plan(self._plan)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Name + summary row ───────────────────────────────────────────────
        row_top = QHBoxLayout()
        row_top.addWidget(QLabel("方案名称:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("可选名称（留空则自动生成）")
        self._name_edit.setMaximumWidth(160)
        row_top.addWidget(self._name_edit)
        row_top.addSpacing(12)
        self._lbl_cycle = QLabel()
        self._lbl_loss  = QLabel()
        row_top.addWidget(self._lbl_cycle)
        row_top.addSpacing(8)
        row_top.addWidget(self._lbl_loss)
        row_top.addStretch()
        root.addLayout(row_top)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        # ── Phase grid ───────────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.addWidget(QLabel("<b>相位</b>"),          0, 0)
        grid.addWidget(QLabel("<b>绿灯（s）</b>"),      0, 1)
        grid.addWidget(QLabel("<b>黄灯（s）</b>"),      0, 2)

        for row, (name, idx) in enumerate(_PHASES, start=1):
            grid.addWidget(QLabel(name), row, 0)

            g_sb = QDoubleSpinBox()
            g_sb.setRange(config.DEFAULT_MIN_GREEN, config.DEFAULT_MAX_CYCLE)
            g_sb.setSingleStep(1.0)
            g_sb.setDecimals(1)
            g_sb.setSuffix(" s")
            g_sb.setFixedWidth(100)
            g_sb.setToolTip(f"相位 {idx+1} 绿灯时长（Webster 自动配时时只读）")
            g_sb.valueChanged.connect(self._on_value_changed)
            self._green_sbs[idx] = g_sb
            grid.addWidget(g_sb, row, 1)

            y_sb = QDoubleSpinBox()
            y_sb.setRange(1.0, 10.0)
            y_sb.setSingleStep(0.5)
            y_sb.setDecimals(1)
            y_sb.setSuffix(" s")
            y_sb.setFixedWidth(90)
            y_sb.setToolTip(
                "黄灯时长（手动配时时每相位可独立设置；"
                "自动配时时固定 3 s）。\n"
                "黄灯期间车辆仍可通行，不计入损失时间。"
            )
            y_sb.valueChanged.connect(self._on_value_changed)
            self._yellow_sbs[idx] = y_sb
            grid.addWidget(y_sb, row, 2)

        root.addLayout(grid)

        # ── All-red info label ────────────────────────────────────────────────
        ar_row = QHBoxLayout()
        ar_row.addWidget(QLabel("全红（固定）:"))
        lbl_ar = QLabel(f"{config.DEFAULT_ALL_RED_TIME:.1f} s")
        lbl_ar.setStyleSheet("color:#666;")
        lbl_ar.setToolTip("全红清空间隙：所有方向均为红灯，让交叉口清空。固定值，不可调整。")
        ar_row.addWidget(lbl_ar)
        ar_row.addStretch()
        root.addLayout(ar_row)

        # ── Validation label ─────────────────────────────────────────────────
        self._lbl_err = QLabel()
        self._lbl_err.setStyleSheet("color: red;")
        self._lbl_err.setWordWrap(True)
        self._lbl_err.setVisible(False)
        root.addWidget(self._lbl_err)

        # ── Apply button ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_apply = QPushButton("下发方案至 SUMO")
        self._btn_apply.setToolTip(
            "将当前配时方案推送至 SUMO。\n"
            "仿真运行中：实时生效（下一步切换）。\n"
            "仿真未运行：方案保存至数据库，下次启动时自动应用。"
        )
        self._btn_apply.clicked.connect(self._on_apply)
        btn_row.addWidget(self._btn_apply)
        btn_row.addStretch()
        root.addLayout(btn_row)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _sync_from_plan(self, plan: TimingPlan) -> None:
        """Push plan values into spinboxes without triggering valueChanged."""
        yt = plan.yellow_times if len(plan.yellow_times) == 4 else [3.0] * 4
        for idx in range(4):
            for sb, val in ((self._green_sbs[idx], plan.green_times[idx]),
                            (self._yellow_sbs[idx], yt[idx])):
                sb.blockSignals(True)
                sb.setValue(val)
                sb.blockSignals(False)
        self._name_edit.setText(plan.name)
        self._refresh_summary()

    def _plan_from_spinboxes(self) -> TimingPlan:
        g = [self._green_sbs[i].value() for i in range(4)]
        y = [self._yellow_sbs[i].value() for i in range(4)]
        ar = config.DEFAULT_ALL_RED_TIME
        cycle = sum(g) + sum(y) + 4 * ar
        return TimingPlan(
            name=self._name_edit.text().strip(),
            cycle=round(cycle, 1),
            green_times=[round(v, 1) for v in g],
            yellow_times=[round(v, 1) for v in y],
            all_red=ar,
            note="manual",
        )

    def _refresh_summary(self) -> None:
        plan = self._plan_from_spinboxes()
        total_loss = sum(plan.yellow_times) + 4 * plan.all_red
        self._lbl_cycle.setText(f"周期：<b>{plan.cycle:.0f} s</b>")
        self._lbl_loss.setText(
            f"损失（黄+全红）：{total_loss:.0f} s"
        )

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_value_changed(self) -> None:
        candidate = self._plan_from_spinboxes()
        self._plan = candidate
        errors = self._engine.validate(candidate)
        self._refresh_summary()
        if errors:
            self._lbl_err.setText("⚠ " + "；".join(errors))
            self._lbl_err.setVisible(True)
        else:
            self._lbl_err.setVisible(False)

    def _on_apply(self) -> None:
        plan = self._plan_from_spinboxes()
        errors = self._engine.validate(plan)
        if errors:
            QMessageBox.warning(
                self, "方案有误",
                "当前方案存在以下问题，请修正后再下发：\n\n" + "\n".join(errors),
            )
            return
        self._plan = plan
        self.plan_applied.emit(plan)

    # ── Public API ─────────────────────────────────────────────────────────────

    def display_plan(self, plan: TimingPlan) -> None:
        """Update panel to show the given TimingPlan."""
        self._plan = plan
        self._sync_from_plan(plan)
        self._lbl_err.setVisible(False)

    def current_plan(self) -> TimingPlan:
        """Return the plan currently represented by the spinboxes."""
        return self._plan_from_spinboxes()
