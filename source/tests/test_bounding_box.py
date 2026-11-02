"""Regression tests for the standard-library Python bounding-box command."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = TOOL_DIR / "bounding_box.py"
TETRA = (
    "# vtk DataFile Version 3.0\ntetra\nASCII\n"
    "DATASET UNSTRUCTURED_GRID\nPOINTS 4 double\n"
    "10 20 30\n12 20 30\n10 24 30\n10 20 36\n"
    "CELLS 1 5\n4 0 1 2 3\nCELL_TYPES 1\n10\n"
)


def parse_output(path: Path) -> tuple[list[tuple[float, ...]], list[list[int]], list[int]]:
    """Independently read the simple VTK 3.0 format emitted by the command."""
    tokens = iter(path.read_text(encoding="utf-8").splitlines()[4:])
    point_header = next(tokens).split()
    points = [tuple(map(float, next(tokens).split())) for _ in range(int(point_header[1]))]
    cell_header = next(tokens).split()
    records = [list(map(int, next(tokens).split())) for _ in range(int(cell_header[1]))]
    if any(record[0] != len(record) - 1 for record in records):
        raise AssertionError("incorrect cell size")
    if sum(map(len, records)) != int(cell_header[2]):
        raise AssertionError("incorrect CELLS integer count")
    type_header = next(tokens).split()
    cell_types = [int(next(tokens)) for _ in range(int(type_header[1]))]
    if list(tokens):
        raise AssertionError("unexpected output records")
    return points, [record[1:] for record in records], cell_types


class BoundingBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.input_path = self.directory / "input.vtk"
        self.output_path = self.directory / "output.vtk"

    def run_command(self, text: str = TETRA, scale: str = "2") -> subprocess.CompletedProcess:
        self.input_path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT),
                str(self.input_path),
                str(self.output_path),
                scale,
            ],
            cwd=self.directory,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_succeeds_without_input_files(self) -> None:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--help"],
            cwd=self.directory,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scale", result.stdout)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_appends_expected_mesh_and_centers_average_size(self) -> None:
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        points, cells, types = parse_output(self.output_path)
        self.assertEqual(len(points), 2292)
        self.assertEqual(len(cells), 4573)
        self.assertEqual(points[:4], [(10, 20, 30), (12, 20, 30), (10, 24, 30), (10, 20, 36)])
        self.assertEqual(cells[0], [0, 1, 2, 3])
        self.assertEqual(types, [10] + [5] * 4572)
        for triangle in cells[1:]:
            self.assertEqual(len(triangle), 3)
            self.assertTrue(all(4 <= index < len(points) for index in triangle))
        box = points[4:]
        minima = [min(point[axis] for point in box) for axis in range(3)]
        maxima = [max(point[axis] for point in box) for axis in range(3)]
        for axis, center in enumerate((11, 22, 33)):
            self.assertAlmostEqual((minima[axis] + maxima[axis]) / 2, center, places=10)
        self.assertAlmostEqual(sum(high - low for low, high in zip(minima, maxima)) / 3, 8)
        self.assertEqual(self.input_path.read_text(encoding="utf-8"), TETRA)
        self.assertNotIn(b"\r\n", self.output_path.read_bytes())

    def test_preserves_full_double_precision(self) -> None:
        value = "10.123456789012345"
        result = self.run_command(TETRA.replace("10 20 30", value + " 20 30", 1))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_output(self.output_path)[0][0][0], float(value))

    def test_vtk_51_offsets_and_connectivity(self) -> None:
        text = TETRA.replace("Version 3.0", "Version 5.1").replace(
            "CELLS 1 5\n4 0 1 2 3\n",
            "CELLS 2 4\nOFFSETS vtktypeint64\n0 4\nCONNECTIVITY vtktypeint64\n0 1 2 3\n",
        )
        result = self.run_command(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(parse_output(self.output_path)[1][0], [0, 1, 2, 3])

    def test_polydata_preserves_cell_order_and_derives_types(self) -> None:
        text = (
            "# vtk DataFile Version 3.0\npoly\nASCII\nDATASET POLYDATA\n"
            "POINTS 5 float\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n2 1 0\n"
            "VERTICES 2 5\n1 0\n2 0 1\n"
            "LINES 2 7\n2 0 1\n3 0 1 2\n"
            "POLYGONS 3 15\n3 0 1 2\n4 0 1 3 2\n5 0 1 4 3 2\n"
            "TRIANGLE_STRIPS 1 5\n4 1 2 3 4\n"
        )
        result = self.run_command(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, cells, types = parse_output(self.output_path)
        self.assertEqual(types[:8], [1, 2, 3, 4, 5, 9, 7, 6])
        self.assertEqual(cells[7], [1, 2, 3, 4])

    def test_vtk_51_polydata(self) -> None:
        text = (
            "# vtk DataFile Version 5.1\npoly\nASCII\nDATASET POLYDATA\n"
            "POINTS 3 double\n0 0 0\n1 0 0\n0 1 0\n"
            "POLYGONS 2 3\nOFFSETS vtktypeint64\n0 3\n"
            "CONNECTIVITY vtktypeint64\n0 1 2\n"
        )
        result = self.run_command(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, cells, types = parse_output(self.output_path)
        self.assertEqual(cells[0], [0, 1, 2])
        self.assertEqual(types[0], 5)

    def test_geometry_only_output_reports_discarded_attributes(self) -> None:
        for section in (
            "POINT_DATA 4\nSCALARS temperature double\nLOOKUP_TABLE default\n1 2 3 4\n",
            "CELL_DATA 1\nSCALARS material int\nLOOKUP_TABLE default\n7\n",
            "FIELD FieldData 1\ntime 1 1 double\n2.5\n",
        ):
            with self.subTest(section=section.split()[0]):
                result = self.run_command(TETRA + section)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("not copied", result.stderr)
                self.assertNotIn(section.split()[0], self.output_path.read_text(encoding="utf-8"))
                self.assertEqual(parse_output(self.output_path)[2][0], 10)

    def test_rejects_invalid_mesh_without_replacing_existing_output(self) -> None:
        invalid = {
            "binary": TETRA.replace("ASCII", "BINARY"),
            "structured": TETRA.replace("UNSTRUCTURED_GRID", "STRUCTURED_GRID"),
            "missing_types": TETRA.split("CELL_TYPES")[0],
            "invalid_index": TETRA.replace("4 0 1 2 3", "4 0 1 2 4"),
            "negative_index": TETRA.replace("4 0 1 2 3", "4 0 1 2 -1"),
            "integer_count": TETRA.replace("CELLS 1 5", "CELLS 1 6"),
            "nan_point": TETRA.replace("10 20 30", "nan 20 30"),
            "empty_points": TETRA.replace(
                "POINTS 4 double\n10 20 30\n12 20 30\n10 24 30\n10 20 36", "POINTS 0 double"
            ),
            "zero_size": TETRA.replace("12 20 30", "10 20 30")
            .replace("10 24 30", "10 20 30")
            .replace("10 20 36", "10 20 30"),
        }
        for name, text in invalid.items():
            with self.subTest(name=name):
                self.output_path.write_text("existing output", encoding="utf-8")
                result = self.run_command(text)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.output_path.read_text(encoding="utf-8"), "existing output")

    def test_rejects_bad_offsets(self) -> None:
        for offsets in ("1 4", "0 5", "0 -1"):
            with self.subTest(offsets=offsets):
                text = TETRA.replace(
                    "CELLS 1 5\n4 0 1 2 3\n",
                    f"CELLS 2 4\nOFFSETS vtktypeint64\n{offsets}\nCONNECTIVITY vtktypeint64\n0 1 2 3\n",
                )
                result = self.run_command(text)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output_path.exists())

    def test_invalid_scale_is_rejected(self) -> None:
        for scale in ("0", "-1", "nan", "inf", "1junk", "1e309", "1e308"):
            with self.subTest(scale=scale):
                result = self.run_command(scale=scale)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.output_path.exists())

    def test_same_input_output_is_rejected(self) -> None:
        self.input_path.write_text(TETRA, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT),
                str(self.input_path),
                str(self.input_path),
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different", result.stderr)
        self.assertEqual(self.input_path.read_text(encoding="utf-8"), TETRA)

    def test_missing_input_and_unwritable_output_fail_cleanly(self) -> None:
        for input_path, output_path in (
            (self.directory / "missing.vtk", self.output_path),
            (self.input_path, self.directory / "absent" / "output.vtk"),
        ):
            with self.subTest(input_path=input_path, output_path=output_path):
                self.input_path.write_text(TETRA, encoding="utf-8")
                result = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(SCRIPT),
                        str(input_path),
                        str(output_path),
                        "2",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)

    def test_template_matches_verified_geometry(self) -> None:
        self.assertTrue((TOOL_DIR / "resources" / "bounding_box_template.json").is_file())
        template = json.loads(
            (TOOL_DIR / "resources" / "bounding_box_template.json").read_text(encoding="utf-8")
        )
        points = template["points"]
        triangles = template["triangles"]
        # Recorded after comparing every point and triangle against the original C++ template.
        geometry = json.dumps([points, triangles], separators=(",", ":")).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(geometry).hexdigest(),
            "1ed179748f2347affdce661983fd90403b76c9e636bf70daca3cb5a905fc28c6",
        )
        self.assertEqual(len(points), 2288)
        self.assertEqual(len(triangles), 4572)
        self.assertTrue(all(math.isfinite(value) for point in points for value in point))

    def test_main_returns_status_instead_of_exiting(self) -> None:
        self.assertTrue(SCRIPT.is_file())
        specification = importlib.util.spec_from_file_location(
            "add_bounding_box_under_test", SCRIPT
        )
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        self.addCleanup(sys.modules.pop, specification.name, None)
        specification.loader.exec_module(module)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["--help"]), 0)
            self.assertNotEqual(module.main([]), 0)


if __name__ == "__main__":
    unittest.main()
