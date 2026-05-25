# SUMO 固定信号配时系统 — Agent 交接文档

> 本文档由 Agent 持续维护，供下一个 Agent 读取后直接继续开发。  
> 项目路径：`D:\Moriarty\SUMOsoftware`

---

## 0. 开发日志（每次写出新代码后必须更新）

> **给下一个 Agent 的要求：**  
> 每完成一段代码或修复一个 bug，立即在本节末尾追加一条日志，格式见下方示例。  
> 保持简洁（1-3 句），重点写**为什么这样设计**或**踩了什么坑**，而不是复述代码内容。

---

### 2026-05-25  第一部分 — SUMO 路网 + 基础数据层

**代码思路**
- SUMO 路网用四个源文件（nod/edg/con/tll）分离关注点，通过 `generate_net.bat` 调用 `netconvert` 生成最终 `net.xml`，避免手写复杂的 net.xml。
- 每个进口 2 车道（lane 0 直行+右转，lane 1 左转），对应 4 相位保护左转的信号设计。
- `data_models.py` 中 `build_tl_states()` 在运行时构造 TL 状态字符串，而非硬编码——因为 netconvert 自动分配的 `linkIndex` 顺序无法从输入文件预测。

**Bug 修正**
- `intersection.con.xml` 初版使用了 `tl=` 和 `linkIndex=` 属性，但这两个属性只存在于 netconvert 的**输出** net.xml 中，输入连接文件不支持。修复：移除这两个属性，同时去掉 `--tllogic-files` 参数，让 netconvert 自动生成初始信号逻辑。
- XML 注释内含 `--` 序列（如 `--net-file`）触发 XML 解析器报错。修复：重写注释，用自然语言替代命令行示例。

---

### 2026-05-25  第二部分 — 算法引擎 + 数据管理层

**代码思路**
- `TimingEngine` 作为算法门面，上层（GUI、TraCIAdapter）只与它交互，不直接调用 `webster()`，实现算法与 I/O 的完全隔离。
- `DataManager` 使用模块级函数 `_enable_wal` 注册 SQLAlchemy `"connect"` 事件（而不是在 `__init__` 内定义 decorator），避免闭包作用域问题导致事件未被注册。
- ORM 行转 dataclass 使用显式字段赋值（`_row_to_plan` 静态方法），避免 `row.__dict__` 携带 `_sa_instance_state` 污染 `from_db_dict()`。

**Bug 修正（测试驱动发现）**
- `build_tl_states()` 中右转键的检测用 `endswith("_right")`，但 `link_map` 的键格式是 `"from_edge_to_edge"`（如 `"n2c_c2w"`），不含 `_right` 后缀，导致右转在所有相位都显示为红灯。修复：改为从四个受控组（8 条直行+左转）之外的剩余键推断右转。
- `adjust_to_cycle()` 对 min_green 截断后未做二次缩减：当某相位绿灯被截断至 10s 时，总绿灯量超出目标，实际周期比目标大 2s。修复：截断后若总量超出，按比例从仍高于 min_green 的相位减去超出量（与 `webster()` 逻辑一致）。

---

### 2026-05-25  第五部分 — 主窗口 + 入口

**代码思路**
- `MainWindow` 持有 `TimingEngine`、`DataManager`、`TraCIAdapter`、`SimController`（后者每次启动仿真时新建实例，结束后置 `None`）。`TraCIAdapter` 复用同一对象，每次启动前若已连接先调 `disconnect()` 重置，再重新 `connect()`。
- 「下发方案至 SUMO」按钮在仿真运行时通过 `sim_ctrl.request_timing_change(plan)` 推送（线程安全队列机制），不运行时只保存到 DB 并提示"下次启动生效"——两种状态共用一个按钮，逻辑分支在 `_on_plan_applied()` 中判断。
- 工具栏用 `Qt.BottomToolBarArea` 放在底部，`setMovable(False)` 固定位置，避免用户误拖。速度滑块 `valueChanged` 同时更新显示标签和 `sim_ctrl.set_speed()`，仿真未运行时调用无副作用（sim_ctrl 为 None，跳过）。
- `closeEvent` 检测仿真是否运行，是则弹对话框确认；确认后依次 `sim_ctrl.stop() → wait(3000) → adapter.disconnect() → dm.close()`，保证资源有序释放。
- HiDPI 属性在 `QApplication` 创建前设置（必须在此时设置，否则无效）。

