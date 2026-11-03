# GeoModel3D · 三维地质建模系统

基于 **Python + PyQt6 + PyVista + GemPy + OpenGeode** 的桌面端三维地质建模软件。
本仓库是《基于 Python + GemPy + OpenGeode 的三维地质建模软件详细设计方案（完善版）》的落地实现。

> 当前进度：**M0~M8 全链路贯通**。已完成可运行的端到端主链路：
> `导入钻孔/断层 → 校验 → 建模 → 三维显示（图层显隐） → 断层参数编辑（真实局部重建模） → 储量 → 剖面 → 统计 → 导出 → 版本快照/自动保存/崩溃恢复 → 工程保存/加载`。
> 引擎：OpenGeode 已接入并验证；GemPy 3.x 通过进程内/外部桥接双路径验证通过。

## 1. 环境要求

- Python 3.9+（设计基线 3.10+，当前环境 3.9 亦可运行）
- Windows 10/11 x64
- 已安装：PyQt6、pyvista、pyvistaqt、numpy、scipy、pandas、pydantic、matplotlib、pyproj、fiona、ezdxf、openpyxl
- 已接入引擎：
  - `opengeode-core==16.6.0`（Python 3.9 可用，已验证建模/拓扑/原生 IO）
  - `gempy 2026.0.3` + `gempy_engine`（numpy 后端，无 torch）运行于隔离环境 `geomodel3d-gempy3`（Python 3.11），主程序通过桥接调用

```powershell
pip install -e .
# 可选：真实引擎
pip install -e ".[geo]"
# 开发依赖
pip install -e ".[dev]"

# GemPy 3.x 隔离环境（主程序为 Python 3.9 时）
powershell -ExecutionPolicy Bypass -File packaging\install_gempy_env.ps1
$env:GEOMODEL3D_GEMPY_PYTHON = "C:\Miniconda_py\envs\geomodel3d-gempy3\python.exe"
```

> **引擎可用性说明**
> - OpenGeode：主环境已安装，`OpenGeodeEngine` 可构建 `HybridSolid3D` 实体、写入 layer 属性、计算邻接并导出原生 `.og_hso3d`。
> - GemPy：采用 GemPy 3.x（`gempy_engine` numpy 后端，**不依赖 torch**）。主程序运行于 Python 3.9，而 GemPy 3.x 需 Python ≥3.10，因此 `GemPyEngine` 支持两种接入：
>   1. **进程内**：解释器可直接 `import gempy` 时直接计算；
>   2. **外部桥接**：设置 `GEOMODEL3D_GEMPY_PYTHON` 指向含 GemPy 3.x 的解释器（或自动发现 conda 环境 `geomodel3d-gempy3`），子进程执行 `_gempy_bridge.py` 完成真实插值并回传 `lith_block`。
>   两种路径均已完成端到端验证（钻孔界面点 → 产状拟合 → `compute_model` → UMesh）。
> - **拓扑布尔切割 / DSI 为闭源能力**：已在隔离环境验证 `opengeode-core 18.x + geode-common` 仅暴露 `GeodeCommonCutterSolidLibrary` 等**注册钩子**（无 `cut`/`boolean`/DSI 函数），`geode-simplex` 仅提供重网格约束。因此断层切割仍以落差位移近似实现；如需真切割需采购 Geode 商业模块。
> - OpenGeode 原生库需在 PyVista/VTK 之前加载，`geomodel3d/__init__.py` 已做预加载以规避 DLL 冲突（可用 `GEOMODEL3D_SKIP_OG_PRELOAD` 关闭）。


## 2. 运行

> ⚠️ **必须用主环境运行 GUI**（`C:\Miniconda_py`，含 PyQt6）。
> `geomodel3d-gempy3` 环境只装 GemPy，**不要**用它启动界面。

```powershell
cd D:\geomodel3D
# 主环境解释器（推荐，显式）
C:\Miniconda_py\python.exe -m geomodel3d
# 或等价写法
python -m geomodel3d.app.main
# 或安装后使用命令
geomodel3d
# 或打包版
.\dist\GeoModel3D\GeoModel3D.exe

# headless 批处理（M10）
geomodel3d-cli run --job examples\demo\job.json --out out
```

