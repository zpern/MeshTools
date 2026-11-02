"""CGNS bridge tests, including real native-library/VTK readback."""

import importlib.util
import tempfile
from pathlib import Path

import meshio
import numpy as np
import pytest

from mesh_convert import find_cgns_dll, read_cgns, write_cgns


def test_file_based_loading():
    path = Path(__file__).resolve().parents[1] / "mesh_convert.py"
    spec = importlib.util.spec_from_file_location("_standalone_bridge_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module._bind_cgns)


@pytest.fixture
def cgns_library():
    pytest.importorskip("vtk")
    try:
        return find_cgns_dll()
    except FileNotFoundError:
        pytest.skip("No native CGNS library installed")


@pytest.mark.parametrize(
    "cells",
    [
        [("triangle", [[0, 1, 2]]), ("quad", [[0, 1, 2, 3]])],
        [
            ("tetra", [[0, 1, 2, 4]]),
            ("pyramid", [[0, 1, 2, 3, 4]]),
            ("wedge", [[0, 1, 2, 4, 5, 6]]),
            ("hexahedron", [[0, 1, 2, 3, 4, 5, 6, 7]]),
        ],
        [("tetra", [[0, 1, 2, 4]]), ("triangle", [[0, 1, 2]])],
        [("line", [[0, 1], [1, 2]])],
        [("triangle", [[0, 1, 2]]), ("line", [[0, 1]]), ("vertex", [[0]])],
    ],
)
def test_native_roundtrip(tmp_path, cgns_library, cells):
    points = np.array(
        [
            [0, 0, 0],
            [1, 0, 0],
            [1, 1, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [1, 1, 1],
            [0, 1, 1],
        ],
        dtype=float,
    )
    mesh = meshio.Mesh(points, cells)
    path = tmp_path / "mixed.cgns"
    write_cgns(path, mesh, cgns_library)
    result = read_cgns(path)
    np.testing.assert_allclose(result.points, points)
    for name, expected in cells:
        np.testing.assert_array_equal(result.cells_dict[name], expected)


def test_chinese_space_path_roundtrip(tmp_path, cgns_library):
    folder = tmp_path / "中文 空格目录"
    folder.mkdir()
    path = folder / "表面 网格.cgns"
    mesh = meshio.Mesh(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        [("triangle", [[0, 1, 2]])],
    )
    write_cgns(path, mesh, cgns_library)
    result = read_cgns(path)
    np.testing.assert_array_equal(result.points, mesh.points)
    np.testing.assert_array_equal(result.cells_dict["triangle"], [[0, 1, 2]])


def test_chinese_system_temp_directory(tmp_path, cgns_library, monkeypatch):
    folder = tmp_path / "中文系统临时目录"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    mesh = meshio.Mesh(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        [("triangle", [[0, 1, 2]])],
    )
    write_cgns(tmp_path / "output.cgns", mesh, cgns_library)


@pytest.mark.parametrize("index", [-1, 3])
def test_invalid_index_rejected_before_loading_library(tmp_path, index):
    mesh = meshio.Mesh(np.zeros((3, 3)), [("triangle", [[0, 1, index]])])
    with pytest.raises(ValueError, match="invalid vertex index"):
        write_cgns(tmp_path / "invalid.cgns", mesh, Path("missing.dll"))
    assert not (tmp_path / "invalid.cgns").exists()


def test_high_order_rejected(tmp_path):
    mesh = meshio.Mesh(np.zeros((6, 3)), [("triangle6", [[0, 1, 2, 3, 4, 5]])])
    with pytest.raises(ValueError, match="Unsupported CGNS cell type"):
        write_cgns(tmp_path / "invalid.cgns", mesh)


def test_point_only_rejected(tmp_path):
    mesh = meshio.Mesh(np.zeros((1, 3)), [("vertex", [[0]])])
    with pytest.raises(ValueError, match="at least one line"):
        write_cgns(tmp_path / "invalid.cgns", mesh)