**Bug 修正**
- `traci.start()` 会自动选择一个空闲端口并在内部把 `--remote-port <port>` 追加到命令行。`intersection.sumocfg` 里同时存在 `<traci_server><remote-port value="8813"/></traci_server>`，导致 SUMO 收到两个 `--remote-port`，报错 "A value for the option 'remote-port' was already set"。修复：从 sumocfg 中删去 `<traci_server>` 块，并去掉 `traci_adapter.py` cmd 列表中的 `--remote-port` 参数，完全交由 `traci.start()` 管理端口。
- `intersection.sumocfg` 注释中含 `--remote-port` 命令行示例，触发 XML `--` 非法序列报错（老问题再现）。修复：将注释中的命令行示例改为自然语言描述，去掉所有 `--` 序列。

---

### 2026-05-25  第四部分 — GUI 面板

**代码思路**
- 三个面板均继承 `QGroupBox`（而非裸 `QWidget`），自带标题边框，无需在主窗口额外套 frame，布局更简洁。
- `FlowPanel` 的 `_spinboxes` 用 `{route_key: QDoubleSpinBox}` 字典，route_key 格式与 `config.DEFAULT_FLOWS`、`FlowData.flows` 完全一致（`"N_through"`、`"N_left"` 等），`load_flow()` 和 `get_flow()` 直接遍历这个字典，无需额外映射。
- `TimingPanel` 用两套显示结构共存（只读 `QLabel` + 隐藏的 `QDoubleSpinBox`），切换编辑模式时只切换可见性，避免布局抖动。`_on_green_changed` 连接所有 SpinBox 的 `valueChanged`，实现实时验证和周期预览——错误时显示红色提示，正常时隐藏。
- `MonitorPanel` 的相位颜色查表用 `dict[int, tuple[str, str]]`（12 条），直接把 SUMO `phase_index`（0-11）映射到显示文字和样式字符串，避免条件判断分支。排队红色阈值 60 m 定义为模块级常量 `_QUEUE_WARN_M`，方便日后配置化。
- `SimController` 的 `status_updated` 信号直接连接 `MonitorPanel.update_status`（Qt 自动为跨线程连接建立队列连接，无需手动 `moveToThread`）。

**无 Bug，import 测试通过**

---

### 2026-05-25  第三部分 — TraCI 适配层 + 仿真控制线程

**代码思路**
- `TraCIAdapter` 使用 `traci.start()` 而不是手动 `subprocess.Popen + traci.init()`——前者在内部等待端口就绪，无需手动 `time.sleep()`。
- `traci` 模块延迟导入（在 `connect()` 中 `import traci`）并保存为 `self._traci`，使得整个 `core/` 包无 SUMO 时仍可 import 和测试，不影响 Part 2 的测试。
- `_build_link_map()` 使用 `rsplit("_", 1)` 提取 edge ID（而不是简单 `split("_")`），兼容边名本身含有下划线的情况（虽然当前路网边名不含下划线，但这是正确的通用做法）。
- `apply_timing()` 每次调用都重建完整的 12 个 Phase 对象并调用 `setProgramLogic`，而不是只修改时长——这样可以在仿真中途安全切换方案，无需重置仿真时钟。
- `get_status()` 中的平均延误直接遍历 `vehicle.getWaitingTime(vid)`，适合小规模路网。大路网应改用 `edge.getWaitingTime(edge_id)` 汇总，避免单步内的大量 TraCI 调用。
- 队列长度用 `halting_vehicles × 7.5m` 估算，这与 SUMO 默认车辆参数（length=5m, minGap=2.5m）一致。
- `add_vehicles()` 用 `dyn_` 前缀命名动态车辆（区别于路由文件中的静态 `<flow>` 车辆），二者可并存；`auto_vehicles=False` 时可关闭动态生成，仅依赖路由文件的静态流量。
- `SimController` 继承 `QThread`，信号跨线程自动使用队列连接（Qt 机制），GUI 更新安全。
- `SimController` 的 `_speed` 用 `threading.Lock` 保护（不是 GIL 裸访问），因为 `set_speed()` 做 read-modify-write；`_running` 和 `_pending_plan` 是单次赋值，GIL 已足够。
- `run()` 结束后在 `finally` 块中调用 `adapter.disconnect()` 并发射 `sim_finished`，确保无论正常退出还是异常都能清理连接。

**无 Bug，测试全通过（30/30）**
- 新增的 Section 5/6 测试覆盖 adapter 初始状态、disconnect 幂等性、SimController 信号/方法存在性、set_speed 边界夹紧、request_timing_change / stop 逻辑。

