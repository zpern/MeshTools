"""Cross-format mesh conversion and geometry preservation tests."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import meshio
import numpy as np

from mesh_convert import convert_mesh, prepare_surface


class MeshConvertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])

    def test_triangle_roundtrip_all_non_cgns_formats(self):
        source = self.root / "source.vtu"
        meshio.write(source, meshio.Mesh(self.points, [("triangle", [[0, 1, 2]])]))
        for fmt in ("VTK", "VTU", "NAS", "PLY", "STL", "OBJ", "MSH", "FACET"):
            with self.subTest(fmt=fmt), contextlib.redirect_stdout(io.StringIO()):
                target = self.root / ("target." + fmt.lower())
                convert_mesh(source, target, "VTU", fmt)
                result = self.root / ("roundtrip_" + fmt + ".vtu")
                convert_mesh(target, result, fmt, "VTU")
                mesh = meshio.read(result)
                self.assertEqual(sum(len(c.data) for c in mesh.cells), 1)
                np.testing.assert_allclose(mesh.points[mesh.cells[0].data[0]], self.points[:3])
        self.assertIn("ASCII", (self.root / "target.vtk").read_text())

    def test_two_tetrahedra_remove_internal_face_and_existing_boundary_duplicate(self):
        points = np.vstack([self.points, [0.0, 0.0, -1.0]])
        mesh = meshio.Mesh(
            points,
            [
                ("tetra", [[0, 1, 2, 3], [0, 2, 1, 4]]),
                ("triangle", [[0, 1, 3], [0, 1, 2]]),
            ],
        )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = prepare_surface(mesh, "PLY")
        faces = [tuple(sorted(face)) for c in result.cells for face in c.data]
        self.assertEqual(len(faces), 6)
        self.assertEqual(len(set(faces)), 6)
        self.assertNotIn((0, 1, 2), faces)
        self.assertIn("boundary", output.getvalue())

    def test_quad_to_facet_triangulates_and_preserves_face_id(self):
        source = self.root / "quad.vtu"
        points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]])
        meshio.write(
            source,
            meshio.Mesh(points, [("quad", [[0, 1, 2, 3]])], cell_data={"face_id": [np.array([9])]}),
        )
        facet = self.root / "quad.facet"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            convert_mesh(source, facet, "VTU", "FACET")
            convert_mesh(facet, self.root / "result.vtu", "FACET", "VTU")
        mesh = meshio.read(self.root / "result.vtu")
        self.assertEqual(len(mesh.cells[0].data), 2)
        np.testing.assert_array_equal(mesh.cell_data["face_id"][0], [9, 9])
        self.assertIn("Triangulated", output.getvalue())

    def test_volume_faces_for_all_linear_volume_types(self):
        for kind, count, face_count in (
            ("tetra", 4, 4),
            ("hexahedron", 8, 6),
            ("wedge", 6, 5),
            ("pyramid", 5, 5),
        ):
            mesh = meshio.Mesh(np.zeros((count, 3)), [(kind, [list(range(count))])])
            with self.subTest(kind=kind), contextlib.redirect_stdout(io.StringIO()):
                result = prepare_surface(mesh, "PLY")
            self.assertEqual(sum(len(c.data) for c in result.cells), face_count)

    def test_line_cannot_silently_disappear_in_surface_output(self):
        source = self.root / "line.vtu"
        meshio.write(source, meshio.Mesh(self.points, [("line", [[0, 1]])]))
        target = self.root / "existing.stl"
        target.write_text("existing", encoding="utf-8")
        before = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "line"):
            convert_mesh(source, target, "VTU", "STL")
        self.assertEqual(target.read_text(), "existing")
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".*.tmp*")), [])

    def test_same_path_rejected(self):
        source = self.root / "source.vtu"
        meshio.write(source, meshio.Mesh(self.points, [("triangle", [[0, 1, 2]])]))
        before = source.read_bytes()
        with self.assertRaises(ValueError):
            convert_mesh(source, source, "VTU", "VTU")
        self.assertEqual(source.read_bytes(), before)

    def test_volume_roundtrip_general_formats(self):
        source = self.root / "volume.vtu"
        meshio.write(source, meshio.Mesh(self.points, [("tetra", [[0, 1, 2, 3]])]))
        for fmt in ("VTK", "VTU", "NAS", "MSH"):
            with self.subTest(fmt=fmt), contextlib.redirect_stdout(io.StringIO()):
                output = self.root / ("volume." + fmt.lower())
                if fmt == "VTU":
                    output = self.root / "copy.vtu"
                convert_mesh(source, output, "VTU", fmt)
                convert_mesh(output, self.root / "result.vtu", fmt, "VTU")
                mesh = meshio.read(self.root / "result.vtu")
                self.assertEqual(mesh.cells[0].type, "tetra")
                np.testing.assert_array_equal(mesh.cells[0].data, [[0, 1, 2, 3]])
                np.testing.assert_allclose(mesh.points, self.points)

    def test_write_failure_retains_existing_output_and_cleans_temporary(self):
        source, target = self.root / "source.vtu", self.root / "target.vtk"
        meshio.write(source, meshio.Mesh(self.points, [("triangle", [[0, 1, 2]])]))
        target.write_text("keep", encoding="utf-8")
        with mock.patch("mesh_convert.write_mesh", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                convert_mesh(source, target, "VTU", "VTK")
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(list(self.root.glob(".*.tmp*")), [])

    def test_concave_polygon_triangulation_preserves_area(self):
        points = np.array(
            [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0], [1.0, 1.0, 0.0], [0.0, 2.0, 0.0]]
        )
        mesh = meshio.Mesh(points, [("polygon", [[0, 1, 2, 3, 4]])])
        with contextlib.redirect_stdout(io.StringIO()):
            result = prepare_surface(mesh, "FACET")
        areas = []
        for a, b, c in result.cells[0].data:
            areas.append(np.linalg.norm(np.cross(points[b] - points[a], points[c] - points[a])) / 2)
        self.assertEqual(len(areas), 3)
        self.assertAlmostEqual(sum(areas), 3.0)

    def test_stl_normals_do_not_corrupt_gmsh_element_data(self):
        source, target = self.root / "surface.stl", self.root / "surface.msh"
        cells = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        meshio.write(source, meshio.Mesh(self.points, [("triangle", cells)]), binary=False)
        with contextlib.redirect_stdout(io.StringIO()) as log:
            convert_mesh(source, target, "STL", "MSH")
        result = meshio.read(target)
        self.assertEqual(sum(len(c.data) for c in result.cells), 4)
        self.assertIn("facet_normals", log.getvalue())
        self.assertNotIn("$ElementData", target.read_text())

    def test_vtk_output_is_compatible_with_quality_reader(self):
        source, target = self.root / "tetra.vtu", self.root / "tetra.vtk"
        meshio.write(source, meshio.Mesh(self.points, [("tetra", [[0, 1, 2, 3]])]))
        with contextlib.redirect_stdout(io.StringIO()):
            convert_mesh(source, target, "VTU", "VTK")
        self.assertNotIn("OFFSETS", target.read_text())
        quality_path = Path(__file__).resolve().parents[1] / "quality.py"
        spec = importlib.util.spec_from_file_location("_converter_test_quality", quality_path)
        quality = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = quality
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(quality)
        report = quality.analyze_mesh(quality.read_legacy_ascii_vtk(target))
        self.assertEqual(report.analyzed_cell_count, 1)

    def test_polydata_triangle_and_quad_keep_connectivity(self):
        source = self.root / "surface.vtk"
        source.write_text(
            "# vtk DataFile Version 3.0\nsurface\nASCII\nDATASET POLYDATA\n"
            "POINTS 5 double\n0 0 0\n1 0 0\n1 1 0\n0 1 0\n0 0 1\n"
            "POLYGONS 2 9\n3 0 1 4\n4 0 1 2 3\n"
            "CELL_DATA 2\nSCALARS face_id int 1\nLOOKUP_TABLE default\n7\n9\n",
            encoding="ascii",
        )
        target = self.root / "surface.vtu"
        with contextlib.redirect_stdout(io.StringIO()):
            convert_mesh(source, target, "VTK", "VTU")
        result = meshio.read(target)
        self.assertEqual([c.type for c in result.cells], ["triangle", "quad"])
        np.testing.assert_array_equal(result.cells[0].data, [[0, 1, 4]])
        np.testing.assert_array_equal(result.cells[1].data, [[0, 1, 2, 3]])
        self.assertEqual([int(a[0]) for a in result.cell_data["face_id"]], [7, 9])
        with contextlib.redirect_stdout(io.StringIO()):
            convert_mesh(source, self.root / "surface.stl", "VTK", "STL")
        stl = meshio.read(self.root / "surface.stl")
        self.assertEqual(len(stl.cells[0].data), 3)

    def test_polydata_strips_polylines_and_vertices_expand_explicitly(self):
        source = self.root / "mixed.vtk"
        source.write_text(
            "# vtk DataFile Version 3.0\nmixed\nASCII\nDATASET POLYDATA\n"
            "POINTS 4 double\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n"
            "VERTICES 1 3\n2 0 1\nLINES 1 4\n3 0 1 3\n"
            "TRIANGLE_STRIPS 1 5\n4 0 1 2 3\n",
            encoding="ascii",
        )
        target = self.root / "mixed.vtu"
        with contextlib.redirect_stdout(io.StringIO()) as log:
            convert_mesh(source, target, "VTK", "VTU")
        result = meshio.read(target)
        self.assertEqual(
            {c.type: len(c.data) for c in result.cells}, {"vertex": 2, "line": 2, "triangle": 2}
        )
        self.assertIn("Expanded", log.getvalue())
        np.testing.assert_array_equal(result.cells_dict["triangle"], [[0, 1, 2], [2, 1, 3]])


if __name__ == "__main__":
    unittest.main()