无 OpenGL 的环境（CI/服务器）可设置：

```powershell
$env:QT_QPA_PLATFORM="offscreen"; $env:GEOMODEL3D_NO_VTK="1"
```

## 3. 目录结构（对应设计 9.1）

```
geomodel3d/
├─ app/         入口、依赖装配（AppContext）、事件总线、任务、主题、日志
├─ ui/          PyQt6 界面（主窗口/控件/对话框/向导）
├─ domain/      领域实体、业务规则（纯 Python，无 Qt）
├─ services/    业务用例服务（Project/Data/Modeling/FaultEditor）
├─ engine/      UMesh 统一模型 + 引擎适配器（base/gempy/opengeode/mock/registry）
├─ algorithms/  插值、几何（headless 可测）
├─ dataio/      钻孔解析、工程包 .gm3d、CRS 服务
├─ jobs/        headless CLI 运行器
├─ config/      schema、默认值、错误码、浅/深色主题 QSS
└─ tests/       pytest 单元/集成/UI 冒烟
```

## 4. 架构要点

- **五层分层**：UI → 业务服务 → 引擎调度 → 算法 → 数据存取；事件总线解耦，UI 不直接引用业务对象。
- **引擎中立**：`ModelingEngine` 适配器接口 + `UMesh` 统一表示；GemPy/OpenGeode 未安装时给出明确错误码与建议，Mock 引擎提供可运行兜底。
- **调度决策**：`engine/registry.py:decide_engine` 按设计 4.3.2 规则自动选引擎（复杂断层/高精度/正式成果 → OpenGeode，快速预览 → GemPy）。
- **异步不卡界面**：`app/tasks.py` 基于 QThreadPool，支持进度、取消、异常上报；无任务管理器时同步执行，便于 headless 测试。
- **局部重建模**：`algorithms/geometry.py:fault_impact_region` 按 BR-03 计算影响域，`FaultEditorService` 只对影响域发起重建模并入 Undo 栈。
- **工程包**：`*.gm3d` 为 Zip，manifest 记录每个文件 SHA-256，原子写入，打开时校验完整性。
- **坐标系自动识别**：导入钻孔时按坐标量级/范围/带号前缀推断 EPSG（经纬度→4490/4326；含带号前缀的高斯投影→CGCS2000 3°/6°带；无带号的投影坐标会提示需人工指定），并在导入向导中预选、工程缺省时自动补充（`dataio/crs_detect.py`）。

## 5. 测试

```powershell
python -m pytest -q
```

覆盖：配置 schema、业务规则（BR-02）、插值（IDW/克里金/协同克里金 KED+平滑）、断层影响域、UMesh 体积/序列化、引擎调度与 Mock/GemPy/OpenGeode 建模、钻孔与地震解析（Esri ASCII/SEG-Y 多卷合并）、工程包完整性、储量（体积法/块段法/分级）、剖面（求交/展开/钻孔投影/断层迹线/标注/图廓）、导出（VTU/OBJ/SHP/DXF/GeoJSON/SVG/PNG）、网格体检与修复、局部重建模、空间索引兜底（无 scipy）、版本快照/自动保存、统计分析、数据回归基准、性能预算、服务层端到端、UI 冒烟（拾取/框选/剖切/offscreen）。

## 6. 性能基准

核心计算已向量化（UMesh 体积/中心、储量距离与块段聚合、剖面候选筛选、网格体检面统计 void 去重）。
可运行基准脚本：

```powershell
geomodel3d-bench --resolution 100 100 20      # 20 万单元
geomodel3d-bench --resolution 100 100 100     # 100 万单元
```

- 20 万单元（本机）：建模 0.09s · 体积 0.45s · 体检 2.5s · 剖面 2.6s · 储量 0.7s · 合计 ~6.4s。
- 100 万单元（本机）：建模 0.5s · 体积 3.2s · 体检 8.9s · 剖面 2.8s · 储量 3.8s · 合计 ~20s（储量目标 <30s 达标）。


## 7. 打包发布

