"""工程包（*.gm3d）读写测试（3.5）。"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

from geomodel3d.config.errors import ProjectFormatError
from geomodel3d.dataio.project_file import (
    load_project,
    read_manifest,
    save_project,
    verify_integrity,
)
from geomodel3d.engine import EngineRegistry


def test_save_load_roundtrip(project, modeling_spec, ctx, tmp_path):
    project.mesh = EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)
    path = save_project(project, tmp_path / "demo.gm3d")
    assert path.exists()

    restored = load_project(path)
    assert restored.name == "demo"
    assert len(restored.drillholes) == len(project.drillholes)
    assert restored.has_model()
    assert restored.mesh.total_volume() == pytest.approx(project.mesh.total_volume())
    # face_groups / meta / version 必须随工程持久化，否则储量/剖面/配色会失效
    assert restored.mesh.face_groups == project.mesh.face_groups
    assert restored.mesh.meta.get("layer_codes") == project.mesh.meta.get("layer_codes")
    assert restored.mesh.version == project.mesh.version
    assert restored.mesh.cell_layer_codes().tolist() == project.mesh.cell_layer_codes().tolist()


def test_encrypted_project_roundtrip(project, tmp_path):
    pytest.importorskip("cryptography")
    from geomodel3d.dataio.encryption import is_encrypted

    path = save_project(project, tmp_path / "secret.gm3d", password="secret")
    assert is_encrypted(path)
    assert read_manifest(path, password="secret")["name"] == project.name
    assert verify_integrity(path, password="secret")[0]

    restored = load_project(path, password="secret")
    assert restored.name == project.name
    with pytest.raises(ProjectFormatError):
        load_project(path)
    with pytest.raises(ProjectFormatError):
        load_project(path, password="wrong")


def test_manifest_and_integrity(project, tmp_path):
    path = save_project(project, tmp_path / "demo.gm3d")
    manifest = read_manifest(path)
    assert manifest["app"] == "GeoModel3D"
    assert "config.json" in manifest["files"]
    ok, problems = verify_integrity(path)
    assert ok and not problems


def test_save_preserves_created_timestamp(project, tmp_path):
    path = save_project(project, tmp_path / "demo.gm3d")
    created = read_manifest(path)["created"]

    time.sleep(0.01)
    save_project(project, path)
    manifest = read_manifest(path)
    assert manifest["created"] == created
    assert manifest["modified"] >= created


def test_corrupted_package_detected(project, tmp_path):
    path = save_project(project, tmp_path / "demo.gm3d")
    tampered = tmp_path / "tampered.gm3d"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "config.json":
                data = data.replace(b"demo", b"hack")
            target.writestr(item, data)
    ok, problems = verify_integrity(tampered)
    assert not ok
    assert any("config.json" in problem for problem in problems)


def test_load_missing_file(tmp_path):
    with pytest.raises(ProjectFormatError):
        load_project(tmp_path / "nope.gm3d")


def test_manifest_extra_file_detected(project, tmp_path):
    path = save_project(project, tmp_path / "demo.gm3d")
    tampered = tmp_path / "extra.gm3d"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("evil.txt", b"x")
    ok, problems = verify_integrity(tampered)
    assert not ok
    assert any("未声明" in problem for problem in problems)


def test_save_rejects_oversized_entry(project, tmp_path, monkeypatch):
    import geomodel3d.dataio.project_file as project_file

    monkeypatch.setattr(project_file, "MAX_ENTRY_BYTES", 10)
    with pytest.raises(ProjectFormatError):
        save_project(project, tmp_path / "toobig.gm3d")
