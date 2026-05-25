"""
gui/main_window.py — 主窗口，集成四个面板与仿真控制。

布局：
  ┌──────────────────────────────────────────────────────────────────┐
  │  菜单栏（文件 / 帮助）                                            │
  ├────────────┬──────────────┬──────────────────┬───────────────────┤
  │ FlowPanel  │ TimingPanel  │ MonitorPanel     │ SchedulePanel     │
  │（流量输入） │（配时方案）   │（实时监控）       │（方案库 & 调度）   │
  ├────────────┴──────────────┴──────────────────┴───────────────────┤
  │ [▶启动]  [■停止]  速度:──●── 10×  [☐可视化]  [☑自动车流]         │
  │ [📥导出结果 ℹ]  [📄导出配时]               状态栏                │
  └──────────────────────────────────────────────────────────────────┘

完整信号链：
  flow_panel.flow_submitted         → _on_flow_submitted  → engine.compute → timing_panel.display_plan
  timing_panel.plan_applied         → _on_plan_applied    → adapter.apply_timing + dm.save_plan
  schedule_panel.save_scenario_req  → _on_save_scenario   → dm.save_scenario + refresh list
  schedule_panel.load_scenario_req  → _on_load_scenario   → timing/flow panels updated
  start_btn.clicked                 → _on_start
  stop_btn.clicked                  → _on_stop
  speed_slider.valueChanged         → _on_speed_changed
  sim_ctrl.status_updated           → monitor_panel.update_status
  sim_ctrl.sim_finished             → _on_sim_finished
  sim_ctrl.error_occurred           → _on_sim_error
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout,
    QToolBar, QPushButton, QSlider, QLabel, QCheckBox,
    QAction, QFileDialog, QMessageBox, QStatusBar,
)

import config
from core.data_manager import DataManager
from core.data_models import FlowData, NamedScenario, ScheduleEntry, TimingPlan
from core.timing_engine import TimingEngine
from core.traci_adapter import TraCIAdapter
from core.sim_controller import SimController
from gui.flow_panel import FlowPanel
from gui.timing_panel import TimingPanel
from gui.monitor_panel import MonitorPanel
from gui.schedule_panel import SchedulePanel

# ── Exported-results field definitions (shown in tooltip / help dialog) ────────
_EXPORT_FIELD_HELP = """\
仿真结果 CSV 各列含义：

id                — 记录自增编号
plan_id           — 所用配时方案内部 ID
scenario_name     — 用户自定义方案名称
sim_duration_s    — 仿真总步数（步长 1s，即持续时间，单位 s）

avg_delay_s_veh   — 全网平均延误（所有在途车辆等待时间均值，s/辆）
                    "等待时间" = 车速 < 0.1 m/s 的累计时长，是信号延误的良好近似。

delay_N/S/E/W_s_veh
                  — 各进口平均延误（进口边 n2c/s2c/e2c/w2c 上
                    所有车辆等待时间之和 / 该边车辆数，s/辆）

mvt_{D}_{m}_s     — 各行进方向平均延误（D = N/S/E/W，m = through/left/right）
                    通过车辆路由 ID 识别行进方向，对应进口边上各车的等待时间均值。
                    直行(through) & 右转(right) 共用 lane 0，左转(left) 使用 lane 1；
                    右转车辆由路由 ID = route_{D}_right 识别后单独统计。

avg_queue_m       — 全网各进口排队长度均值（停驶车辆数 × 7.5 m 估算，m）
throughput_veh    — 仿真期间完成全程行驶的车辆总数
speed_factor      — 仿真倍速系数（GUI 滑块值）
created_at        — 记录时间戳（ISO 格式）
"""


class MainWindow(QMainWindow):
    """
    Top-level application window.

    Owns the four core objects:
      - TimingEngine   (stateless algorithm wrapper)
      - DataManager    (SQLite persistence)
      - TraCIAdapter   (SUMO TraCI connection, created once per run)
      - SimController  (QThread, created fresh for each simulation run)
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(config.WINDOW_TITLE)
        self.resize(1440, 640)

        # Core objects
        self._engine   = TimingEngine()
        self._dm       = DataManager()
        self._adapter  = TraCIAdapter()
        self._sim_ctrl: Optional[SimController] = None

        # Build UI
        self._build_menu()
        self._build_central()
        self._build_toolbar()
        self._build_statusbar()

        # Connect panel signals
        self._flow_panel.flow_submitted.connect(self._on_flow_submitted)
        self._timing_panel.plan_applied.connect(self._on_plan_applied)
        self._schedule_panel.save_scenario_requested.connect(self._on_save_scenario)
        self._schedule_panel.load_scenario_requested.connect(self._on_load_scenario)

        self._set_sim_running(False)

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("文件(&F)")

        act_export = QAction("导出仿真结果 CSV(&E)...", self)
        act_export.triggered.connect(self._on_export)
        file_menu.addAction(act_export)

        act_export_plans = QAction("导出配时方案 CSV(&P)...", self)
        act_export_plans.triggered.connect(self._on_export_plans)
        file_menu.addAction(act_export_plans)

        act_export_sc = QAction("导出命名方案 CSV(&S)...", self)
        act_export_sc.triggered.connect(self._on_export_scenarios)
        file_menu.addAction(act_export_sc)

        file_menu.addSeparator()

        act_quit = QAction("退出(&Q)", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        help_menu = menu.addMenu("帮助(&H)")
        act_result_help = QAction("导出结果字段说明(&R)", self)
        act_result_help.triggered.connect(self._on_result_help)
        help_menu.addAction(act_result_help)

        act_about = QAction("关于(&A)", self)
        act_about.triggered.connect(self._on_about)
        help_menu.addAction(act_about)

    # ── Central widget ────────────────────────────────────────────────────────

    def _build_central(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        self._flow_panel     = FlowPanel()
        self._timing_panel   = TimingPanel()
        self._monitor_panel  = MonitorPanel()
        self._schedule_panel = SchedulePanel(self._dm)

        h_layout = QHBoxLayout(central)
        h_layout.setSpacing(8)
        h_layout.setContentsMargins(8, 8, 8, 4)
        h_layout.addWidget(self._flow_panel,     stretch=2)
        h_layout.addWidget(self._timing_panel,   stretch=2)
        h_layout.addWidget(self._monitor_panel,  stretch=3)
        h_layout.addWidget(self._schedule_panel, stretch=3)

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        tb = QToolBar("仿真控制")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(Qt.BottomToolBarArea, tb)

        # Start / Stop
        self._btn_start = QPushButton("▶  启动仿真")
        self._btn_start.setFixedHeight(32)
        self._btn_start.clicked.connect(self._on_start)
        tb.addWidget(self._btn_start)

        self._btn_stop = QPushButton("■  停止")
        self._btn_stop.setFixedHeight(32)
        self._btn_stop.clicked.connect(self._on_stop)
        tb.addWidget(self._btn_stop)

        tb.addSeparator()

        # Speed slider
        tb.addWidget(QLabel("速度："))
        self._speed_slider = QSlider(Qt.Horizontal)
        self._speed_slider.setRange(config.SIM_SPEED_MIN, config.SIM_SPEED_MAX)
        self._speed_slider.setValue(config.SIM_SPEED_DEFAULT)
        self._speed_slider.setFixedWidth(130)
        self._speed_slider.setTickPosition(QSlider.TicksBelow)
        self._speed_slider.setTickInterval(5)
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        tb.addWidget(self._speed_slider)

        self._lbl_speed = QLabel(f"{config.SIM_SPEED_DEFAULT}×")
        self._lbl_speed.setFixedWidth(34)
        tb.addWidget(self._lbl_speed)

        tb.addSeparator()

        # SUMO visualisation checkbox
        self._chk_sumo_gui = QCheckBox("SUMO 可视化")
        self._chk_sumo_gui.setChecked(False)
        self._chk_sumo_gui.setToolTip(
            "勾选后以 sumo-gui 图形模式启动仿真，可在 SUMO 窗口中观察车辆运行。\n"
            "取消勾选则以后台模式运行（速度更快）。")
        tb.addWidget(self._chk_sumo_gui)

        # Auto-vehicle checkbox
        self._chk_auto_veh = QCheckBox("自动生成车流")
        self._chk_auto_veh.setChecked(True)
        self._chk_auto_veh.setToolTip(
            "勾选后每步按泊松过程随机插入车辆（叠加路由文件的静态 flow）")
        tb.addWidget(self._chk_auto_veh)

        tb.addSeparator()

        # Export results + help button
        self._btn_export = QPushButton("📥  导出结果")
        self._btn_export.setFixedHeight(32)
        self._btn_export.setToolTip(
            "将所有仿真结果导出为 CSV。\n"
            "点击菜单「帮助 → 导出结果字段说明」可查看各列定义。"
        )
        self._btn_export.clicked.connect(self._on_export)
        tb.addWidget(self._btn_export)

        btn_help = QPushButton("ℹ")
        btn_help.setFixedSize(28, 32)
        btn_help.setToolTip("查看导出结果各字段的含义与计算方法")
        btn_help.clicked.connect(self._on_result_help)
        tb.addWidget(btn_help)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_bar = sb
        sb.showMessage("就绪。输入流量 → 生成配时方案 → 点击「启动仿真」。")

    # ── UI state helpers ──────────────────────────────────────────────────────

    def _set_sim_running(self, running: bool) -> None:
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        self._chk_sumo_gui.setEnabled(not running)
        self._chk_auto_veh.setEnabled(not running)
        self._flow_panel.setEnabled(not running)
        self._schedule_panel.setEnabled(not running)

    # ── Panel signal handlers ─────────────────────────────────────────────────

    def _on_flow_submitted(self, fd: FlowData) -> None:
        try:
            plan = self._engine.compute(fd)
        except Exception as exc:
            QMessageBox.critical(self, "计算失败", str(exc))
            return
        self._dm.save_flow(fd)
        self._timing_panel.display_plan(plan)
        self._status_bar.showMessage(
            f"Webster 配时完成：{self._engine.plan_summary(plan)}"
        )

    def _on_plan_applied(self, plan: TimingPlan) -> None:
        self._dm.save_plan(plan)
        if self._adapter.is_connected and self._sim_ctrl is not None:
            self._sim_ctrl.request_timing_change(plan)
            self._status_bar.showMessage(
                f"配时方案已排队下发（下一步生效）：{plan.summary()}"
            )
        else:
            self._status_bar.showMessage(
                f"方案已保存（仿真未运行，将在下次启动时生效）：{plan.summary()}"
            )

    def _on_save_scenario(self, name: str) -> None:
        """Save current timing + flow as a named scenario."""
        from core.data_models import NamedScenario
        plan = self._timing_panel.current_plan()
        plan.name = name
        flow = self._flow_panel.get_flow()
        sc = NamedScenario(name=name, plan=plan, flow=flow)
        self._dm.save_scenario(sc)
        self._schedule_panel.refresh_scenario_list()
        self._status_bar.showMessage(f"方案「{name}」已保存至方案库。")

    def _on_load_scenario(self, sc: NamedScenario) -> None:
        """Load a named scenario into the timing and flow panels."""
        self._timing_panel.display_plan(sc.plan)
        if sc.flow:
            self._flow_panel.load_flow(sc.flow)
        self._status_bar.showMessage(
            f"已加载方案「{sc.name}」到配时面板。"
        )

    # ── Toolbar handlers ──────────────────────────────────────────────────────

    def _on_start(self) -> None:
        plan = self._timing_panel.current_plan()
        errors = self._engine.validate(plan)
        if errors:
            QMessageBox.warning(
                self, "配时方案有误",
                "请先在配时面板修正以下问题：\n\n" + "\n".join(errors),
            )
            return

        # Disconnect any stale previous adapter
        if self._adapter.is_connected:
            self._adapter.disconnect()

        use_gui = self._chk_sumo_gui.isChecked()
        try:
            self._status_bar.showMessage("正在启动 SUMO，请稍候…")
            self.repaint()
            self._adapter.connect(use_gui=use_gui)
            self._adapter.apply_timing(plan)
        except Exception as exc:
            QMessageBox.critical(
                self, "SUMO 启动失败",
                f"无法连接 SUMO：\n{exc}\n\n"
                "请确认 SUMO 已安装、SUMO_HOME 已设置，并已运行 generate_net.bat。",
            )
            return

        # Resolve schedule
        schedule = None
        scenario_name = plan.name or plan.plan_id
        if self._schedule_panel.has_schedule():
            schedule = self._schedule_panel.build_schedule()
            if schedule:
                scenario_name = "调度运行"

        flow_data  = self._flow_panel.get_flow()
        auto_veh   = self._chk_auto_veh.isChecked()

        self._sim_ctrl = SimController(
            adapter=self._adapter,
            data_manager=self._dm,
            plan=plan,
            flows=flow_data,
            auto_vehicles=auto_veh,
            schedule=schedule,
            scenario_name=scenario_name,
        )

        self._sim_ctrl.status_updated.connect(self._monitor_panel.update_status)
        self._sim_ctrl.sim_finished.connect(self._on_sim_finished)
        self._sim_ctrl.error_occurred.connect(self._on_sim_error)
        self._sim_ctrl.set_speed(self._speed_slider.value())

        self._set_sim_running(True)
        self._monitor_panel.reset()
        self._sim_ctrl.start()

        gui_flag = " [SUMO-GUI]" if use_gui else ""
        sched_flag = f" [调度: {len(schedule)} 时段]" if schedule else ""
        self._status_bar.showMessage(
            f"仿真运行中…{gui_flag}{sched_flag}  方案：{plan.summary()}"
        )

    def _on_stop(self) -> None:
        if self._sim_ctrl is not None:
            self._sim_ctrl.stop()
        self._status_bar.showMessage("正在停止仿真，请等待当前步完成…")

    def _on_speed_changed(self, value: int) -> None:
        self._lbl_speed.setText(f"{value}×")
        if self._sim_ctrl is not None:
            self._sim_ctrl.set_speed(value)

    # ── SimController signal handlers ─────────────────────────────────────────

    def _on_sim_finished(self, result) -> None:
        self._set_sim_running(False)
        self._sim_ctrl = None
        per_dir = "  ".join(
            f"{d}:{v:.1f}s"
            for d, v in sorted(result.delay_by_approach.items())
            if v > 0
        )
        msg = (
            f"仿真结束。"
            f"  平均延误 {result.avg_delay:.1f} s/辆"
            f"  通过量 {result.throughput} 辆"
            f"  运行 {result.sim_duration:.0f} 步"
        )
        if per_dir:
            msg += f"  各进口：{per_dir}"
        self._status_bar.showMessage(msg)

    def _on_sim_error(self, error_msg: str) -> None:
        self._set_sim_running(False)
        self._sim_ctrl = None
        QMessageBox.critical(self, "仿真运行错误", error_msg)
        self._status_bar.showMessage("仿真因错误终止。")

    # ── Export handlers ───────────────────────────────────────────────────────

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出仿真结果", "sim_results.csv",
            "CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            n = self._dm.export_results_csv(path)
            self._status_bar.showMessage(f"已导出 {n} 条仿真结果 → {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _on_export_plans(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配时方案", "timing_plans.csv",
            "CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            n = self._dm.export_plans_csv(path)
            self._status_bar.showMessage(f"已导出 {n} 条配时方案 → {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _on_export_scenarios(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出命名方案", "named_scenarios.csv",
            "CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            n = self._dm.export_scenarios_csv(path)
            self._status_bar.showMessage(f"已导出 {n} 条命名方案 → {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _on_result_help(self) -> None:
        QMessageBox.information(self, "导出结果字段说明", _EXPORT_FIELD_HELP)

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "关于本软件",
            "<b>SUMO 固定信号配时系统 v1.1</b><br><br>"
            "基于 SUMO TraCI 的四相位交叉口信号优化工具。<br>"
            "使用 Webster (1958) 最优周期算法。<br><br>"
            "路网：十字形单交叉口，4 进口各 2 车道<br>"
            "周期范围：30~180 s<br>"
            "功能：手动/自动配时、分时段调度、命名方案库、"
            "各行进方向延误统计",
        )

    # ── Window lifecycle ──────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._sim_ctrl is not None and self._sim_ctrl.isRunning():
            reply = QMessageBox.question(
                self, "仿真正在运行",
                "仿真尚未停止，确认退出并强制终止？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self._sim_ctrl.stop()
            self._sim_ctrl.wait(3000)

        self._adapter.disconnect()
        self._dm.close()
        event.accept()