---

## 1. 项目背景

### 来源对话
- **Claude 官网对话**（分享链接：`https://claude.ai/share/7e9e08d0-b398-49f5-8bef-b5808398b836`）：用户 Moriartii 与 Claude 的三轮对话，设计了基于 SUMO TraCI 的交叉口单点固定信号配时软件。
- **Cursor 对话**（`empty-window` 项目，transcript UUID `1b2e86b9-0dfd-4d06-9981-5c380c545dcd`）：读取上述对话内容、确认技术栈与代码分部规划。
- **当前 Cursor 对话**（`d-Moriarty-SUMOsoftware` 项目）：第一部分和第二部分已完成。

### 系统设计文档
完整 HTML 设计文档位于：`C:\Users\叶飞扬\Downloads\sumo_signal_system_design.html`  
六个标签页：① 物理结构  ② 逻辑结构·DFD  ③ 用例关系图  ④ 软件模块设计  ⑤ 人机交互设计  ⑥ 数据结构与数据库

---

### 2026-05-25  四项新需求（黄灯 / 调度 / 导出说明 / 行进方向延误）

**改动概要**

1. **手动配时各相位黄灯时长可调（需求1）**
   - `TimingPlan.yellow: float` → `yellow_times: List[float]`（4 个独立值）；`all_red` 固定 1s 不变。
   - `validate()` 增加 `yellow_times` 长度和取值范围（1~10s）检查。
   - `to_db_dict()` / `from_db_dict()` 改用 `yellow_times_json`；向后兼容旧 `yellow` 标量列。
   - `timing_engine.py`：所有构造/调整函数接受 `yellow_times` 列表；Webster 固定 `[3.0]*4`；新增 `actual_cycle()` 方法。
   - `traci_adapter.apply_timing()`：逐相位使用 `plan.yellow_times[i]` 构建 SUMO Phase。
   - `gui/timing_panel.py`：每相位增加黄灯 SpinBox（1~10s）；周期预览改为 Σgreen + Σyellow + 4×1。

2. **分时段调度 + 命名方案库（需求2）**
   - 新增 `NamedScenario`（name + plan + flow）和 `ScheduleEntry`（start/end + plan + flow）dataclass。
   - `data_manager.py`：新增 `named_scenarios` 表 + CRUD + `export_scenarios_csv()`；`sim_results` 增 `scenario_name`/`delay_json`/`movement_delay_json` 列；`timing_plans` 增 `name`/`yellow_times_json` 列；`_migrate()` 自动 ALTER TABLE 补列。
   - `sim_controller.py`：接受 `schedule: List[ScheduleEntry]`；run() 在对应仿真时刻切换方案和流量；`_max_steps` 由 schedule 尾部决定。
   - 新增 `gui/schedule_panel.py`：方案库（双击加载）+ 分时段调度表。
   - `gui/main_window.py`：增加第四列面板；`_on_save_scenario()` / `_on_load_scenario()` 相互联动配时/流量面板。

3. **导出结果字段说明（需求3）**
   - 工具栏新增「ℹ」按钮，弹出逐列说明对话框；菜单「帮助 → 导出结果字段说明」同效。

4. **各行进方向延误修复与扩展（需求4）**
   - **根本修复**：`_SimResultRow` 原无 `delay_json` 列导致导出 CSV 时进口延误恒为空。修复：增加 `delay_json` 和 `movement_delay_json` 列，save/load 均序列化。
   - `traci_adapter._calc_movement_delays()`：对进口边车辆按路由名（`route_N_through` 等）归桶，计算 12 行进方向均值延误。
   - `SimStatus` / `SimResult` 增加 `delay_by_movement`（12 键）。
   - `monitor_panel.py`：新增 4×3 行进方向延误表格。
   - CSV 增加 12 列 `mvt_{D}_{m}_s`。

**测试**：30/30 全部通过。

**给下一 Agent 的注意事项**
- `plan.yellow` 已删除，改用 `plan.yellow_times[i]`。
- 旧 DB 文件会被 `_migrate()` 自动补列，无需手动迁移。
- `SchedulePanel` 删除按钮的 lambda 捕获固定行号，不支持行移动；若要支持需改为动态查找行号。

---

### 2026-05-25  方案A回归 + GUI 完整功能

