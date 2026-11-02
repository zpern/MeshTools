import contextlib
import io
import math
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from quality import (
    BIN_LABELS,
    VTK_HEXAHEDRON,
    VTK_PYRAMID,
    VTK_TETRA,
    VTK_WEDGE,
    MeshData,
    VtkFormatError,
    analyze_mesh,
    equiangle_skewness,
    main,
    read_legacy_ascii_vtk,
    signed_cell_volume,
    skewness_bin_index,
    write_histogram_png,
)

TETRA = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))
REGULAR_TETRA = (
    (0, 0, 0),
    (1, 0, 0),
    (0.5, math.sqrt(3) / 2, 0),
    (0.5, math.sqrt(3) / 6, math.sqrt(2 / 3)),
)
HEX = (
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (1, 1, 1),
    (0, 1, 1),
)
WEDGE = (
    (0, 0, 0),
    (1, 0, 0),
    (0.5, math.sqrt(3) / 2, 0),
    (0, 0, 1),
    (1, 0, 1),
    (0.5, math.sqrt(3) / 2, 1),
)
PYRAMID = (
    (-0.5, -0.5, 0),
    (0.5, -0.5, 0),
    (0.5, 0.5, 0),
    (-0.5, 0.5, 0),
    (0, 0, 1 / math.sqrt(2)),
)


def mesh_from_cells(cell_specs):
    points = []
    cells = []
    types = []
    for cell_type, cell_points in cell_specs:
        start = len(points)
        points.extend(cell_points)
        cells.append(tuple(range(start, start + len(cell_points))))
        types.append(cell_type)
    return MeshData(tuple(points), tuple(cells), tuple(types))


def make_vtk(mesh):
    point_text = "\n".join("{} {} {}".format(*point) for point in mesh.points)
    cell_text = "\n".join(f"{len(cell)} " + " ".join(map(str, cell)) for cell in mesh.cells)
    cell_size = sum(len(cell) + 1 for cell in mesh.cells)
    type_text = "\n".join(map(str, mesh.cell_types))
    return (
        "# vtk DataFile Version 3.0\nquality test\nASCII\n"
        "DATASET UNSTRUCTURED_GRID\n"
        f"POINTS {len(mesh.points)} double\n{point_text}\n"
        f"CELLS {len(mesh.cells)} {cell_size}\n{cell_text}\n"
        f"CELL_TYPES {len(mesh.cell_types)}\n{type_text}\n"
    )


class SignedVolumeTests(unittest.TestCase):
    def test_standard_cells_have_expected_positive_volumes(self):
        cases = (
            (VTK_TETRA, TETRA, 1 / 6),
            (VTK_HEXAHEDRON, HEX, 1.0),
            (VTK_WEDGE, WEDGE, math.sqrt(3) / 4),
            (VTK_PYRAMID, PYRAMID, 1 / math.sqrt(2) / 3),
        )
        for cell_type, points, expected in cases:
            with self.subTest(cell_type=cell_type):
                self.assertAlmostEqual(signed_cell_volume(cell_type, points), expected)

    def test_reversing_orientation_makes_each_volume_negative(self):
        reversals = {
            VTK_TETRA: (0, 2, 1, 3),
            VTK_HEXAHEDRON: (0, 3, 2, 1, 4, 7, 6, 5),
            VTK_WEDGE: (0, 2, 1, 3, 5, 4),
            VTK_PYRAMID: (0, 3, 2, 1, 4),
        }
        fixtures = {
            VTK_TETRA: TETRA,
            VTK_HEXAHEDRON: HEX,
            VTK_WEDGE: WEDGE,
            VTK_PYRAMID: PYRAMID,
        }
        for cell_type, order in reversals.items():
            points = tuple(fixtures[cell_type][index] for index in order)
            self.assertLess(signed_cell_volume(cell_type, points), 0.0)


