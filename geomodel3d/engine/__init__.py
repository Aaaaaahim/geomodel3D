"""引擎层：统一模型 UMesh 与引擎适配器。"""
from geomodel3d.engine.base import (
    ENGINE_API_VERSION,
    EngineCapabilities,
    ModelingEngine,
    ModelingSpec,
)
from geomodel3d.engine.gempy_adapter import GemPyEngine, gempy_available
from geomodel3d.engine.mock import MockEngine
from geomodel3d.engine.opengeode_engine import OpenGeodeEngine, opengeode_available
from geomodel3d.engine.registry import EngineContext, EngineRegistry, decide_engine
from geomodel3d.engine.umesh import UMesh

__all__ = [
    "UMesh",
    "ModelingEngine",
    "ModelingSpec",
    "EngineCapabilities",
    "ENGINE_API_VERSION",
    "MockEngine",
    "GemPyEngine",
    "OpenGeodeEngine",
    "gempy_available",
    "opengeode_available",
    "EngineContext",
    "EngineRegistry",
    "decide_engine",
]