**背景**
用户讨论是否将 NS直行 与 EW左转 配对同时放行（以及反向组合）。我临时实现了配对约束
和配对 Webster 算法，但随后确认这在常规四相位保护左转设计中属于相位冲突，用户选择方案A——
恢复四相位完全独立。

**代码改动**
1. `core/data_models.py`：从 `TimingPlan.validate()` 中移除配对约束检查（`green_times[0]==green_times[3]` 等），恢复原始逻辑；`delay_by_approach` 字段保留。
2. `core/timing_engine.py`：完整重写回独立 Webster 四相位——`compute()` 直接调 `webster()`，`plan_from_green_times()` 接受 4 个独立参数，`default_plan()` 恢复 96 s 方案，`adjust_to_cycle()` 无配对约束。
3. `gui/timing_panel.py`：去掉只读/编辑两态切换（`_btn_edit` 已删除），改为始终可编辑的四个 SpinBox；实时显示周期预览与验证错误。
4. `gui/monitor_panel.py`：新增"各方向延误（s/辆）"列，与"排队（m）"并排显示；排队≥60m 标红规则保留。
5. `gui/main_window.py`：新增"SUMO 可视化"复选框（`_chk_sumo_gui`）连接 `adapter.connect(use_gui=...)`；修复 `_set_sim_running()` 中残留的 `_btn_edit` 引用（该按钮已删除，原代码运行时会 AttributeError）；仿真结束状态栏显示各方向延误。

**测试**
30/30 全部通过（`python test_part2.py`）。

---

## 2. 系统概览

### 功能需求
1. **手动模式**：GUI 输入各方向车流量，手动下发配时方案至 SUMO
2. **自动模式**：根据进口流量用 Webster 算法自动生成固定配时并下发
3. **仿真控制**：倍速仿真（1×~20×）、自动随机生成车流
4. **数据持久化**：配时方案、流量历史、仿真结果存入 SQLite

### 技术栈
| 层次 | 技术 |
|------|------|
| GUI | PyQt5 |
| 算法 | Python 3.10+，Webster 公式 |
| SUMO 接口 | traci（来自 SUMO 安装目录） |
| 数据库 | SQLite + SQLAlchemy 2.x |
| 并发 | Python threading（仿真循环与 GUI 解耦） |

### 交叉口设计
- **路网**：十字形单交叉口，4 个进口各 2 车道（lane 0 直行+右转，lane 1 左转）
- **信号**：4 相位（N-S 直行、N-S 左转、E-W 直行、E-W 左转）
- **默认周期**：96 s（32+16+22+10 绿灯 + 4×4s 过渡）

---

## 3. 已完成内容（第一、二部分）

### 第一部分（SUMO 路网 + 基础数据层）

```
sumo_net/
  intersection.nod.xml      节点定义（5 个节点）
  intersection.edg.xml      边定义（8 条，2 车道，50 km/h）
  intersection.con.xml      连接关系（12 个：4 方向×3 动作）
  intersection.tll.xml      参考文档（不传入 netconvert，仅记录设计）
  intersection.rou.xml      车型+12 条路由+8 条默认流量
  intersection.sumocfg      SUMO 仿真主配置
  generate_net.bat          Windows 脚本：调用 netconvert 生成 intersection.net.xml
```

**重要说明**：
- `generate_net.bat` 需在生成代码前运行一次，产生 `intersection.net.xml`
- `tll.xml` 不传入 netconvert，信号逻辑由 TraCIAdapter 在仿真启动后通过 TraCI 动态设置
- netconvert 会自动为 junction C 的 12 条连接分配 linkIndex，TraCIAdapter 需用 `traci.trafficlight.getControlledLinks()` 读取实际 linkIndex 再构造状态字符串

```
config.py                   全局配置（路径、TraCI 参数、相位常量、默认流量）
core/__init__.py
core/data_models.py         核心数据结构 + Webster 算法
gui/__init__.py
data/.gitkeep
requirements.txt
```

### 第二部分（算法引擎 + 数据管理）

```
core/timing_engine.py       TimingEngine 类（Webster 包装 + 验证 + 周期调整）
core/data_manager.py        DataManager 类（SQLAlchemy ORM，3 张表 CRUD + CSV 导出）
```

### 第三部分（TraCI 适配层 + 仿真控制线程）✅ 已完成

```
core/traci_adapter.py       TraCIAdapter 类（封装所有 TraCI 调用，延迟导入 traci）
core/sim_controller.py      SimController(QThread)（步进循环、速度控制、Qt 信号）
```

