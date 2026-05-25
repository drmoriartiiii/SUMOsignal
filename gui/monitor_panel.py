"""
gui/monitor_panel.py — 实时仿真监控面板。

显示内容（只读，由 SimController.status_updated 信号驱动刷新）：
  ① 仿真时刻、在途车辆数、通过量
  ② 当前信号相位（颜色高亮）、剩余绿灯
  ③ 全网平均延误
  ④ 各进口排队长度（≥60 m 变红）和进口级延误
  ⑤ 各行进方向延误（12 条：N/S/E/W × 直行/左转/右转）

颜色规则：
  - 相位名称：绿色（绿灯）/ 橙色（黄灯）/ 红色（全红）
  - 排队 ≥ 60 m：数值标签变红
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QGridLayout, QLabel, QGroupBox, QFrame, QVBoxLayout,
    QScrollArea,
)

import config
from core.data_models import SimStatus

_QUEUE_WARN_M = 60.0

# SUMO tlLogic phase_index (0-11) → (显示文字, 样式)
_PHASE_DISPLAY: dict[int, tuple[str, str]] = {
    0:  ("N-S 直行 🟢", "color:#1a7f1a;font-weight:bold;"),
    1:  ("N-S 直行 🟡", "color:#b88000;font-weight:bold;"),
    2:  ("N-S 直行 🔴", "color:#c0392b;font-weight:bold;"),
    3:  ("N-S 左转 🟢", "color:#1a7f1a;font-weight:bold;"),
    4:  ("N-S 左转 🟡", "color:#b88000;font-weight:bold;"),
    5:  ("N-S 左转 🔴", "color:#c0392b;font-weight:bold;"),
    6:  ("E-W 直行 🟢", "color:#1a7f1a;font-weight:bold;"),
    7:  ("E-W 直行 🟡", "color:#b88000;font-weight:bold;"),
    8:  ("E-W 直行 🔴", "color:#c0392b;font-weight:bold;"),
    9:  ("E-W 左转 🟢", "color:#1a7f1a;font-weight:bold;"),
    10: ("E-W 左转 🟡", "color:#b88000;font-weight:bold;"),
    11: ("E-W 左转 🔴", "color:#c0392b;font-weight:bold;"),
}

_DIR_ZH   = {"N": "北", "S": "南", "E": "东", "W": "西"}
_MVT_ZH   = {"through": "直行", "left": "左转", "right": "右转"}
_DIRS     = list(_DIR_ZH.keys())
_MOVEMENTS = ["through", "left", "right"]


def _val(text: str = "—") -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return lbl


class MonitorPanel(QGroupBox):
    """
    Read-only real-time monitoring panel.

    Call  update_status(SimStatus)  from the slot connected to
    SimController.status_updated to refresh all fields.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("📊 实时监控", parent)
        self._queue_labels: dict[str, QLabel] = {}
        self._approach_delay_labels: dict[str, QLabel] = {}
        self._mvt_delay_labels: dict[str, QLabel] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        # ── ① 概要 ─────────────────────────────────────────────────────────
        g1 = QGridLayout(); g1.setSpacing(4)
        g1.addWidget(QLabel("仿真时刻"), 0, 0)
        self._lbl_time = _val()
        g1.addWidget(self._lbl_time, 0, 1)
        g1.addWidget(QLabel("在途车辆"), 0, 2)
        self._lbl_vehs = _val()
        g1.addWidget(self._lbl_vehs, 0, 3)

        g1.addWidget(QLabel("当前相位"), 1, 0)
        self._lbl_phase = QLabel("—")
        self._lbl_phase.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        g1.addWidget(self._lbl_phase, 1, 1)
        g1.addWidget(QLabel("通过量"), 1, 2)
        self._lbl_thru = _val()
        g1.addWidget(self._lbl_thru, 1, 3)

        g1.addWidget(QLabel("剩余绿灯"), 2, 0)
        self._lbl_rem = _val()
        g1.addWidget(self._lbl_rem, 2, 1)
        g1.addWidget(QLabel("全网延误"), 2, 2)
        self._lbl_delay = _val()
        g1.addWidget(self._lbl_delay, 2, 3)
        root.addLayout(g1)

        root.addWidget(_hsep())

        # ── ② 各进口排队 + 进口延误 ────────────────────────────────────────
        g2 = QGridLayout(); g2.setSpacing(4)
        g2.addWidget(QLabel("<b>进口</b>"),          0, 0)
        g2.addWidget(QLabel("<b>排队（m）</b>"),       0, 1)
        g2.addWidget(QLabel("<b>进口延误（s/辆）</b>"), 0, 2)
        for row, (d, zh) in enumerate(_DIR_ZH.items(), start=1):
            g2.addWidget(QLabel(f"{zh}（{d}）"), row, 0)
            q_lbl = _val(); self._queue_labels[d] = q_lbl
            g2.addWidget(q_lbl, row, 1)
            a_lbl = _val(); self._approach_delay_labels[d] = a_lbl
            g2.addWidget(a_lbl, row, 2)
        root.addLayout(g2)

        root.addWidget(_hsep())

        # ── ③ 各行进方向延误（12 行，含右转） ──────────────────────────────
        root.addWidget(QLabel("<b>行进方向延误（s/辆）</b>:"))
        mvt_grid = QGridLayout(); mvt_grid.setSpacing(3)
        mvt_grid.addWidget(QLabel("方向"),      0, 0)
        mvt_grid.addWidget(QLabel("直行"),      0, 1)
        mvt_grid.addWidget(QLabel("左转"),      0, 2)
        mvt_grid.addWidget(QLabel("右转"),      0, 3)

        for row, (d, zh) in enumerate(_DIR_ZH.items(), start=1):
            mvt_grid.addWidget(QLabel(f"{zh}"), row, 0)
            for col, mvt in enumerate(_MOVEMENTS, start=1):
                lbl = _val()
                self._mvt_delay_labels[f"{d}_{mvt}"] = lbl
                mvt_grid.addWidget(lbl, row, col)

        root.addLayout(mvt_grid)

    # ── Public API ─────────────────────────────────────────────────────────────

    def update_status(self, status: SimStatus) -> None:
        """Refresh all displayed values. Must be called from the main thread."""
        self._lbl_time.setText(status.sim_time_str)
        self._lbl_vehs.setText(str(status.vehicle_count))
        self._lbl_thru.setText(str(status.throughput))
        self._lbl_delay.setText(f"{status.avg_delay:.1f} s/辆")

        phase_text, phase_style = _PHASE_DISPLAY.get(
            status.phase_index, (f"相位 {status.phase_index}", ""))
        self._lbl_phase.setText(phase_text)
        self._lbl_phase.setStyleSheet(phase_style)

        if status.phase_index % 3 == 0:
            self._lbl_rem.setText(f"<b>{status.phase_remaining:.1f} s</b>")
        else:
            self._lbl_rem.setText(f"{status.phase_remaining:.1f} s")

        for d, q_lbl in self._queue_labels.items():
            q = status.queue_lengths.get(d, 0.0)
            q_lbl.setText(f"{q:.1f}")
            q_lbl.setStyleSheet(
                "color:red;font-weight:bold;" if q >= _QUEUE_WARN_M else "")

        for d, a_lbl in self._approach_delay_labels.items():
            v = status.delay_by_approach.get(d, 0.0)
            a_lbl.setText(f"{v:.1f}")

        for key, m_lbl in self._mvt_delay_labels.items():
            v = status.delay_by_movement.get(key, 0.0)
            m_lbl.setText(f"{v:.1f}" if v > 0 else "—")

    def reset(self) -> None:
        """Reset all values to idle state."""
        for lbl in (self._lbl_time, self._lbl_vehs, self._lbl_thru,
                    self._lbl_rem, self._lbl_delay):
            lbl.setText("—"); lbl.setStyleSheet("")
        self._lbl_phase.setText("—"); self._lbl_phase.setStyleSheet("")
        for d in _DIRS:
            for lbl in (self._queue_labels.get(d),
                        self._approach_delay_labels.get(d)):
                if lbl:
                    lbl.setText("—"); lbl.setStyleSheet("")
        for lbl in self._mvt_delay_labels.values():
            lbl.setText("—"); lbl.setStyleSheet("")


def _hsep() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep
