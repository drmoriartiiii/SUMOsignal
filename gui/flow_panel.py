"""
gui/flow_panel.py — 流量输入面板。

布局：
  ┌─────────────────────────────────────┐
  │  📍 流量输入                         │
  │  ┌──────┬──────────┬───────────┐    │
  │  │方向  │  直行(辆/h)│  左转(辆/h)│    │
  │  ├──────┼──────────┼───────────┤    │
  │  │  N   │  [620]   │  [180]    │    │
  │  │  S   │  [580]   │  [160]    │    │
  │  │  E   │  [430]   │  [120]    │    │
  │  │  W   │  [460]   │  [140]    │    │
  │  └──────┴──────────┴───────────┘    │
  │  [自动生成配时方案]  [重置为默认值]   │
  └─────────────────────────────────────┘

信号：
  flow_submitted(FlowData)  — 用户点击「生成方案」后发射，携带当前输入的流量数据
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QDoubleSpinBox, QPushButton, QGroupBox,
)

import config
from core.data_models import FlowData


# 行顺序与显示标签
_DIRECTIONS = [("N", "北"), ("S", "南"), ("E", "东"), ("W", "西")]


class FlowPanel(QGroupBox):
    """
    四方向直行/左转流量输入表单。

    外部接口：
        flow_submitted  — 点击「生成方案」时发射 FlowData
        load_flow(fd)   — 用 FlowData 的数值填充所有 SpinBox
        get_flow()      — 读取当前 SpinBox 值并返回 FlowData
    """

    flow_submitted = pyqtSignal(object)   # payload: FlowData

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("📍 流量输入（辆 / 小时）", parent)
        self._spinboxes: dict[str, QDoubleSpinBox] = {}
        self._build_ui()
        self._reset_to_defaults()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Table header
        grid = QGridLayout()
        grid.setSpacing(6)

        for col, text in enumerate(["方向", "直行（辆/h）", "左转（辆/h）"]):
            lbl = QLabel(f"<b>{text}</b>")
            grid.addWidget(lbl, 0, col)

        # One row per direction
        for row, (direction, zh_name) in enumerate(_DIRECTIONS, start=1):
            grid.addWidget(QLabel(f"<b>{zh_name} ({direction})</b>"), row, 0)
            for col, movement in enumerate(["through", "left"], start=1):
                key = f"{direction}_{movement}"
                sb = self._make_spinbox(key)
                self._spinboxes[key] = sb
                grid.addWidget(sb, row, col)

        root.addLayout(grid)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_compute = QPushButton("自动生成配时方案")
        self._btn_compute.setToolTip("根据当前流量用 Webster 算法计算最优配时")
        self._btn_compute.clicked.connect(self._on_compute_clicked)

        self._btn_reset = QPushButton("重置为默认值")
        self._btn_reset.setToolTip("恢复设计文档的标准流量")
        self._btn_reset.clicked.connect(self._reset_to_defaults)

        btn_row.addWidget(self._btn_compute)
        btn_row.addWidget(self._btn_reset)
        btn_row.addStretch()
        root.addLayout(btn_row)

    def _make_spinbox(self, key: str) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(0.0, config.FLOW_INPUT_MAX)
        sb.setSingleStep(10.0)
        sb.setDecimals(0)
        sb.setSuffix(" 辆/h")
        sb.setFixedWidth(130)
        return sb

    # ── Slots ──────────────────────────────────────────────────────────────────

    def _on_compute_clicked(self) -> None:
        """Validate inputs and emit flow_submitted."""
        fd = self.get_flow()
        errors = fd.validate()
        if errors:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "流量输入有误",
                                "\n".join(errors))
            return
        self.flow_submitted.emit(fd)

    def _reset_to_defaults(self) -> None:
        for key, sb in self._spinboxes.items():
            sb.setValue(config.DEFAULT_FLOWS.get(key, 0.0))

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_flow(self, fd: FlowData) -> None:
        """Fill SpinBoxes from a FlowData instance."""
        for key, sb in self._spinboxes.items():
            sb.setValue(fd.flows.get(key, 0.0))

    def get_flow(self) -> FlowData:
        """Read current SpinBox values and return a FlowData instance."""
        flows = {key: sb.value() for key, sb in self._spinboxes.items()}
        return FlowData(flows=flows)