**测试覆盖**：`test_part2.py` Section 5/6 共 5 项结构测试，全部通过（无需 SUMO）。

### 第四部分（GUI 面板）✅ 已完成

```
gui/flow_panel.py           FlowPanel(QGroupBox)  — 8 个 SpinBox 流量输入 + 生成方案
gui/timing_panel.py         TimingPanel(QGroupBox) — 配时展示 + 手动编辑 + 下发信号
gui/monitor_panel.py        MonitorPanel(QGroupBox)— 实时仿真状态显示，相位颜色高亮
```

**import 验证**：`python -c "from gui.flow_panel import FlowPanel; ..."` 三个面板全部 OK。

### 第五部分（主窗口 + 入口）✅ 已完成

```
gui/main_window.py          MainWindow(QMainWindow) — 集成三面板 + 工具栏 + 信号链
main.py                     应用入口（HiDPI + Fusion + MainWindow）
```

**import 验证**：`python -c "from gui.main_window import MainWindow; import main"` OK。

---

## 4. 关键 API 速查

### `core/data_models.py`

```python
# 数据类
IntersectionParam(sat_flow=1800, num_phases=4, loss_time=3, yellow_time=3,
                  all_red=1, min_green=10, max_cycle=180, min_cycle=30)

FlowData(flows={"N_through": 620, "N_left": 180, ...})
  .compute_critical_ratios(param)  # 填充 critical_ratios 和 Y
  .validate() -> List[str]
  .to_json() / .from_json(s)

TimingPlan(cycle, green_times=[32,16,22,10], yellow=3, all_red=1, note="auto")
  .validate(min_green) -> List[str]
  .to_db_dict() / .from_db_dict(d)
  .summary() -> str

SimStatus(sim_time, phase_index, phase_remaining, queue_lengths,
          avg_delay, throughput, vehicle_count)
  .green_phase_number -> int  # 1-4，黄灯/全红返回 0
  .sim_time_str -> str        # "MM:SS"

SimResult(plan_id, sim_duration, avg_delay, avg_queue, throughput, speed_factor)

# Webster 算法
plan = webster(flow_data, param)

# 状态字符串构建（运行时调用，需先获取 link_map）
build_tl_states(link_map: Dict[str, int], total_links: int) -> Dict[str, str]
# link_map 键格式："{from_edge}_{to_edge}"，如 "n2c_c2s"
# 返回键：NS_THROUGH_GREEN / NS_THROUGH_YELLOW / NS_LEFT_GREEN /
#          NS_LEFT_YELLOW / EW_THROUGH_GREEN / EW_THROUGH_YELLOW /
#          EW_LEFT_GREEN / EW_LEFT_YELLOW / ALL_RED
```

### `core/timing_engine.py`

```python
engine = TimingEngine()                   # 使用 config 默认参数
plan   = engine.compute(flow_data)        # Webster 最优方案
plan   = engine.default_plan()            # config 默认方案（96s）
plan   = engine.plan_from_green_times(32, 16, 22, 10)  # 手动输入
plan   = engine.adjust_to_cycle(plan, 90) # 等比缩放到目标周期
errors = engine.validate(plan)            # [] = 合法
txt    = engine.plan_summary(plan)        # 一行文本摘要
txt    = engine.flow_summary(flow_data)   # Y 值和饱和度摘要
```

### `core/data_manager.py`

```python
dm = DataManager()                  # 自动建表，WAL 模式
dm.save_plan(plan)                  # INSERT OR REPLACE
dm.load_plan(plan_id) -> Optional[TimingPlan]
dm.list_plans(limit=100) -> List[TimingPlan]
dm.delete_plan(plan_id) -> bool
dm.save_flow(flow_data) -> int      # 返回 row id
dm.load_last_flow() -> Optional[FlowData]
dm.save_result(result) -> int       # 填充 result.id
dm.list_results(limit=50) -> List[SimResult]
dm.export_results_csv(path) -> int  # 返回写入行数
dm.export_plans_csv(path)   -> int
dm.close()
```

### `core/traci_adapter.py`

