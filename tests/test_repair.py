"""模型修复测试（M6）。"""
from __future__ import annotations

import numpy as np
import pytest

from geomodel3d.algorithms.mesh_health import health_check
from geomodel3d.algorithms.repair import RepairOptions, repair
from geomodel3d.engine import EngineRegistry
from geomodel3d.engine.umesh import VTK_HEXAHEDRON, UMesh


def _build(project, modeling_spec, ctx):
    return EngineRegistry().resolve(modeling_spec).build(modeling_spec, ctx)


def test_remove_duplicate_cell(project, modeling_spec, ctx):
    mesh = _build(project, modeling_spec, ctx)
    cells = list(mesh.cells) + [mesh.cells[0].copy()]
    cell_types = np.append(mesh.cell_types, VTK_HEXAHEDRON)
    attrs = {key: np.append(value, [value[0]]) for key, value in mesh.cell_attr.items()}
    bad = UMesh(vertices=mesh.vertices, cells=cells, cell_types=cell_types, cell_attr=attrs)
    fixed, report = repair(bad, RepairOptions())
    assert fixed.n_cells == mesh.n_cells
    assert any("重复" in action for action in report.actions)


def test_weld_vertices():
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
         [1e-9, 0, 0], [1, 1, 0], [1, 0, 1], [0, 1, 1]],
        dtype=float,
    )
    cells = [np.array([0, 1, 2, 3]), np.array([4, 5, 6, 7])]
    mesh = UMesh(vertices=vertices, cells=cells, cell_types=np.array([10, 10]))
    fixed, report = repair(mesh, RepairOptions(weld_tolerance=1e-6))
    assert fixed.n_points == 7
    assert any("焊接" in action for action in report.actions)


def test_repair_preserves_face_groups(project, modeling_spec, ctx):
    mesh = _build(project, modeling_spec, ctx)
    cells = list(mesh.cells) + [mesh.cells[0].copy()]
    cell_types = np.append(mesh.cell_types, VTK_HEXAHEDRON)
    attrs = {key: np.append(value, [value[0]]) for key, value in mesh.cell_attr.items()}
    groups = {code: list(indices) + ([0] if code == next(iter(mesh.face_groups)) else [])
              for code, indices in mesh.face_groups.items()}
    bad = UMesh(
        vertices=mesh.vertices, cells=cells, cell_types=cell_types,
        cell_attr=attrs, face_groups=groups,
    )
    fixed, _ = repair(bad)
    assert fixed.face_groups
    assert health_check(fixed).passed


def test_fix_layer_order_inversion():
    """可选层序修复应把每列地层号重排为自上而下递减。"""
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    mesh = MockEngine().build(synthetic_spec((4, 4, 4)), _Ctx())
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
    assert "MSH-ORD-001" in {issue.code for issue in health_check(mesh).issues}

    fixed, report = repair(mesh, RepairOptions(fix_layer_order=True))
    assert any("层序" in action for action in report.actions)
    assert "MSH-ORD-001" not in {issue.code for issue in health_check(fixed).issues}
    assert sum(len(values) for values in fixed.face_groups.values()) == fixed.n_cells


def test_merge_layer_cells_preserves_volume():
    """同层单元沿 Z 合并应减少单元数并严格保持逐层体积。"""
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.algorithms.statistics import mesh_layer_volumes
    from geomodel3d.engine.mock import MockEngine

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    mesh = MockEngine().build(synthetic_spec((4, 4, 4)), _Ctx())
    fixed, report = repair(
        mesh,
        RepairOptions(
            merge_layers=True,
            fill_holes=False,
            weld=False,
            remove_degenerate=False,
            remove_duplicate=False,
            fix_normals=False,
        ),
    )
    assert fixed.n_cells < mesh.n_cells
    assert any("合并同层" in action for action in report.actions)
    assert fixed.total_volume() == pytest.approx(mesh.total_volume(), rel=1e-9)

    expected = mesh_layer_volumes(mesh)
    actual = mesh_layer_volumes(fixed)
    assert set(actual) == set(expected)
    for code, volume in expected.items():
        assert actual[code] == pytest.approx(volume, rel=1e-9)


def test_fix_layer_order_non_regular_fallback():
    """非规则网格（缺单元）应通过 XY 分箱回退修复层序。"""
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    mesh = MockEngine().build(synthetic_spec((4, 4, 4)), _Ctx())
    layer = np.asarray(mesh.cell_attr["layer"]).copy()
    grid = layer.reshape(4, 4, 4)
    target = None
    for i in range(4):
        for j in range(4):
            if grid[i, j, 1] != grid[i, j, 2]:
                grid[i, j, 1], grid[i, j, 2] = grid[i, j, 2], grid[i, j, 1]
                target = (i, j)
                break
        else:
            continue
        break
    mesh.cell_attr["layer"] = grid.ravel()

    drop = 0  # 移除另一个列，制造非规则网格
    keep = [index for index in range(mesh.n_cells) if index != drop]
    inverse = {old: new for new, old in enumerate(keep)}
    groups = {
        code: [inverse[index] for index in indices if index in inverse]
        for code, indices in mesh.face_groups.items()
    }
    damaged = UMesh(
        vertices=mesh.vertices,
        cells=[mesh.cells[index] for index in keep],
        cell_types=mesh.cell_types[keep],
        cell_attr={key: np.asarray(value)[keep] for key, value in mesh.cell_attr.items()},
        face_groups=groups,
        meta=dict(mesh.meta),
    )

    fixed, report = repair(
        damaged, RepairOptions(fill_holes=False, fix_layer_order=True, weld=False)
    )
    assert any("层序" in action for action in report.actions)

    centers = fixed.cell_centers()
    column = [
        index
        for index, center in enumerate(centers)
        if abs(center[0] - (target[0] + 0.5) * 250.0) < 1e-6
        and abs(center[1] - (target[1] + 0.5) * 250.0) < 1e-6
    ]
    values = [
        int(fixed.cell_attr["layer"][index])
        for index in sorted(column, key=lambda index: -centers[index, 2])
    ]
    assert values == sorted(values, reverse=True)


