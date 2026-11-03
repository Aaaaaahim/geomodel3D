"""工程服务（M9）：新建/打开/保存/另存/关闭、最近工程、自动保存。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from geomodel3d.app.events import (
    MODEL_CHANGED,
    PROJECT_CLOSED,
    PROJECT_MODIFIED,
    PROJECT_OPENED,
    PROJECT_SAVED,
    PROJECT_SNAPSHOT,
    EventBus,
)
from geomodel3d.config.defaults import PROJECT_SUFFIX, user_data_dir
from geomodel3d.config.errors import ErrorCode, GeoModelError
from geomodel3d.config.schema import (
    AppSettings,
    Bounds,
    CRSConfig,
    ProjectConfig,
    ProjectMeta,
)
from geomodel3d.dataio.project_file import load_project, save_project
from geomodel3d.domain.project import Project
from geomodel3d.domain.snapshot import ProjectSnapshot

logger = logging.getLogger(__name__)


class ProjectService:
    """工程生命周期用例服务。"""

    def __init__(self, bus: EventBus, settings: AppSettings) -> None:
        self._bus = bus
        self.settings = settings
        self.project: Optional[Project] = None
        self.password: Optional[str] = None

    # ------------------------------------------------------------------ #
    def new_project(
        self,
        name: str = "untitled",
        *,
        crs_epsg: Optional[int] = None,
        bounds: Optional[Bounds] = None,
    ) -> Project:
        import uuid

        meta = ProjectMeta(
            id=uuid.uuid4().hex[:12],
            name=name,
            crs=CRSConfig(epsg=crs_epsg or self.settings.default_crs_epsg),
            bounds=bounds or Bounds(),
        )
        project = Project(ProjectConfig(project=meta))
        self.password = None
        self._set_project(project)
        logger.info("新建工程: %s", name)
        return project

    def open_project(self, path: Path, *, password: Optional[str] = None) -> Project:
        project = load_project(Path(path), password=password)
        self.password = password
        self._set_project(project)
        self.settings.push_recent(str(project.path))
        logger.info("打开工程: %s", path)
        return project

    def save(self, path: Optional[Path] = None, *, password: Optional[str] = None) -> Path:
        project = self._require_project()
        effective = password if password is not None else self.password
        pre_autosave = self.autosave_path()
        target = save_project(project, path, password=effective)
        self.password = effective or None
        self.settings.push_recent(str(target))
        self._bus.publish(PROJECT_SAVED, {"path": str(target)}, source="project")
        logger.info("保存工程: %s", target)
        _remove_file(pre_autosave)
        self._clear_autosave(target)
        return target

    def save_as(self, path: Path, *, password: Optional[str] = None) -> Path:
        project = self._require_project()
        target = Path(path)
        if target.suffix.lower() != PROJECT_SUFFIX:
            target = target.with_suffix(PROJECT_SUFFIX)
        return self.save(target, password=password)

    def close(self) -> None:
        if self.project is None:
            return
        logger.info("关闭工程: %s", self.project.name)
        self.project = None
        self.password = None
        self._bus.publish(PROJECT_CLOSED, None, source="project")

    def touch(self) -> None:
        """标记工程已修改并广播。"""
        project = self._require_project()
        project.mark_dirty()
        self._bus.publish(PROJECT_MODIFIED, project.summary(), source="project")

    # 版本快照 --------------------------------------------------------- #
    def snapshot(self, label: str = "手动快照", *, kind: str = "manual") -> ProjectSnapshot:
        project = self._require_project()
        snapshot = project.create_snapshot(
            label, kind=kind, versions_max=self.settings.versions_max
        )
        logger.info("创建版本快照: %s (%s)", snapshot.id, label)
        self._bus.publish(PROJECT_SNAPSHOT, snapshot.summary(), source="project")
        return snapshot

    def list_snapshots(self):
        return self._require_project().snapshot_summaries()

    def restore(self, snapshot_id: str) -> bool:
        project = self._require_project()
        if not project.restore_snapshot(snapshot_id):
            return False
        logger.info("回滚到版本快照: %s", snapshot_id)
        self._bus.publish(PROJECT_OPENED, project, source="project")
        self._bus.publish(MODEL_CHANGED, {"version": project.mesh.version if project.mesh else 0}, source="project")
        return True

    def diff(self, left_id: str, right_id: str) -> dict:
        """对比两个快照（配置/数据/模型维度）。"""
        project = self._require_project()
        left = project.snapshot(left_id)
        right = project.snapshot(right_id)
        if left is None or right is None:
            raise GeoModelError(ErrorCode.GEN_STATE_001, "快照不存在。")
        left_summary = left.summary()
        right_summary = right.summary()
        keys = set(left_summary) | set(right_summary)
        return {
            "left": left_summary,
            "right": right_summary,
            "changed": {
                key: [left_summary.get(key), right_summary.get(key)]
                for key in sorted(keys)
                if left_summary.get(key) != right_summary.get(key)
            },
        }

    # 自动保存 / 崩溃恢复 ----------------------------------------------- #
    def autosave_path(self) -> Path:
        project = self._require_project()
        if project.path is not None:
            return project.path.with_suffix(project.path.suffix + ".autosave")
        safe = "".join(ch for ch in project.name if ch.isalnum() or ch in "-_") or "untitled"
        return user_data_dir() / "autosave" / f"{safe}{PROJECT_SUFFIX}.autosave"

    def autosave(self) -> Optional[Path]:
        project = self._require_project()
        if not project.dirty:
            return None
        path = save_project(
            project,
            self.autosave_path(),
            update_project=False,
            password=self.password,
        )
        logger.info("自动保存: %s", path)
        return path

    def has_recovery(self) -> Optional[Path]:
        if self.project is None or self.project.path is None:
            return None
        candidate = self.autosave_path()
        if candidate.exists() and candidate.stat().st_mtime > self.project.path.stat().st_mtime:
            return candidate
        return None

    def orphan_autosaves(self) -> list:
        """列出未保存工程的孤儿自动保存文件（崩溃恢复入口）。"""
        folder = user_data_dir() / "autosave"
        if not folder.exists():
            return []
        return sorted(folder.glob(f"*{PROJECT_SUFFIX}.autosave"))

    def recover_from(self, autosave: Path, *, password: Optional[str] = None) -> Project:
        """从未保存工程的自动保存文件恢复（路径置空，需另存为）。"""
        autosave = Path(autosave)
        effective = password if password is not None else self.password
        recovered = load_project(autosave, password=effective)
        self.password = effective or None
        recovered.path = None
        recovered.mark_dirty()
        self._set_project(recovered)
        _remove_file(autosave)
        logger.info("从孤儿自动保存恢复: %s", autosave)
        return recovered

    def recover(self) -> Project:
        path = self._require_project().path
        candidate = self.autosave_path()
        recovered = load_project(candidate, password=self.password)
        recovered.path = path
        recovered.mark_dirty()
        self._set_project(recovered)
        self._clear_autosave(path)
        logger.info("从自动保存恢复: %s", candidate)
        return recovered

    def _clear_autosave(self, project_path: Optional[Path]) -> None:
        """正常保存/恢复后删除自动保存文件。"""
        if project_path is None:
            return
        _remove_file(project_path.with_suffix(project_path.suffix + ".autosave"))

    # ------------------------------------------------------------------ #
    def _set_project(self, project: Project) -> None:
        self.project = project
        self._bus.publish(PROJECT_OPENED, project, source="project")

    def _require_project(self) -> Project:
        if self.project is None:
            raise GeoModelError(ErrorCode.GEN_STATE_001, "当前没有打开的工程。")
        return self.project


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # noqa: BLE001
        logger.debug("删除文件失败: %s", path, exc_info=True)


__all__ = ["ProjectService"]