```python
adapter = TraCIAdapter()

# 连接 SUMO
adapter.connect(use_gui=False)    # 启动 sumo 进程并打开 TraCI 连接
adapter.connect(use_gui=True)     # 启动 sumo-gui（可视化调试）

# 状态
adapter.is_connected              # bool
adapter.link_map                  # Dict[str, int]：{"n2c_c2s": 0, ...}（连接后只读）

# 信号控制
adapter.apply_timing(plan)        # 推送 TimingPlan 至 SUMO（12 个 Phase 对象）

# 仿真步进
adapter.step()                    # traci.simulationStep()
adapter.get_status() -> SimStatus # 读取当前仿真状态快照
adapter.add_vehicles(flows, sim_time, rng) -> int  # 按泊松过程插入车辆（返回插入数）

# 关闭
adapter.disconnect()              # 关闭 TraCI，幂等（未连接时调用无副作用）
```

**注意**：`add_vehicles()` 是可选的动态补充，路由文件中的 `<flow>` 定义已提供基础需求。
`SimController(auto_vehicles=False)` 可完全依赖路由文件流量，不额外插入车辆。

### `core/sim_controller.py`

```python
# 初始化（adapter 已 connect，plan 已 apply_timing）
ctrl = SimController(
    adapter=adapter,
    data_manager=dm,
    plan=plan,
    flows=flow_data,       # None → 使用 config.DEFAULT_FLOWS
    auto_vehicles=True,    # False → 仅依赖路由文件流量
)

# 信号连接（在 QThread.start() 前完成）
ctrl.status_updated.connect(monitor_panel.update_status)  # SimStatus
ctrl.sim_finished.connect(on_sim_finished)                # SimResult
ctrl.error_occurred.connect(on_error)                     # str

# 控制
ctrl.start()                          # 启动 QThread
ctrl.set_speed(10)                    # 1-20×，线程安全
ctrl.get_current_speed() -> int
ctrl.request_timing_change(plan)      # 在下一步应用新配时方案（线程安全）
ctrl.stop()                           # 请求退出循环（异步）
ctrl.wait()                           # 阻塞等待线程结束（可选）

# sim_finished 信号携带 SimResult，已自动调用 dm.save_result()
# adapter.disconnect() 在 run() 的 finally 块中自动调用，无需手动关闭
```

### `gui/flow_panel.py`

```python
panel = FlowPanel()

# 信号
panel.flow_submitted.connect(on_flow_submitted)  # payload: FlowData

# 方法
panel.load_flow(fd: FlowData)  # 用 fd 的数值填充所有 SpinBox
panel.get_flow() -> FlowData   # 读取当前 SpinBox 值返回 FlowData
```

### `gui/timing_panel.py`

```python
panel = TimingPanel()

# 信号
panel.plan_applied.connect(on_plan_applied)  # payload: TimingPlan

# 方法
panel.display_plan(plan: TimingPlan)  # 更新面板显示（只读 + SpinBox 同步）
panel.current_plan() -> TimingPlan    # 返回面板当前持有的方案
```

**编辑模式切换**：用户点「手动编辑」按钮进入编辑模式；SpinBox 实时显示周期预览和验证错误；
点「结束编辑」将 SpinBox 值提交回 `_plan`；点「下发方案至 SUMO」发射 `plan_applied` 信号。

### `gui/monitor_panel.py`

```python
panel = MonitorPanel()

# 连接 SimController 信号（Qt 自动队列连接，线程安全）
sim_ctrl.status_updated.connect(panel.update_status)

# 方法
panel.update_status(status: SimStatus)  # 刷新所有显示值
panel.reset()                           # 恢复为空闲状态（显示 "—"）
```

**颜色规则**：
- 相位颜色：`phase_index % 3 == 0` → 绿（#1a7f1a），`== 1` → 橙（黄灯），`== 2` → 红（全红）
- 排队 ≥ 60 m 时该方向排队标签变红加粗

### `config.py` 重要常量

```python
TL_ID = "C"              # SUMO 信号灯 junction ID
TRACI_PORT = 8813
CFG_FILE                 # intersection.sumocfg 绝对路径
NET_FILE                 # intersection.net.xml 绝对路径
DB_PATH                  # data/signal_system.db 绝对路径

# 相位顺序索引（对应 TimingPlan.green_times 下标）
PHASE_IDX_NS_THROUGH = 0
PHASE_IDX_NS_LEFT    = 1
PHASE_IDX_EW_THROUGH = 2
PHASE_IDX_EW_LEFT    = 3

INCOMING_EDGES = {"N": "n2c", "S": "s2c", "E": "e2c", "W": "w2c"}
OUTGOING_EDGES = {"N": "c2n", "S": "c2s", "E": "c2e", "W": "c2w"}

DEFAULT_FLOWS = {
    "N_through": 620, "N_left": 180,
    "S_through": 580, "S_left": 160,
    "E_through": 430, "E_left": 120,
    "W_through": 460, "W_left": 140,
}
```

