"""Exercise the delivered EXE, including all 81 mesh format combinations."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

import meshio
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FORMATS = ("VTK", "VTU", "NAS", "PLY", "STL", "OBJ", "MSH", "CGNS", "FACET")
SURFACE = """# vtk DataFile Version 3.0
tetra surface
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 double
0 0 0
1 0 0
0 1 0
0 0 1
CELLS 4 16
3 0 2 1
3 0 1 3
3 1 2 3
3 2 0 3
CELL_TYPES 4
5
5
5
5
"""
VOLUME = SURFACE.split("CELLS")[0] + "CELLS 1 5\n4 0 1 2 3\nCELL_TYPES 1\n10\n"


def read_output(path, fmt):
    if fmt in {"CGNS", "FACET"}:
        spec = importlib.util.spec_from_file_location("smoke_converter", ROOT / "mesh_convert.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if fmt == "CGNS":
            return module.read_cgns(path)
        points, cells, _ = module.read_facet(path)
        return meshio.Mesh(points, [("triangle", cells)])
    return meshio.read(path, file_format={"NAS": "nastran", "MSH": "gmsh"}.get(fmt, fmt.lower()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument(
        "--quick", action="store_true", help="only seed formats and other functions"
    )
    args = parser.parse_args()
    executable = args.exe.resolve()
    env = os.environ.copy()
    # Deliberately omit Python and ANSYS from PATH: the EXE must carry its runtime.
    windows = Path(env.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    with tempfile.TemporaryDirectory(prefix="exe 中文 smoke ", dir=ROOT / "tests") as folder:
        directory = Path(folder)
        source = directory / "surface.vtk"
        source.write_text(SURFACE, encoding="ascii")
        count = 0

        def run(source, target, input_format, output_format, function="convert"):
            nonlocal count
            command = [str(executable), function, str(source)]
            if function == "convert":
                command += [
                    str(target),
                    "--input-format",
                    input_format,
                    "--output-format",
                    output_format,
                ]
            elif function == "quality":
                command += ["--output", str(target)]
            elif function == "surface-check":
                command += ["--log-path", str(target)]
            else:
                command += [str(target), "2.0"]
            result = subprocess.run(
                command,
                cwd=directory,
                env=env,
                timeout=90,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
            detail = result.stdout + result.stderr
            if result.returncode or not target.is_file():
                raise AssertionError(
                    f"{function}: {input_format}->{output_format}, exit={result.returncode}\n{detail}"
                )
            count += 1

        inputs = {"VTK": source}
        for fmt in FORMATS:
            if fmt == "VTK":
                continue
            inputs[fmt] = directory / ("seed." + fmt.lower())
            run(source, inputs[fmt], "VTK", fmt)
            print(f"EXE seed VTK -> {fmt}: OK", flush=True)
        if not args.quick:
            expected_area = (3 + np.sqrt(3)) / 2
            for input_format in FORMATS:
                for output_format in FORMATS:
                    target = (
                        directory / f"{input_format}_to_{output_format}.{output_format.lower()}"
                    )
                    run(inputs[input_format], target, input_format, output_format)
                    mesh = read_output(target, output_format)
                    triangles = np.concatenate([c.data for c in mesh.cells if c.type == "triangle"])
                    assert len(triangles) == 4, (input_format, output_format, len(triangles))
                    points = mesh.points[triangles]
                    area = (
                        np.linalg.norm(
                            np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]),
                            axis=1,
                        ).sum()
                        / 2
                    )
                    np.testing.assert_allclose(area, expected_area, rtol=1e-6)
                print(f"EXE {input_format} -> all 9 formats: OK", flush=True)
        volume = directory / "volume.vtk"
        volume.write_text(VOLUME, encoding="ascii")
        normalized = directory / "normalized.vtk"
        run(volume, normalized, "VTK", "VTK")
        run(normalized, directory / "quality.png", "VTK", "PNG", "quality")
        cgns_volume = directory / "volume.cgns"
        run(volume, cgns_volume, "VTK", "CGNS")
        roundtrip = directory / "volume-returned.vtu"
        run(cgns_volume, roundtrip, "CGNS", "VTU")
        np.testing.assert_array_equal(
            read_output(roundtrip, "VTU").cells_dict["tetra"], [[0, 1, 2, 3]]
        )
        polydata = directory / "polydata.vtk"
        polydata.write_text(
            SURFACE.split("CELL_TYPES")[0]
            .replace("UNSTRUCTURED_GRID", "POLYDATA")
            .replace("CELLS", "POLYGONS"),
            encoding="ascii",
        )
        poly_stl = directory / "polydata.stl"
        run(polydata, poly_stl, "VTK", "STL")
        assert len(read_output(poly_stl, "STL").cells_dict["triangle"]) == 4
        run(source, directory / "boxed.vtk", "VTK", "VTK", "bounding-box")
        run(source, directory / "surface.log", "VTK", "LOG", "surface-check")
        print(f"EXE smoke passed: {count} jobs; PATH excluded Python and ANSYS.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