class ParserTests(unittest.TestCase):
    def write_fixture(self, text):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "mesh.vtk"
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_legacy_ascii_unstructured_grid(self):
        mesh = mesh_from_cells([(VTK_WEDGE, WEDGE)])
        parsed = read_legacy_ascii_vtk(self.write_fixture(make_vtk(mesh)))
        self.assertEqual(parsed, mesh)

    def test_rejects_binary_vtk(self):
        mesh = mesh_from_cells([(VTK_TETRA, TETRA)])
        text = make_vtk(mesh).replace("ASCII", "BINARY", 1)
        with self.assertRaisesRegex(VtkFormatError, "ASCII"):
            read_legacy_ascii_vtk(self.write_fixture(text))

    def test_rejects_cell_type_count_mismatch(self):
        mesh = mesh_from_cells([(VTK_TETRA, TETRA)])
        text = make_vtk(mesh).replace("CELL_TYPES 1\n10", "CELL_TYPES 0\n")
        with self.assertRaisesRegex(VtkFormatError, "CELL_TYPES"):
            read_legacy_ascii_vtk(self.write_fixture(text))

    def test_rejects_malformed_connectivity(self):
        mesh = mesh_from_cells([(VTK_TETRA, TETRA)])
        text = make_vtk(mesh).replace("4 0 1 2 3", "5 0 1 2 3")
        with self.assertRaisesRegex(VtkFormatError, "connectivity|CELLS"):
            read_legacy_ascii_vtk(self.write_fixture(text))


class SkewnessTests(unittest.TestCase):
    def test_ideal_cells_have_zero_skewness(self):
        fixtures = {
            VTK_TETRA: REGULAR_TETRA,
            VTK_HEXAHEDRON: HEX,
            VTK_WEDGE: WEDGE,
            VTK_PYRAMID: PYRAMID,
        }
        for cell_type, points in fixtures.items():
            with self.subTest(cell_type=cell_type):
                self.assertAlmostEqual(equiangle_skewness(cell_type, points), 0.0, places=12)

    def test_distortion_increases_skewness(self):
        distorted = list(HEX)
        distorted[6] = (1.8, 1.0, 1.0)
        self.assertGreater(equiangle_skewness(VTK_HEXAHEDRON, distorted), 0.0)

    def test_zero_length_edge_returns_one(self):
        degenerate = list(TETRA)
        degenerate[1] = degenerate[0]
        self.assertEqual(equiangle_skewness(VTK_TETRA, degenerate), 1.0)


class AnalysisTests(unittest.TestCase):
    def test_bin_boundaries(self):
        expected = (
            (0.0, 0),
            (0.099999, 0),
            (0.1, 1),
            (0.9, 9),
            (1.0, 9),
            (1.000001, 10),
        )
        for value, index in expected:
            self.assertEqual(skewness_bin_index(value), index)
        self.assertEqual(len(BIN_LABELS), 11)

    def test_negative_volume_is_minimum_and_reported(self):
        reversed_tetra = tuple(TETRA[index] for index in (0, 2, 1, 3))
        report = analyze_mesh(mesh_from_cells([(VTK_TETRA, TETRA), (VTK_TETRA, reversed_tetra)]))
        self.assertEqual(report.minimum_volume_cell_ids, (1,))
        self.assertLess(report.minimum_volume, 0.0)
        self.assertEqual(report.negative_volume_cell_ids, (1,))

    def test_equal_extrema_return_all_cell_ids(self):
        report = analyze_mesh(mesh_from_cells([(VTK_HEXAHEDRON, HEX), (VTK_HEXAHEDRON, HEX)]))
        self.assertEqual(report.maximum_skewness_cell_ids, (0, 1))
        self.assertEqual(report.minimum_volume_cell_ids, (0, 1))

    def test_mixed_mesh_preserves_ids_and_skips_surface_cells(self):
        mesh = mesh_from_cells([(VTK_TETRA, TETRA)])
        mesh = MeshData(mesh.points, ((0, 1, 2),) + mesh.cells, (5, VTK_TETRA))
        report = analyze_mesh(mesh)
        self.assertEqual(report.analyzed_cell_count, 1)
        self.assertEqual(report.maximum_skewness_cell_ids, (1,))
        self.assertEqual(report.skipped_type_counts, ((5, 1),))

    def test_supported_cell_rejects_bad_connectivity(self):
        mesh = MeshData(TETRA, ((0, 1, 2),), (VTK_TETRA,))
        with self.assertRaisesRegex(VtkFormatError, "requires 4 point IDs"):
            analyze_mesh(mesh)

    def test_supported_cell_rejects_out_of_range_point_id(self):
        mesh = MeshData(TETRA, ((0, 1, 2, 99),), (VTK_TETRA,))
        with self.assertRaisesRegex(VtkFormatError, "out-of-range"):
            analyze_mesh(mesh)

    def test_mesh_without_supported_cells_is_rejected(self):
        mesh = MeshData(TETRA[:3], ((0, 1, 2),), (5,))
        with self.assertRaisesRegex(VtkFormatError, "no supported"):
            analyze_mesh(mesh)

    def test_non_finite_coordinate_is_rejected(self):
        points = list(TETRA)
        points[3] = (0, 0, math.inf)
        with self.assertRaisesRegex(VtkFormatError, "non-finite"):
            analyze_mesh(mesh_from_cells([(VTK_TETRA, points)]))