---

## 5. 所有部分已完成 ✅

全部五个部分均已于 2026-05-25 实现完毕，项目可端到端运行（需 SUMO 已安装）。

### 第五部分（参考）：主窗口 + 入口

#### `MainWindow`（`gui/main_window.py`）
- 继承 `QMainWindow`
- 布局：三栏水平（FlowPanel | TimingPanel | MonitorPanel）+ 底部工具栏
- 工具栏元素：
  - `▶ 启动仿真` 按钮
  - `■ 停止` 按钮
  - 速度倍率滑块（1~20）+ 数值标签
  - 「自动生成车流」复选框
  - 「导出报告」按钮
- 信号-槽连接：
  - `flow_panel.flow_submitted` → `_on_flow_submitted(flow_data)` → 调用 `engine.compute()`，更新 `timing_panel`
  - `timing_panel.plan_applied` → `_on_plan_applied(plan)` → 调用 `adapter.apply_timing()`, `dm.save_plan()`
  - `start_btn.clicked` → `_on_start()` → 创建 `SimController`，连接 `status_updated → monitor_panel.update_status`
  - `stop_btn.clicked` → `sim_controller.stop()`
  - `speed_slider.valueChanged` → `sim_controller.set_speed()`
  - `sim_controller.sim_finished` → `_on_sim_finished(result)` → `dm.save_result(result)`
- 持有：`TimingEngine`, `DataManager`, `TraCIAdapter`, `SimController`（可为 None）
- `closeEvent` 中调用 `adapter.disconnect()` 和 `dm.close()`

**重要**：`start_btn` 点击时必须按以下顺序操作：
```python
adapter.connect(use_gui=False)      # 1. 启动 SUMO
adapter.apply_timing(current_plan)  # 2. 下发初始方案
ctrl = SimController(adapter, dm, current_plan, ...)
ctrl.status_updated.connect(...)    # 3. 连接信号（start 前完成）
ctrl.sim_finished.connect(...)
ctrl.start()                        # 4. 启动 QThread
```

#### `main.py`
```python
import sys
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
```

---


### 第四部分：`gui/flow_panel.py` + `gui/timing_panel.py` + `gui/monitor_panel.py`（约 350 行）

所有面板继承 `QWidget`，通过 Qt 信号与父窗口通信。

#### `FlowPanel`（`gui/flow_panel.py`）
- 8 个 `QDoubleSpinBox`（0~9999，步长 10）：N/S/E/W × 直行/左转
- 「自动生成配时方案」按钮 → 发射 `flow_submitted = pyqtSignal(FlowData)`
- 「重置为默认值」按钮
- 支持通过 `load_flow(flow_data)` 从外部填充数值

#### `TimingPanel`（`gui/timing_panel.py`）
- 展示区：周期长度、总损失时间、4 相位绿灯（只读 QLabel）
- 编辑区：4 个 `QDoubleSpinBox` 手动修改绿灯时长（触发实时验证）
- 「下发方案至 SUMO」按钮 → 发射 `plan_applied = pyqtSignal(TimingPlan)`
- 「手动编辑」切换按钮（切换只读/可编辑模式）
- 方法 `display_plan(plan: TimingPlan)` 更新显示

#### `MonitorPanel`（`gui/monitor_panel.py`）
- 8 个 `QLabel` 显示：仿真时刻、当前相位、剩余绿灯、平均延误、N/E 进口排队、通过量、在途车辆
- 方法 `update_status(status: SimStatus)` 由主窗口的槽函数调用
- 当前相位标签按相位颜色高亮（绿/黄/红）
- 排队超阈值（60 m）时标签变红

---

### 第五部分：`gui/main_window.py` + `main.py`（约 200 行）

#### `MainWindow`（`gui/main_window.py`）
- 继承 `QMainWindow`
- 布局：三栏水平（FlowPanel | TimingPanel | MonitorPanel）+ 底部工具栏
- 工具栏元素：
  - `▶ 启动仿真` 按钮
  - `■ 停止` 按钮
  - 速度倍率滑块（1~20）+ 数值标签
  - 「自动生成车流」复选框
  - 「导出报告」按钮
