"""Regression tests for the standalone FACET converter."""

from __future__ import annotations

import contextlib
import io
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import meshio

TOOL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = TOOL_DIR / "mesh_convert.py"
MINIMAL_FACET = """FACET FILE V3.0
1
Grid
0, 0.00 0.00 0.00 0.00
3
0 0 0
1.2345678901234567 0 0
0 1 0
1
Triangles
1 3
1 2 3 0 0007 99
"""


class FacetToVtkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.work_dir = Path(self.temp_dir.name)
        self.input_path = self.work_dir / "input.facet"
        self.output_path = self.work_dir / "output.vtk"
        self.input_path.write_text(MINIMAL_FACET, encoding="utf-8")

    def run_tool(self, *args: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-B",
                str(SCRIPT),
                *(str(arg) for arg in args),
                "--input-format",
                "FACET",
                "--output-format",
                "VTK",
            ],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_multiple_faces_preserve_connectivity_and_ids(self) -> None:
        content = MINIMAL_FACET.replace(
            "1 3\n1 2 3 0 0007 99", "2 3\n1 2 3 0 0007 99\n3 2 1 0 0009 99"
        )
        self.input_path.write_text(content, encoding="utf-8")
        original = self.input_path.read_bytes()
        result = self.run_tool(self.input_path, self.output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        mesh = meshio.read(self.output_path)
        self.assertEqual(len(mesh.points), 3)
        self.assertEqual(mesh.cells_dict["triangle"].tolist(), [[0, 1, 2], [2, 1, 0]])
        self.assertEqual(mesh.cell_data_dict["face_id"]["triangle"].tolist(), [7, 9])
        self.assertEqual(original, self.input_path.read_bytes())

    def test_minimal_mesh_preserves_precision_and_uses_fifth_column_for_face_id(self) -> None:
        result = self.run_tool(self.input_path, self.output_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        mesh = meshio.read(self.output_path)
        self.assertEqual(mesh.points[1, 0], 1.2345678901234567)
        self.assertEqual(mesh.cells_dict["triangle"].tolist(), [[0, 1, 2]])
        self.assertEqual(mesh.cell_data_dict["face_id"]["triangle"].tolist(), [7])

    def test_help_succeeds_without_reading_input(self) -> None:
        result = self.run_tool("missing.facet", "missing.vtk", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--input-format", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.work_dir / "missing.vtk").exists())

    def test_invalid_input_reports_error_and_preserves_existing_output(self) -> None:
        invalid_inputs = [
            "",
            MINIMAL_FACET.replace("\n3\n", "\n-1\n", 1),
            MINIMAL_FACET.replace("0 0 0\n", "0 invalid 0\n", 1),
            MINIMAL_FACET.replace("0 0 0\n", "nan 0 0\n", 1),
            MINIMAL_FACET.replace("1 2 3 0 0007 99", "0 2 3 0 0007 99"),
            MINIMAL_FACET.replace("1 2 3 0 0007 99", "1 2 4 0 0007 99"),
            MINIMAL_FACET.replace("1 2 3 0 0007 99", "1 2 3 0 invalid 99"),
            MINIMAL_FACET.replace("1 2 3 0 0007 99\n", ""),
            MINIMAL_FACET.replace("1 3\n", "1 4\n"),
            MINIMAL_FACET.replace("V3.0\n1\n", "V3.0\n2\n"),
            MINIMAL_FACET.replace("1\nTriangles", "2\nTriangles"),
        ]
        for content in invalid_inputs:
            with self.subTest(content=content):
                self.input_path.write_text(content, encoding="utf-8")
                self.output_path.write_text("existing output", encoding="utf-8")
                original = self.input_path.read_bytes()
                result = self.run_tool(self.input_path, self.output_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.output_path.read_text(encoding="utf-8"), "existing output")
                self.assertEqual(self.input_path.read_bytes(), original)

    def test_same_path_is_rejected_without_changing_input(self) -> None:
        original = self.input_path.read_bytes()
        result = self.run_tool(self.input_path, self.input_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different files", result.stderr)
        self.assertEqual(self.input_path.read_bytes(), original)

    def test_hard_link_output_is_rejected_without_changing_input(self) -> None:
        original = self.input_path.read_bytes()
        try:
            os.link(self.input_path, self.output_path)
        except OSError as error:
            self.skipTest(f"Hard links unavailable: {error}")
        result = self.run_tool(self.input_path, self.output_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different files", result.stderr)
        self.assertEqual(self.input_path.read_bytes(), original)

    def test_missing_input_and_invalid_output_path_report_error(self) -> None:
        for input_path, output_path in [
            (self.work_dir / "missing.facet", self.output_path),
            (self.input_path, self.work_dir),
        ]:
            with self.subTest(input_path=input_path, output_path=output_path):
                result = self.run_tool(input_path, output_path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Error:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(output_path.is_file())

    def test_main_can_be_called_with_argument_list(self) -> None:
        self.assertTrue(SCRIPT.is_file(), "The Python converter must exist")
        namespace = runpy.run_path(str(SCRIPT))
        with contextlib.redirect_stdout(io.StringIO()):
            result = namespace["main"](
                [
                    str(self.input_path),
                    str(self.output_path),
                    "--input-format",
                    "FACET",
                    "--output-format",
                    "VTK",
                ]
            )
        self.assertEqual(result, 0)
        self.assertTrue(self.output_path.is_file())


if __name__ == "__main__":
    unittest.main()
