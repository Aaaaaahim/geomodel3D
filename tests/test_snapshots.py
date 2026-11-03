"""工程版本快照与自动保存测试（M9-F2/F3 / FR-25）。"""
from __future__ import annotations

from geomodel3d.app.context import AppContext
from geomodel3d.config.schema import AppSettings, Bounds, StratigraphyEntry
from geomodel3d.dataio.project_file import load_project
from geomodel3d.domain.entities import Fault, FaultParams
from geomodel3d.services.modeling_service import spec_from_project

CSV = (
    "孔号,东坐标,北坐标,孔口高程,层顶深,层底深,地层代码,品位\n"
    "H1,0,0,100,0,20,A,1.2\nH1,0,0,100,20,50,B,2.4\n"
    "H2,180,180,120,0,25,A,0.9\nH2,180,180,120,25,60,B,1.6\n"
)


def _context(tmp_path):
    context = AppContext(AppSettings(versions_max=10), log_dir=tmp_path / "logs", enable_tasks=False)
    project = context.projects.new_project("demo", bounds=Bounds(min=[0, 0, 0], max=[200, 200, 200]))
    project.config.stratigraphy = [
        StratigraphyEntry(layer_code="A", order=0),
        StratigraphyEntry(layer_code="B", order=1),
    ]
    csv = tmp_path / "bh.csv"
    csv.write_text(CSV, encoding="utf-8-sig")
    context.data.import_boreholes(csv)
    return context


def test_snapshot_create_restore(tmp_path):
    context = _context(tmp_path)
    project = context.projects.project
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)

    snapshot = context.projects.snapshot("建模后")
    assert project.snapshot(snapshot.id) is not None
    assert snapshot.mesh is not None

    project.config.project.name = "renamed"
    project.faults = [Fault(fault_id="F1", params=FaultParams())]
    assert project.name == "renamed"

    assert context.projects.restore(snapshot.id)
    assert project.name == "demo"
    assert project.faults == []


def test_snapshot_reuses_unchanged_mesh(tmp_path):
    context = _context(tmp_path)
    project = context.projects.project
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)

    first = context.projects.snapshot("v1")
    project.config.project.name = "renamed"
    second = context.projects.snapshot("v2")

    assert second.mesh is first.mesh
    assert second.mesh_hash == first.mesh_hash
    assert context.projects.restore(first.id)
    assert project.name == "demo"
    assert project.mesh is not first.mesh


def test_snapshot_persistence_dedupes_identical_mesh(tmp_path):
    import json
    import zipfile

    context = _context(tmp_path)
    project = context.projects.project
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)
    context.projects.snapshot("v1")
    project.config.project.name = "renamed"
    context.projects.snapshot("v2")

    path = context.projects.save(tmp_path / "demo.gm3d")
    with zipfile.ZipFile(path) as archive:
        vtu = [name for name in archive.namelist() if name.startswith("versions/") and name.endswith(".vtu")]
        index = json.loads(archive.read("versions/index.json"))
    assert len(vtu) == 1
    assert len({entry["mesh_file"] for entry in index if entry.get("has_mesh")}) == 1

    restored = load_project(path)
    assert len(restored.snapshots) == 2
    assert all(snapshot.mesh is not None for snapshot in restored.snapshots)


def test_snapshot_persistence_keeps_distinct_meshes(tmp_path):
    import zipfile

    context = _context(tmp_path)
    project = context.projects.project
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)
    context.projects.snapshot("v1")

    project.mesh = project.mesh.copy()
    project.mesh.vertices[:, 2] += 1.0
    project.mesh.version += 1
    context.projects.snapshot("v2")

    path = context.projects.save(tmp_path / "demo.gm3d")
    with zipfile.ZipFile(path) as archive:
        vtu = [name for name in archive.namelist() if name.startswith("versions/") and name.endswith(".vtu")]
    assert len(vtu) == 2


def test_snapshot_persistence(tmp_path):
    context = _context(tmp_path)
    project = context.projects.project
    context.modeling.create_run(spec_from_project(project, engine="mock", resolution=(8, 8, 6)), async_run=False)
    context.projects.snapshot("版本一")
    path = context.projects.save(tmp_path / "demo.gm3d")

    restored = load_project(path)
    assert len(restored.snapshots) == 1
    snap = restored.snapshots[0]
    assert snap.label == "版本一"
    assert snap.mesh is not None
    assert snap.mesh.n_cells == project.mesh.n_cells
    assert restored.restore_snapshot(snap.id)


def test_autosave_and_recovery(tmp_path):
    import os

    context = _context(tmp_path)
    project = context.projects.project
    path = context.projects.save(tmp_path / "demo.gm3d")

    project.config.project.name = "edited"
    project.mark_dirty()
    autosave = context.projects.autosave()
    assert autosave is not None and autosave.exists()
    # 确定性：显式把自动保存文件设为比工程文件更新
    newer = path.stat().st_mtime + 5
    os.utime(autosave, (newer, newer))

    candidate = context.projects.has_recovery()
    assert candidate == autosave

    recovered = context.projects.recover()
    assert recovered.name == "edited"
    assert recovered.path == path


def test_snapshot_diff(tmp_path):
    context = _context(tmp_path)
    project = context.projects.project
    first = context.projects.snapshot("v1")
    project.config.project.name = "changed"
    second = context.projects.snapshot("v2")
    diff = context.projects.diff(first.id, second.id)
    assert "name" in diff["changed"]


def test_recover_from_orphan_autosave(tmp_path):
    from geomodel3d.dataio.project_file import save_project

    context = _context(tmp_path)
    project = context.projects.project
    project.config.project.name = "orphan"
    project.mark_dirty()
    autosave = save_project(project, tmp_path / "orphan.gm3d.autosave", update_project=False)

    recovered = context.projects.recover_from(autosave)
    assert recovered.name == "orphan"
    assert recovered.path is None  # 未保存工程恢复后需另存
    assert not autosave.exists()  # 恢复后清理