- 信号-槽连接：
  - `flow_panel.flow_submitted` → `_on_flow_submitted(flow_data)` → 调用 `engine.compute()`，更新 `timing_panel`
  - `timing_panel.plan_applied` → `_on_plan_applied(plan)` → 调用 `adapter.apply_timing()`, `dm.save_plan()`
  - `start_btn.clicked` → `_on_start()` → 创建 `SimController`，连接 `status_updated → monitor_panel.update_status`
  - `stop_btn.clicked` → `sim_controller.stop()`
  - `speed_slider.valueChanged` → `sim_controller.set_speed()`
  - `sim_controller.sim_finished` → `_on_sim_finished(result)` → `dm.save_result(result)`
- 持有：`TimingEngine`, `DataManager`, `TraCIAdapter`, `SimController`（可为 None）
- `closeEvent` 中调用 `adapter.disconnect()` 和 `dm.close()`

#### `main.py`
```python
import sys
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
```

---

## 6. 目录结构（完成后）

```
D:\Moriarty\SUMOsoftware\
├── main.py                         ← 已完成
├── config.py                       ← 已完成
├── requirements.txt                ← 已完成
├── HANDOFF.md                      ← 本文档
├── sumo_net\
│   ├── intersection.nod.xml        ← 已完成
│   ├── intersection.edg.xml        ← 已完成
│   ├── intersection.con.xml        ← 已完成
│   ├── intersection.tll.xml        ← 已完成（参考文档）
│   ├── intersection.rou.xml        ← 已完成
│   ├── intersection.sumocfg        ← 已完成
│   ├── generate_net.bat            ← 已完成
│   └── intersection.net.xml        ← 运行 generate_net.bat 后生成
├── core\
│   ├── __init__.py                 ← 已完成
│   ├── data_models.py              ← 已完成
│   ├── timing_engine.py            ← 已完成
│   ├── data_manager.py             ← 已完成
│   ├── traci_adapter.py            ← 已完成
│   └── sim_controller.py           ← 已完成
├── gui\
│   ├── __init__.py                 ← 已完成
│   ├── flow_panel.py               ← 已完成
│   ├── timing_panel.py             ← 已完成
│   ├── monitor_panel.py            ← 已完成
│   └── main_window.py              ← 已完成
└── data\
    ├── .gitkeep                    ← 已完成
    └── signal_system.db            ← 运行后自动创建
```

---

## 7. 启动流程（完成后）

```
1. 确认 SUMO 已安装，SUMO_HOME 环境变量已设置
2. pip install -r requirements.txt
3. 运行 sumo_net\generate_net.bat  →  生成 intersection.net.xml
4. python main.py                  →  启动 GUI
5. GUI 中：输入流量 → 自动生成方案 → 下发 → 启动仿真
```

---

## 8. 下一个 Agent 的行动指南

### 当前进度
- ✅ 第一部分：SUMO 路网 + 基础数据层
- ✅ 第二部分：算法引擎 + 数据管理
- ✅ 第三部分：TraCI 适配层 + 仿真控制线程
- ✅ 第四部分：GUI 面板（`gui/flow_panel.py`、`gui/timing_panel.py`、`gui/monitor_panel.py`）
- ✅ 第五部分：主窗口 + 入口（`gui/main_window.py`、`main.py`）

### 项目已完整，启动方式

```
1. 确认 SUMO 已安装，SUMO_HOME 环境变量已设置（或 config.py 能自动检测）
2. pip install -r requirements.txt
3. 运行 sumo_net\generate_net.bat       → 生成 intersection.net.xml
4. python main.py                       → 启动 GUI
5. GUI 操作流程：
   a. 在「流量输入」面板填写各方向流量
   b. 点「自动生成配时方案」→「配时方案」面板自动填充 Webster 结果
   c. 可手动编辑绿灯时长（点「手动编辑」）
   d. 点「▶ 启动仿真」→ SUMO 自动启动，「实时监控」面板开始刷新
   e. 仿真运行中可点「下发方案至 SUMO」修改配时
   f. 点「■ 停止」或等待仿真自然结束，结果自动存入 SQLite
   g. 点「导出结果」或菜单「文件→导出仿真结果 CSV」保存报告
```

### 如需继续维护/扩展

- 所有常量（端口、速度范围、排队阈值等）在 `config.py` 修改
- 排队警告阈值在 `gui/monitor_panel.py` 的 `_QUEUE_WARN_M = 60.0`
- 要支持 sumo-gui（可视化调试），将 `main_window.py` 的 `use_gui_flag = False` 改为 `True`
- 每次扩展后在第 0 节末尾追加日志（格式见日志区块）
