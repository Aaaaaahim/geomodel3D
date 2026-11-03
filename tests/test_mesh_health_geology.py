"""网格地质体检：空洞、尖灭与层序倒置检测。"""
from __future__ import annotations

import numpy as np

from geomodel3d.algorithms.benchmark import synthetic_spec
from geomodel3d.algorithms.mesh_health import health_check
from geomodel3d.engine.mock import MockEngine
from geomodel3d.engine.umesh import UMesh


class _Ctx:
    def progress(self, percent, stage=""):
        return None

    def raise_if_cancelled(self):
        return None


def _mesh(resolution=(4, 4, 4)):
    return MockEngine().build(synthetic_spec(resolution), _Ctx())


def _without_cell(mesh: UMesh, drop: int) -> UMesh:
    keep = [index for index in range(mesh.n_cells) if index != drop]
    inverse = {old: new for new, old in enumerate(keep)}
    groups = {
        code: [inverse[index] for index in indices if index in inverse]
        for code, indices in mesh.face_groups.items()
    }
    return UMesh(
        vertices=mesh.vertices,
        cells=[mesh.cells[index] for index in keep],
        cell_types=mesh.cell_types[keep],
        cell_attr={key: np.asarray(value)[keep] for key, value in mesh.cell_attr.items()},
        face_groups=groups,
        meta=dict(mesh.meta),
    )


def test_missing_interior_cell_detected():
    mesh = _mesh()
    # 4x4x4 网格中 (i=1, j=1, k=1) 的单元索引；移除后形成内部空洞
    damaged = _without_cell(mesh, 1 * 4 * 4 + 1 * 4 + 1)
    report = health_check(damaged)
    codes = {issue.code for issue in report.issues}
    assert "MSH-HOL-001" in codes
    hole = next(issue for issue in report.issues if issue.code == "MSH-HOL-001")
    assert hole.count == 1


def test_pinched_layer_detected():
    mesh = _mesh()
    mesh.face_groups["C"] = []
    mesh.meta["layer_codes"].append("C")
    report = health_check(mesh)
    codes = {issue.code for issue in report.issues}
    assert "MSH-PIN-001" in codes


def test_layer_order_inversion_detected():
    mesh = _mesh()
    layer = np.asarray(mesh.cell_attr["layer"]).copy()
    grid = layer.reshape(4, 4, 4)
    for i in range(4):
        for j in range(4):
            if grid[i, j, 1] != grid[i, j, 2]:
                grid[i, j, 1], grid[i, j, 2] = grid[i, j, 2], grid[i, j, 1]
                break
        else:
            continue
        break
    mesh.cell_attr["layer"] = grid.ravel()
    report = health_check(mesh)
    codes = {issue.code for issue in report.issues}
    assert "MSH-ORD-001" in codes
