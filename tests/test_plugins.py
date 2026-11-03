"""引擎插件发现测试（M10-F6）。"""
from __future__ import annotations

import geomodel3d.engine.registry as registry_module
from geomodel3d.engine.base import ENGINE_API_VERSION, EngineCapabilities, ModelingEngine
from geomodel3d.engine.registry import EngineRegistry


class _DummyEngine(ModelingEngine):
    name = "dummy"
    capabilities = EngineCapabilities(name="dummy")

    def build(self, spec, ctx):
        raise NotImplementedError


class _DummyPlugin:
    name = "dummy"

    def load(self):
        return _DummyEngine


class _BrokenPlugin:
    name = "broken"

    def load(self):
        raise RuntimeError("synthetic plugin failure")


def test_discover_registers_plugin(monkeypatch):
    monkeypatch.setattr(registry_module, "_engine_entry_points", lambda: [_DummyPlugin()])
    registry = EngineRegistry(discover=False)
    assert "dummy" not in registry.names()

    assert registry.discover_plugins() == ["dummy"]
    assert registry.get("dummy").name == "dummy"


def test_registry_discovers_plugin_on_init(monkeypatch):
    monkeypatch.setattr(registry_module, "_engine_entry_points", lambda: [_DummyPlugin()])
    registry = EngineRegistry()
    assert "dummy" in registry.names()


def test_broken_plugin_does_not_block_others(monkeypatch):
    monkeypatch.setattr(
        registry_module,
        "_engine_entry_points",
        lambda: [_BrokenPlugin(), _DummyPlugin()],
    )
    registry = EngineRegistry(discover=False)
    assert registry.discover_plugins() == ["dummy"]
    assert "dummy" in registry.names()


def test_plugin_api_version_mismatch_rejected(monkeypatch):
    class _FutureEngine(_DummyEngine):
        name = "future"
        api_version = ENGINE_API_VERSION + 1

    class _FuturePlugin:
        name = "future"

        def load(self):
            return _FutureEngine

    monkeypatch.setattr(
        registry_module, "_engine_entry_points", lambda: [_FuturePlugin(), _DummyPlugin()]
    )
    registry = EngineRegistry(discover=False)
    assert registry.discover_plugins() == ["dummy"]
    assert "future" not in registry.names()


def test_plugin_min_app_version_rejected(monkeypatch):
    class _TooNewEngine(_DummyEngine):
        name = "toonew"
        min_app_version = "999.0.0"

    class _TooNewPlugin:
        name = "toonew"

        def load(self):
            return _TooNewEngine

    monkeypatch.setattr(
        registry_module, "_engine_entry_points", lambda: [_TooNewPlugin(), _DummyPlugin()]
    )
    registry = EngineRegistry(discover=False)
    assert registry.discover_plugins() == ["dummy"]
    assert "toonew" not in registry.names()


def test_plugin_compatible_version_accepted(monkeypatch):
    class _CompatibleEngine(_DummyEngine):
        name = "compatible"
        api_version = ENGINE_API_VERSION
        min_app_version = "0.1"

    class _CompatiblePlugin:
        name = "compatible"

        def load(self):
            return _CompatibleEngine

    monkeypatch.setattr(
        registry_module, "_engine_entry_points", lambda: [_CompatiblePlugin()]
    )
    registry = EngineRegistry(discover=False)
    assert registry.discover_plugins() == ["compatible"]


def test_plugin_factory_function(monkeypatch):
    class _FactoryPlugin:
        name = "factory"

        def load(self):
            return _DummyEngine

    monkeypatch.setattr(
        registry_module, "_engine_entry_points", lambda: [_FactoryPlugin()]
    )
    registry = EngineRegistry(discover=False)
    assert registry.discover_plugins() == ["dummy"]
