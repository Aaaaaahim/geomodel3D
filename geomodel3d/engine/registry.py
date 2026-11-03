"""引擎注册表、插件发现与调度决策（4.3.2 / M10-F6）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from geomodel3d import __version__
from geomodel3d.domain.entities import EngineKind
from geomodel3d.engine.base import ENGINE_API_VERSION, ModelingEngine, ModelingSpec
from geomodel3d.engine.gempy_adapter import GemPyEngine
from geomodel3d.engine.mock import MockEngine
from geomodel3d.engine.opengeode_engine import OpenGeodeEngine

logger = logging.getLogger(__name__)

PLUGIN_GROUP = "geomodel3d.engines"


def _engine_entry_points():
    """返回已安装的引擎插件入口，兼容 Python 3.9/3.10+ 的 metadata API。"""
    from importlib import metadata

    entry_points = metadata.entry_points()
    if hasattr(entry_points, "select"):
        return list(entry_points.select(group=PLUGIN_GROUP))
    return list(entry_points.get(PLUGIN_GROUP, []))


def _version_tuple(value: str):
    parts = []
    for part in str(value).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts) or (0,)


def _validate_plugin(engine: ModelingEngine, name: str) -> None:
    """拒绝 API 主版本不匹配或要求更高应用版本的插件。"""
    api_version = int(getattr(engine, "api_version", ENGINE_API_VERSION) or 0)
    if api_version != ENGINE_API_VERSION:
        raise TypeError(
            f"插件 {name} 的引擎 API 版本 {api_version} 与当前 {ENGINE_API_VERSION} 不兼容"
        )
    required = getattr(engine, "min_app_version", "") or getattr(
        engine, "requires_app_version", ""
    )
    if required and _version_tuple(required) > _version_tuple(__version__):
        raise TypeError(
            f"插件 {name} 要求应用版本 >= {required}，当前为 {__version__}"
        )


@dataclass
class EngineContext:
    """引擎调度上下文（4.3.2）。"""

    manual_engine: Optional[str] = None
    has_faults: bool = False
    need_high_precision: bool = False
    target: str = "preview"
    purpose: str = "quick_preview"


def decide_engine(ctx: EngineContext) -> EngineKind:
    """按需求 4.3.2 的决策规则选择引擎。"""
    if ctx.manual_engine:
        try:
            return EngineKind(ctx.manual_engine)
        except ValueError:
            pass
    if ctx.has_faults or ctx.need_high_precision or ctx.target in ("reserve", "section", "export"):
        return EngineKind.OPENGEODE
    if ctx.purpose in ("quick_preview", "trend", "teaching"):
        return EngineKind.GEMPY
    return EngineKind.OPENGEODE


class EngineRegistry:
    """引擎适配器注册表，支持插件式扩展（M10-F6）。"""

    def __init__(self, *, discover: bool = True) -> None:
        self._engines: Dict[str, ModelingEngine] = {}
        self.register(MockEngine())
        self.register(GemPyEngine())
        self.register(OpenGeodeEngine())
        if discover:
            self.discover_plugins()

    def register(self, engine: ModelingEngine) -> None:
        if engine.name in self._engines:
            logger.warning("引擎插件覆盖已注册引擎: %s", engine.name)
        self._engines[engine.name] = engine

    def discover_plugins(self) -> List[str]:
        """加载 ``geomodel3d.engines`` 入口点并注册插件引擎。

        插件可以是 ``ModelingEngine`` 实例、子类或返回实例的工厂函数。
        单个插件加载失败只记录日志，不影响内置引擎与其它插件。
        """
        registered: List[str] = []
        try:
            entries = _engine_entry_points()
        except Exception:  # noqa: BLE001 - 枚举失败不应阻断内置引擎
            logger.exception("枚举引擎插件失败")
            return registered
        for entry in entries:
            name = getattr(entry, "name", "?")
            try:
                loaded = entry.load()
                if isinstance(loaded, type):
                    engine = loaded()
                elif isinstance(loaded, ModelingEngine):
                    engine = loaded
                elif callable(loaded):
                    engine = loaded()
                else:
                    raise TypeError(f"插件 {name} 不是可加载的引擎类型")
                if not isinstance(engine, ModelingEngine):
                    raise TypeError(f"插件 {name} 未返回 ModelingEngine 实例")
                _validate_plugin(engine, name)
                self.register(engine)
                registered.append(engine.name)
                logger.info("已加载引擎插件: %s (%s)", name, engine.name)
            except Exception:  # noqa: BLE001 - 插件故障隔离
                logger.exception("加载引擎插件失败: %s", name)
        return registered

    def get(self, name: str) -> ModelingEngine:
        if name not in self._engines:
            raise KeyError(f"未注册的引擎: {name}")
        return self._engines[name]

    def names(self) -> List[str]:
        return list(self._engines)

    def available(self) -> List[str]:
        return [name for name, engine in self._engines.items() if engine.is_available()]

    def describe_all(self) -> List[dict]:
        return [engine.describe() for engine in self._engines.values()]

    def resolve(self, spec: ModelingSpec) -> ModelingEngine:
        """依据规格中的引擎策略与上下文解析出可用引擎。"""
        policy = spec.engine
        if policy in ("auto", "", None):
            decision = decide_engine(
                EngineContext(
                    has_faults=bool(spec.faults),
                    need_high_precision=spec.need_high_precision,
                    purpose=spec.purpose,
                    target=spec.extra.get("target", "preview"),
                )
            ).value
        else:
            decision = policy
        engine = self._engines.get(decision)
        if engine is not None and engine.is_available():
            return engine
        # 回退到 Mock：保证快速预览/无引擎场景始终可用
        return self._engines["mock"]


__all__ = ["EngineContext", "decide_engine", "EngineRegistry", "PLUGIN_GROUP"]