class PngTests(unittest.TestCase):
    def test_histogram_is_valid_rgb_png(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histogram.png"
            write_histogram_png(path, (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 2))
            data = path.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height, depth, color = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((width, height, depth, color), (1100, 600, 8, 2))
        self.assertGreater(len(data), 100)

    def test_histogram_rejects_wrong_bin_count(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "11"):
                write_histogram_png(Path(directory) / "x.png", (1, 2))

    def test_histogram_contains_non_white_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histogram.png"
            write_histogram_png(path, (0,) * 10 + (3,))
            data = path.read_bytes()
        idat_start = data.index(b"IDAT")
        length = struct.unpack(">I", data[idat_start - 4 : idat_start])[0]
        raw = zlib.decompress(data[idat_start + 4 : idat_start + 4 + length])
        self.assertIn(b"\x34\x78\xbe", raw)


class CliTests(unittest.TestCase):
    def test_cli_reports_results_and_writes_png(self):
        reversed_tetra = tuple(TETRA[index] for index in (0, 2, 1, 3))
        mesh = mesh_from_cells([(VTK_HEXAHEDRON, HEX), (VTK_TETRA, reversed_tetra)])
        with tempfile.TemporaryDirectory() as directory:
            vtk_path = Path(directory) / "mesh.vtk"
            png_path = Path(directory) / "custom.png"
            vtk_path.write_text(make_vtk(mesh), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main([str(vtk_path), "--output", str(png_path)])
            output = stdout.getvalue()
            self.assertEqual(code, 0)
            self.assertTrue(png_path.exists())
        self.assertIn("[0.0, 0.1):", output)
        self.assertIn("Maximum skewness cell ID(s):", output)
        self.assertIn("Minimum signed volume cell ID(s): 1", output)
        self.assertIn("Negative-volume cells: 1", output)
        self.assertIn("Negative-volume cell ID(s): 1", output)

    def test_cli_uses_default_png_name(self):
        mesh = mesh_from_cells([(VTK_HEXAHEDRON, HEX)])
        with tempfile.TemporaryDirectory() as directory:
            vtk_path = Path(directory) / "mesh.vtk"
            vtk_path.write_text(make_vtk(mesh), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = main([str(vtk_path)])
            self.assertEqual(code, 0)
            self.assertTrue((Path(directory) / "mesh_skewness_histogram.png").exists())

    def test_cli_returns_one_for_invalid_input(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(["missing.vtk"])
        self.assertEqual(code, 1)
        self.assertIn("Error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
