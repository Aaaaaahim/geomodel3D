"""帮助对话框（FR-28）：快速开始、工作流、快捷键与故障排查。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

HELP_HTML = """
<h2>GeoModel3D 快速开始</h2>
<ol>
  <li><b>新建工程</b>（Ctrl+N），在工程设置中确认 CRS 与建模范围。</li>
  <li><b>导入数据</b>：钻孔 CSV/Excel、断层 SHP/JSON、地震 ASC/SEG-Y/点云。</li>
  <li><b>建模</b>：点击“快速建模”预览；需要高精度时使用“高精度建模”。</li>
  <li><b>查看与编辑</b>：在三维视图切换图层显隐、显示模式、剖切与拾取；右侧面板可编辑断层并局部重建模。</li>
  <li><b>计算与导出</b>：储量、剖面、统计分析；导出 VTU/OBJ/STL/PLY/GLB 或二维成果。</li>
</ol>

<h2>数据要求</h2>
<ul>
  <li>钻孔必填：孔号、X、Y、孔口高程、地层代码，以及（层顶深+层底深）或（层顶标高+层底标高）。</li>
  <li>列名支持中英文自动识别；也可在导入向导中手工映射。</li>
  <li>导入时会自动识别 EPSG；无法判定时请在导入向导或工程设置中手动指定。</li>
  <li>钻孔坐标与工程包围盒严重不符时，系统会按钻孔范围自动重设包围盒并提示。</li>
</ul>

<h2>建模引擎</h2>
<ul>
  <li><b>Mock</b>：纯 NumPy 分层建模，用于无引擎环境、演示与测试。</li>
  <li><b>GemPy</b>：快速预览/趋势建模；支持进程内或外部解释器桥接。</li>
  <li><b>OpenGeode</b>：高精度实体与原生网格 IO；当前断层采用落差位移近似，拓扑布尔切割需商业扩展。</li>
  <li>引擎不可用时自动回退 Mock；可在日志面板查看回退原因。</li>
</ul>

<h2>断层编辑与局部重建模</h2>
<ul>
  <li>在右侧“断层参数”中选择断层，调整延伸长度、影响深度、倾角、走向、垂直/水平落差与缓冲。</li>
  <li>“仅预览影响域”不重建模；“应用并重建模”只重算影响域及过渡带，域外单元保持不变。</li>
  <li>Ctrl+Z / Ctrl+Y 可撤销/重做断层参数修改，并自动同步局部模型。</li>
  <li>“几何切割预览”使用 VTK 沿断层面裁切并平移一盘，两盘保持封闭；OpenGeode 原生布尔切割仍需商业扩展。</li>
</ul>

<h2>工程、自动保存与版本快照</h2>
<ul>
  <li>工程包 <code>.gm3d</code> 为 Zip，包含 manifest SHA-256 完整性校验，保存采用原子替换。</li>
  <li>自动保存生成 <code>.autosave</code>；启动或打开工程时可从较新的自动保存恢复。</li>
  <li>Ctrl+Shift+N 创建版本快照；“编辑 → 版本历史”可查看、对比与回滚。</li>
</ul>

<h2>导出格式</h2>
<ul>
  <li>三维：VTU、OBJ、STL、PLY、GLB、OpenGeode <code>.og_hso3d</code>。</li>
  <li>剖面：GeoJSON、SHP、DXF、SVG、PNG；矢量格式要求工程已配置 CRS。</li>
  <li>报表：储量 XLSX、统计图表 PNG、统计报表 XLSX。</li>
</ul>

<h2>工程加密与崩溃转储</h2>
<ul>
  <li>“文件 → 设置/清除工程密码”可对保存的 <code>.gm3d</code> 使用 AES-256-GCM 加密；留空保存即恢复为普通工程包。</li>
  <li>加密功能依赖 <code>cryptography</code>：<code>pip install "geomodel3d[crypto]"</code>。</li>
  <li>未处理异常会尝试写入 Windows minidump；致命信号栈写入日志目录的 <code>faulthandler.log</code>。</li>
</ul>

<h2>常用快捷键</h2>
<table border="1" cellpadding="4" cellspacing="0">
  <tr><th>快捷键</th><th>功能</th></tr>
  <tr><td>Ctrl+N / Ctrl+O / Ctrl+S</td><td>新建 / 打开 / 保存工程</td></tr>
  <tr><td>Ctrl+Shift+S</td><td>另存为</td></tr>
  <tr><td>Ctrl+Z / Ctrl+Y</td><td>撤销 / 重做</td></tr>
  <tr><td>Ctrl+Shift+N</td><td>创建版本快照</td></tr>
  <tr><td>R</td><td>复位视图</td></tr>
  <tr><td>Ctrl+P</td><td>拾取查询</td></tr>
  <tr><td>F</td><td>框选查询</td></tr>
  <tr><td>F1</td><td>打开帮助</td></tr>
</table>

<h2>故障排查</h2>
<ul>
  <li>操作失败时优先查看底部“日志”面板；日志同时写入用户数据目录的 <code>logs/modeling.log</code>（JSONL）。</li>
  <li>“引擎不可用”：安装可选依赖，或设置 <code>GEOMODEL3D_GEMPY_PYTHON</code> 指向含 GemPy 的解释器。</li>
  <li>“缺少 CRS”：在工程设置或导入向导中指定 EPSG 后再导出矢量成果。</li>
  <li>模型与钻孔无关：检查工程包围盒是否覆盖孔口与孔底高程，或重新导入让系统自动重设。</li>
</ul>

<h2>引擎插件</h2>
<p>第三方引擎可通过 Python entry point 注册，组名为
<code>geomodel3d.engines</code>。入口点应返回 <code>ModelingEngine</code>
实例、子类或工厂函数；加载失败不会影响内置引擎。</p>
"""


class HelpDialog(QDialog):
    """应用内帮助文档。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("帮助")
        self.resize(860, 640)
        layout = QVBoxLayout(self)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setHtml(HELP_HTML)
        layout.addWidget(self._browser)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)


__all__ = ["HelpDialog", "HELP_HTML"]
