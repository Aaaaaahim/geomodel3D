"""工程包（*.gm3d）读写（3.5）。

工程包本质为 Zip：manifest.json 记录文件清单与 SHA-256；写入采用
临时文件 + 原子替换；打开时校验完整性，损坏时抛出可恢复错误。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from geomodel3d import __schema_version__, __version__
from geomodel3d.config.errors import ErrorCode, ProjectFormatError
from geomodel3d.config.schema import ProjectConfig
from geomodel3d.domain.entities import Drillhole, Fault, ModelRunMeta, SeismicLayer
from geomodel3d.domain.project import Project
from geomodel3d.dataio.encryption import (
    MAGIC,
    EncryptionError,
    decrypt_bytes,
    encrypt_bytes,
    is_encrypted_bytes,
)
from geomodel3d.domain.snapshot import ProjectSnapshot
from geomodel3d.engine.umesh import UMesh

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
CONFIG = "config.json"
STRATIGRAPHY = "stratigraphy.json"
BOREHOLES = "data/boreholes.json"
FAULTS = "data/faults.json"
SEISMIC = "data/seismic.json"
RUNS = "models/runs.json"
LATEST_MESH = "models/unified/latest.vtu"
LATEST_MESH_META = "models/unified/latest.json"
VERSIONS_INDEX = "versions/index.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _mesh_sidecar(mesh: UMesh) -> Dict[str, Any]:
    """网格的 face_groups / meta / version（VTU 不含这些信息，需单独保存）。"""
    return {
        "face_groups": {code: list(indices) for code, indices in mesh.face_groups.items()},
        "meta": mesh.meta,
        "version": mesh.version,
    }


def _apply_sidecar(mesh: Optional[UMesh], payload: Dict[str, Any]) -> Optional[UMesh]:
    if mesh is None or not payload:
        return mesh
    mesh.face_groups = {
        code: list(indices) for code, indices in payload.get("face_groups", {}).items()
    }
    mesh.meta = dict(payload.get("meta", {}))
    mesh.version = int(payload.get("version", 0))
    return mesh


def _dump_mesh(mesh: UMesh) -> bytes:
    """把 UMesh 序列化为 VTU 字节流。"""
    grid = mesh.to_pyvista()
    with tempfile.NamedTemporaryFile(suffix=".vtu", delete=False) as handle:
        tmp_path = handle.name
    try:
        grid.save(tmp_path)
        return Path(tmp_path).read_bytes()
    finally:
        os.remove(tmp_path)


def save_project(
    project: Project,
    path: Optional[Path] = None,
    *,
    update_project: bool = True,
    password: Optional[str] = None,
) -> Path:
    """保存工程包（原子替换）。

    ``update_project=False`` 时用于自动保存：不修改工程路径与脏标记。
    """
    target = Path(path) if path else project.path
    if target is None:
        raise ProjectFormatError("未指定工程保存路径。")
    target.parent.mkdir(parents=True, exist_ok=True)

    payloads: Dict[str, bytes] = {
        CONFIG: _json_bytes(project.config.model_dump(mode="json", by_alias=True)),
        STRATIGRAPHY: _json_bytes(
            [entry.model_dump(mode="json") for entry in project.config.stratigraphy]
        ),
        BOREHOLES: _json_bytes([h.model_dump(mode="json") for h in project.drillholes]),
        FAULTS: _json_bytes([f.model_dump(mode="json") for f in project.faults]),
        SEISMIC: _json_bytes([s.model_dump(mode="json") for s in project.seismic]),
        RUNS: _json_bytes([r.model_dump(mode="json") for r in project.runs]),
    }
    if project.has_model():
        payloads[LATEST_MESH] = _dump_mesh(project.mesh)
        payloads[LATEST_MESH_META] = _json_bytes(_mesh_sidecar(project.mesh))

    if project.snapshots:
        mesh_files: Dict[str, str] = {}
        entries: List[Dict[str, Any]] = []
        for snapshot in project.snapshots:
            entry = {
                "id": snapshot.id,
                "label": snapshot.label,
                "kind": snapshot.kind,
                "created": snapshot.created,
                "has_mesh": snapshot.mesh is not None,
                "config": snapshot.config.model_dump(mode="json", by_alias=True),
                "drillholes": [h.model_dump(mode="json") for h in snapshot.drillholes],
                "faults": [f.model_dump(mode="json") for f in snapshot.faults],
                "seismic": [s.model_dump(mode="json") for s in snapshot.seismic],
            }
            if snapshot.mesh is not None:
                # 内容哈希相同的快照共享同一份 VTU，避免工程包随版本线性膨胀；
                # 每个快照仍保留独立 sidecar（face_groups/meta/version 可能不同）。
                digest = snapshot.mesh_hash
                mesh_name = mesh_files.get(digest)
                if mesh_name is None:
                    mesh_name = f"versions/{snapshot.id}.vtu"
                    payloads[mesh_name] = _dump_mesh(snapshot.mesh)
                    mesh_files[digest] = mesh_name
                payloads[f"versions/{snapshot.id}.json"] = _json_bytes(
                    _mesh_sidecar(snapshot.mesh)
                )
                entry["mesh_file"] = mesh_name
            entries.append(entry)
        payloads[VERSIONS_INDEX] = _json_bytes(entries)

    for name, data in payloads.items():
        if len(data) > MAX_ENTRY_BYTES:
            raise ProjectFormatError(
                f"工程内文件 {name} 超过大小上限 {MAX_ENTRY_BYTES} 字节，无法保存。",
                context={"entry": name, "size": len(data)},
            )

    now = datetime.now(timezone.utc).isoformat()
    created = now
    if target.exists():
        try:
            created = str(
                read_manifest(target, password=password).get("created") or now
            )
        except Exception:  # noqa: BLE001 - 既有文件损坏/密码不同则按新工程处理
            logger.debug("读取既有工程 manifest 失败，使用当前时间", exc_info=True)
    manifest = {
        "app": "GeoModel3D",
        "app_version": __version__,
        "schema_version": __schema_version__,
        "name": project.name,
        "created": created,
        "modified": now,
        "crs": project.crs.model_dump(mode="json"),
        "files": {name: _sha256(data) for name, data in payloads.items()},
    }
    payloads[MANIFEST] = _json_bytes(manifest)

    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in payloads.items():
                archive.writestr(name, data)
        if password:
            try:
                encrypted = encrypt_bytes(Path(tmp_name).read_bytes(), password)
            except EncryptionError as exc:
                raise ProjectFormatError(
                    str(exc),
                    context={"path": str(target)},
                    suggestion="安装 cryptography（pip install \"geomodel3d[crypto]\"）后重试。",
                ) from exc
            Path(tmp_name).write_bytes(encrypted)
        os.replace(tmp_name, target)
    except Exception as exc:  # noqa: BLE001
        raise ProjectFormatError(
            f"保存工程失败: {exc}", context={"path": str(target)}
        ) from exc
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)

    if update_project:
        project.path = target
        project.mark_clean()
    return target


def _open_archive(source: Path, password: Optional[str] = None) -> zipfile.ZipFile:
    """打开普通或口令加密工程包，返回可读取的 ZipFile。"""
    with source.open("rb") as handle:
        header = handle.read(len(MAGIC))
    if not is_encrypted_bytes(header):
        return zipfile.ZipFile(source, "r")
    try:
        data = decrypt_bytes(source.read_bytes(), password)
    except EncryptionError as exc:
        raise ProjectFormatError(
            str(exc),
            context={"path": str(source)},
            suggestion="请输入正确的工程密码；加密工程需要 cryptography（geomodel3d[crypto]）。",
        ) from exc
    return zipfile.ZipFile(io.BytesIO(data), "r")


def load_project(path: Path, *, password: Optional[str] = None) -> Project:
    """读取并校验工程包；加密工程需提供正确 ``password``。"""
    source = Path(path)
    if not source.exists():
        raise ProjectFormatError(f"工程文件不存在: {source}", context={"path": str(source)})
    try:
        with _open_archive(source, password) as archive:
            names = set(archive.namelist())
            if MANIFEST not in names:
                raise ProjectFormatError("工程包缺少 manifest.json，文件可能已损坏。")
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            version = str(manifest.get("schema_version", ""))
            if version and version.split(".")[0] != __schema_version__.split(".")[0]:
                raise ProjectFormatError(
                    f"工程 schema 版本不兼容: {version}（当前 {__schema_version__}）",
                    code=ErrorCode.PRJ_OPN_002,
                )
            problems = _verify_archive(archive, manifest)
            if problems:
                raise ProjectFormatError(
                    "工程包完整性校验失败: " + "; ".join(problems[:3]),
                    context={"problems": problems},
                )
            config = ProjectConfig.model_validate(json.loads(archive.read(CONFIG)))
            drillholes = [
                Drillhole.model_validate(item)
                for item in json.loads(archive.read(BOREHOLES))
            ] if BOREHOLES in names else []
            faults = [
                Fault.model_validate(item) for item in json.loads(archive.read(FAULTS))
            ] if FAULTS in names else []
            seismic = [
                SeismicLayer.model_validate(item)
                for item in json.loads(archive.read(SEISMIC))
            ] if SEISMIC in names else []
            runs = [
                ModelRunMeta.model_validate(item) for item in json.loads(archive.read(RUNS))
            ] if RUNS in names else []
            mesh = _load_mesh(archive) if LATEST_MESH in names else None
            if mesh is not None and LATEST_MESH_META in names:
                _apply_sidecar(mesh, json.loads(archive.read(LATEST_MESH_META)))
            snapshots = _load_snapshots(archive, names)
    except ProjectFormatError:
        raise
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ProjectFormatError(
            f"工程包解析失败: {exc}", context={"path": str(source)}
        ) from exc

    project = Project(
        config=config,
        path=source,
        drillholes=drillholes,
        faults=faults,
        seismic=seismic,
    )
    project.runs = runs
    project.mesh = mesh
    project.snapshots = snapshots
    project.mark_clean()
    return project


def read_manifest(path: Path, *, password: Optional[str] = None) -> Dict[str, Any]:
    with _open_archive(Path(path), password) as archive:
        return json.loads(archive.read(MANIFEST).decode("utf-8"))


def verify_integrity(
    path: Path, *, password: Optional[str] = None
) -> Tuple[bool, List[str]]:
    """校验工程包完整性，返回 ``(是否通过, 问题列表)``。"""
    try:
        with _open_archive(Path(path), password) as archive:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
            problems = _verify_archive(archive, manifest)
            return (not problems), problems
    except Exception as exc:  # noqa: BLE001
        return False, [str(exc)]


MAX_ENTRY_BYTES = 512 * 1024 * 1024  # 单文件解压上限，防 zip 炸弹


def _verify_archive(archive: zipfile.ZipFile, manifest: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    declared = set(manifest.get("files", {}))
    actual = set(archive.namelist())
    expected = declared | {MANIFEST}
    extra = actual - expected
    if extra:
        problems.append(f"存在未声明的文件: {sorted(extra)[:3]}")
    for name, checksum in manifest.get("files", {}).items():
        try:
            info = archive.getinfo(name)
        except KeyError:
            problems.append(f"缺少文件 {name}")
            continue
        if info.file_size > MAX_ENTRY_BYTES:
            problems.append(f"文件超过大小上限，疑似损坏或恶意: {name}")
            continue
        try:
            data = archive.read(name)
        except KeyError:
            problems.append(f"缺少文件 {name}")
            continue
        if _sha256(data) != checksum:
            problems.append(f"校验和不匹配 {name}")
    return problems


def _load_mesh(archive: zipfile.ZipFile, name: str = LATEST_MESH) -> Optional[UMesh]:
    import pyvista as pv

    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".vtu", delete=False) as handle:
            tmp_path = handle.name
            handle.write(archive.read(name))
        grid = pv.read(tmp_path)
        return UMesh.from_pyvista(grid, meta={"source": "project"})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:  # noqa: BLE001
                logger.debug("删除临时 VTU 失败: %s", tmp_path, exc_info=True)


def _load_snapshots(archive: zipfile.ZipFile, names: set) -> List[ProjectSnapshot]:
    if VERSIONS_INDEX not in names:
        return []
    entries = json.loads(archive.read(VERSIONS_INDEX))
    snapshots: List[ProjectSnapshot] = []
    for entry in entries:
        mesh_name = entry.get("mesh_file") or f"versions/{entry['id']}.vtu"
        meta_name = f"versions/{entry['id']}.json"
        mesh = _load_mesh(archive, mesh_name) if entry.get("has_mesh") and mesh_name in names else None
        if mesh is not None and meta_name in names:
            _apply_sidecar(mesh, json.loads(archive.read(meta_name)))
        snapshots.append(
            ProjectSnapshot(
                id=entry["id"],
                label=entry.get("label", "快照"),
                kind=entry.get("kind", "manual"),
                created=entry.get("created", ""),
                config=ProjectConfig.model_validate(entry["config"]),
                drillholes=[Drillhole.model_validate(item) for item in entry.get("drillholes", [])],
                faults=[Fault.model_validate(item) for item in entry.get("faults", [])],
                seismic=[SeismicLayer.model_validate(item) for item in entry.get("seismic", [])],
                mesh=mesh,
            )
        )
    return snapshots


__all__ = [
    "save_project",
    "load_project",
    "read_manifest",
    "verify_integrity",
    "MANIFEST",
    "VERSIONS_INDEX",
]
