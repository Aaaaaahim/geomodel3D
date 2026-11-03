"""统计图表生成（M7-F5）。

使用 matplotlib（Agg 后端）生成厚度、品位、断层、质量与体积图表，
可导出 PNG/SVG。headless 可测。
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from geomodel3d.config.errors import ErrorCode, GeoModelError


def configure_matplotlib():
    """初始化 matplotlib 并配置中文字体，返回 pyplot 模块。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception as exc:  # noqa: BLE001
        raise GeoModelError(ErrorCode.EXP_CHK_001, "图表导出需要 matplotlib。") from exc
    _configure_cjk_font(plt, font_manager)
    return plt


def _configure_cjk_font(plt, font_manager) -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for candidate in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "PingFang SC"):
        if candidate in available:
            plt.rcParams["font.sans-serif"] = [candidate]
            break
    plt.rcParams["axes.unicode_minus"] = False


def thickness_chart(stats: Dict[str, Dict], path: Path) -> Path:
    plt = configure_matplotlib()
    layers = list(stats)
    figure, axes = plt.subplots(1, max(len(layers), 1), figsize=(4 * max(len(layers), 1), 3.5), squeeze=False)
    for axis, code in zip(axes[0], layers):
        hist = stats[code]["histogram"]
        axis.bar(hist["centers"], hist["counts"], width=_bin_width(hist["centers"]), color="#2F6FB5")
        summary = stats[code]["summary"]
        axis.set_title(f"{code} 厚度 (均值 {summary['mean']:.1f} m)")
        axis.set_xlabel("厚度 (m)")
        axis.set_ylabel("频数")
    figure.tight_layout()
    return _save(figure, plt, path)


def grade_chart(stats: Dict, path: Path) -> Path:
    plt = configure_matplotlib()
    figure, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    hist = stats["histogram"]
    left.bar(hist["centers"], hist["counts"], width=_bin_width(hist["centers"]), color="#2F9E63")
    left.set_title(f"品位分布 ({stats['unit']})")
    left.set_xlabel("品位")
    left.set_ylabel("频数")

    cumulative = stats["cumulative"]
    right.plot(cumulative["values"], cumulative["percent"], color="#E8913A")
    right.set_title("品位累积曲线")
    right.set_xlabel("品位")
    right.set_ylabel("累积百分比 (%)")
    right.grid(True, ls=":", alpha=0.4)
    figure.tight_layout()
    return _save(figure, plt, path)


def fault_chart(stats: Dict, path: Path) -> Path:
    plt = configure_matplotlib()
    figure, (left, right) = plt.subplots(1, 2, figsize=(10, 4), subplot_kw={"projection": None})
    rose = stats["rose"]
    left.bar(rose["centers"], rose["counts"], width=8.0, color="#E8913A")
    left.set_title("断层走向玫瑰图 (°)")
    left.set_xlabel("走向")
    left.set_ylabel("条数")

    right.bar(["平均倾角", "平均落差"], [stats["dip"]["mean"], stats["throw"]["mean"]], color="#6B7A8D")
    right.set_title("产状概览")
    figure.tight_layout()
    return _save(figure, plt, path)


def quality_chart(stats: Dict, path: Path) -> Path:
    plt = configure_matplotlib()
    by_code = stats["by_code"]
    figure, axis = plt.subplots(figsize=(7, 4))
    if by_code:
        codes = list(by_code)
        axis.bar(codes, [by_code[c] for c in codes], color="#D64550")
    axis.set_title("数据质量：问题分布")
    axis.set_ylabel("数量")
    figure.tight_layout()
    return _save(figure, plt, path)


def layer_volume_chart(volumes: Dict[str, float], path: Path) -> Path:
    plt = configure_matplotlib()
    figure, axis = plt.subplots(figsize=(7, 4))
    codes = list(volumes)
    axis.bar(codes, [volumes[c] for c in codes], color="#7FA8C9")
    axis.set_title("地层体积")
    axis.set_ylabel("体积 (m³)")
    figure.tight_layout()
    return _save(figure, plt, path)


def _bin_width(centers: List[float]) -> float:
    if len(centers) < 2:
        return 1.0
    return abs(centers[1] - centers[0]) * 0.9


def _save(figure, plt, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        figure.savefig(path, dpi=150)
    finally:
        plt.close(figure)
    return path


__all__ = [
    "configure_matplotlib",
    "thickness_chart",
    "grade_chart",
    "fault_chart",
    "quality_chart",
    "layer_volume_chart",
]