```powershell
pyinstaller --clean --noconfirm --distpath dist --workpath build packaging\geomodel3d.spec

# 冻结产物自检：装配依赖 + 建模 + 储量 + 剖面 + CRS 变换 + SHP/XLSX/VTU 导出，返回码 0 表示通过
$env:GEOMODEL3D_SELF_TEST="1"; $env:QT_QPA_PLATFORM="offscreen"; $env:GEOMODEL3D_NO_VTK="1"
.\dist\GeoModel3D\GeoModel3D.exe; echo $LASTEXITCODE
```

已在本机验证：单目录发布、自检返回码 0（**约 404MB**，含 VTK/Qt/GDAL）。

打包注意：
- **不可排除 `setuptools`**：`pkg_resources` 运行时钩子依赖其内置 `jaraco`（`setuptools._vendor.jaraco`），已显式收集，否则启动报 `No module named 'jaraco'`。
- **冻结版不内置 OpenGeode**：其原生库在冻结环境会触发访问冲突，打包版已排除并跳过预加载，引擎自动回退 Mock（GemPy 仍可经外部环境桥接）。
- **排除 `vtk` 元模块**：PyVista 仅在 `except ImportError` 兜底分支引用它，正常运行用 `vtkmodules`；排除后仅保留实际用到的 71 个 VTK 模块（已静态校验无缺失）。
- **排除 `scipy`**：仅用于 `cKDTree` 加速，已提供 NumPy 兜底（`algorithms/spatial.py`：分块暴力最近邻 + 均匀网格哈希），冻结版使用兜底。
- 体积演进：607 → 572 → 506 → **404MB**（剔 opengl32sw、requests/pooch/lxml/PIL-avif、`vtk` 元模块、`scipy`）。

## 8. 持续集成

```powershell
# 本地冒烟：compileall -> pytest -> 基准 -> 冻结产物自检
powershell -ExecutionPolicy Bypass -File scripts\ci_smoke.ps1
```

`.github/workflows/ci.yml` 提供 Windows + Python 3.9/3.10/3.11 矩阵，运行编译与测试。


## 9. 里程碑对照

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 骨架 | 工程框架、主窗口、主题、工程文件、日志 | ✅ |
| M1 导入预览 | 钻孔/断层/地震导入、质量报告 | ✅ 钻孔/断层/点云地震 |
| M2 快速建模 | GemPy 适配、快速预览 | ✅ GemPy 3.x（numpy 引擎）进程内/桥接双路径，端到端验证通过 |
| M3 高精建模 | OpenGeode 适配、断层切割、封闭实体 | ✅ OpenGeode 已安装并验证：HybridSolid3D + layer 属性 + 邻接 + 原生 IO；拓扑布尔切割待扩展 |
| M4 可视化与断层编辑 | 三维可视化 + 局部重建模闭环 | ✅ |
| M5 储量与剖面 | 储量计算、剖面生成、矢量导出 | ✅ |
| M6 修复与质检 | 体检、顶点焊接、退化/重复剔除、光顺 | ✅ |
| M7 统计与批量 | 统计图表/报表、参数迭代、headless CLI | ✅ |
| M8 打包发布 | 性能、打包、文档、数据回归 | ✅ 单目录 404MB，自检（建模/储量/剖面/导出）通过 |
| M9 工程管理 | 新建/保存/打开、版本快照、自动保存、崩溃恢复 | ✅ 快照时间轴/对比/回滚 + 自动保存 + 恢复提示 |

## 10. 后续开发建议

1. 拓扑布尔切割 / DSI 属 Geode 商业模块（开源栈仅注册钩子，已验证）；如采购则接入替换落差位移近似。
2. 冻结版进一步瘦身（GDAL 驱动裁剪、Qt 插件裁剪）与干净虚拟机发布冒烟。
3. 剖面坐标网格与更完整图廓（分幅、图签）。
4. 地震协变量各向异性参数 UI 化与 SEG-Y 更多卷索引格式。

## 11. 许可证 (License)

本项目遵循 **MIT License** 开源许可协议。
详见 [LICENSE](LICENSE) 文件。