def _cavity_mesh():
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    mesh = MockEngine().build(synthetic_spec((4, 4, 4)), _Ctx())
    perturbed = mesh.vertices.copy()
    perturbed += np.random.RandomState(0).uniform(-0.001, 0.001, perturbed.shape)
    full = UMesh(
        vertices=perturbed,
        cells=[cell.copy() for cell in mesh.cells],
        cell_types=mesh.cell_types.copy(),
        cell_attr={key: np.asarray(value).copy() for key, value in mesh.cell_attr.items()},
        face_groups={key: list(value) for key, value in mesh.face_groups.items()},
        meta=dict(mesh.meta),
    )
    drop = 1 * 4 * 4 + 1 * 4 + 1
    keep = [index for index in range(full.n_cells) if index != drop]
    inverse = {old: new for new, old in enumerate(keep)}
    groups = {
        code: [inverse[index] for index in indices if index in inverse]
        for code, indices in full.face_groups.items()
    }
    damaged = UMesh(
        vertices=full.vertices,
        cells=[full.cells[index] for index in keep],
        cell_types=full.cell_types[keep],
        cell_attr={key: np.asarray(value)[keep] for key, value in full.cell_attr.items()},
        face_groups=groups,
        meta={key: value for key, value in full.meta.items() if key != "resolution"},
    )
    return full, damaged


def test_fill_non_regular_cavity():
    """无 resolution 的非规则网格应通过边界环四面体化填充内部空腔。"""
    full, damaged = _cavity_mesh()
    fixed, report = repair(
        damaged,
        RepairOptions(
            weld=False,
            remove_degenerate=False,
            remove_duplicate=False,
            fix_normals=False,
            smooth_iterations=0,
        ),
    )
    assert fixed.n_cells > damaged.n_cells
    assert any("空洞" in action for action in report.actions)
    assert fixed.total_volume() == pytest.approx(full.total_volume(), rel=1e-6)
    assert sum(len(values) for values in fixed.face_groups.values()) == fixed.n_cells
    assert health_check(fixed).passed


def test_fill_interior_hole():
    """规则网格内部缺单元应被自动补回，并保持总体积。"""
    from geomodel3d.algorithms.benchmark import synthetic_spec
    from geomodel3d.engine.mock import MockEngine

    class _Ctx:
        def progress(self, percent, stage=""):
            return None

        def raise_if_cancelled(self):
            return None

    mesh = MockEngine().build(synthetic_spec((4, 4, 4)), _Ctx())
    drop = 1 * 4 * 4 + 1 * 4 + 1
    keep = [index for index in range(mesh.n_cells) if index != drop]
    inverse = {old: new for new, old in enumerate(keep)}
    groups = {
        code: [inverse[index] for index in indices if index in inverse]
        for code, indices in mesh.face_groups.items()
    }
    damaged = UMesh(
        vertices=mesh.vertices,
        cells=[mesh.cells[index] for index in keep],
        cell_types=mesh.cell_types[keep],
        cell_attr={key: np.asarray(value)[keep] for key, value in mesh.cell_attr.items()},
        face_groups=groups,
        meta=dict(mesh.meta),
    )
    assert "MSH-HOL-001" in {issue.code for issue in health_check(damaged).issues}

    fixed, report = repair(damaged, RepairOptions())
    assert fixed.n_cells == mesh.n_cells
    assert any("空洞" in action for action in report.actions)
    assert health_check(fixed).passed
    assert fixed.total_volume() == pytest.approx(mesh.total_volume())


def test_simplification_merges_close_vertices():
    """顶点聚类简化应合并容差内的顶点，并报告简化动作。"""
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
         [1e-6, 0, 0]],
        dtype=float,
    )
    mesh = UMesh(
        vertices=vertices,
        cells=[np.arange(8)],
        cell_types=np.array([VTK_HEXAHEDRON]),
    )
    fixed, report = repair(
        mesh, RepairOptions(weld=False, simplify_tolerance=0.2)
    )
    assert fixed.n_points == 8
    assert fixed.n_cells == 1
    assert any("简化" in action for action in report.actions)


def test_fix_inverted_normals():
    from geomodel3d.algorithms.repair import _ORIENTATION_FLIP

    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    inverted = np.array(_ORIENTATION_FLIP[8], dtype=np.int64)
    mesh = UMesh(vertices=vertices, cells=[inverted], cell_types=np.array([VTK_HEXAHEDRON]))
    assert health_check(mesh).metrics["inverted"] == 1

    fixed, report = repair(mesh, RepairOptions())
    assert fixed.n_cells == 1
    assert any("法向" in action for action in report.actions)
    assert health_check(fixed).metrics["inverted"] == 0


def test_vectorized_degenerate_filter():
    vertices = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        dtype=float,
    )
    good = np.arange(8, dtype=np.int64)
    degenerate = np.array([0, 0, 2, 3, 4, 5, 6, 7], dtype=np.int64)  # 顶点重复
    mesh = UMesh(
        vertices=vertices,
        cells=[good, degenerate],
        cell_types=np.array([VTK_HEXAHEDRON, VTK_HEXAHEDRON]),
    )
    fixed, report = repair(mesh, RepairOptions(weld=False))
    assert fixed.n_cells == 1
    assert any("退化" in action for action in report.actions)
