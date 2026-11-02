"""Append the retained triangular bounding-box template to a legacy VTK mesh.

The output preserves input points, connectivity, and cell types, and is written
as ASCII VTK 3.0 UNSTRUCTURED_GRID. Point/cell attributes and
FIELD data are omitted; the CLI reports this explicitly when such data occurs.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence, TextIO

Point = tuple[float, float, float]
Cell = tuple[int, ...]
TEMPLATE_PATH = Path(__file__).resolve().parent / "resources" / "bounding_box_template.json"
POLYDATA_SECTIONS = {"VERTICES", "LINES", "POLYGONS", "TRIANGLE_STRIPS"}


# Mesh representation and VTK parsing.


@dataclass
class VtkMesh:
    """Geometry retained when converting legacy VTK to an unstructured grid."""

    points: list[Point]
    cells: list[Cell]
    cell_types: list[int]
    attributes_omitted: bool = False


def read_token(tokens: Iterator[str], name: str) -> str:
    """Read one token with a useful error for truncated input."""
    try:
        return next(tokens)
    except StopIteration as error:
        raise ValueError(f"unexpected end of {name}") from error


def parse_integer(token: str, name: str) -> int:
    if re.fullmatch(r"[+-]?[0-9]+", token) is None:
        raise ValueError(f"invalid {name}: {token!r}")
    return int(token)


def read_integer(tokens: Iterator[str], name: str) -> int:
    return parse_integer(read_token(tokens, name), name)


def read_size(tokens: Iterator[str], name: str) -> int:
    value = read_integer(tokens, name)
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def read_coordinate(tokens: Iterator[str]) -> float:
    token = read_token(tokens, "POINTS data")
    try:
        value = float(token)
    except ValueError as error:
        raise ValueError(f"invalid point coordinate: {token!r}") from error
    if "_" in token or not math.isfinite(value):
        raise ValueError("point coordinates must be finite numbers")
    return value


def read_cells(tokens: Iterator[str], count: int, integer_count: int) -> list[Cell]:
    """Read VTK 3.x cell records or VTK 5.1 OFFSETS/CONNECTIVITY arrays."""
    if count == 0:
        if integer_count != 0:
            raise ValueError("CELLS counts are inconsistent")
        return []
    first = read_token(tokens, "CELLS data")
    if first.upper() == "OFFSETS":
        read_token(tokens, "OFFSETS scalar type")
        offsets = [read_size(tokens, "cell offset") for _ in range(count)]
        if offsets[0] != 0 or offsets[-1] != integer_count:
            raise ValueError("OFFSETS range does not match CONNECTIVITY count")
        if any(right < left for left, right in zip(offsets, offsets[1:])):
            raise ValueError("OFFSETS must be nondecreasing")
        if read_token(tokens, "CONNECTIVITY section").upper() != "CONNECTIVITY":
            raise ValueError("missing CONNECTIVITY section after OFFSETS")
        read_token(tokens, "CONNECTIVITY scalar type")
        connectivity = [read_integer(tokens, "cell point index") for _ in range(integer_count)]
        return [tuple(connectivity[left:right]) for left, right in zip(offsets, offsets[1:])]

    cells = []
    consumed = 0
    for cell_index in range(count):
        node_count = (
            parse_integer(first, "cell node count")
            if cell_index == 0
            else read_size(tokens, "cell node count")
        )
        if node_count < 0 or consumed + node_count + 1 > integer_count:
            raise ValueError("CELLS integer count is inconsistent")
        cells.append(tuple(read_integer(tokens, "cell point index") for _ in range(node_count)))
        consumed += node_count + 1
    if consumed != integer_count:
        raise ValueError("CELLS integer count is inconsistent")
    return cells


def polydata_cell_type(section: str, node_count: int) -> int:
    if section == "VERTICES":
        return 1 if node_count == 1 else 2
    if section == "LINES":
        return 3 if node_count == 2 else 4
    if section == "TRIANGLE_STRIPS":
        return 6
    return {3: 5, 4: 9}.get(node_count, 7)


def validate_mesh(mesh: VtkMesh) -> None:
    if not mesh.points:
        raise ValueError("mesh contains no points")
    if len(mesh.cells) != len(mesh.cell_types):
        raise ValueError("CELL_TYPES count does not match CELLS count")
    for point in mesh.points:
        if len(point) != 3 or not all(math.isfinite(value) for value in point):
            raise ValueError("point coordinates must be finite triples")
    for cell in mesh.cells:
        if any(index < 0 or index >= len(mesh.points) for index in cell):
            raise ValueError("cell point index is out of range")


def read_legacy_vtk(path: Path) -> VtkMesh:
    """Read ASCII UNSTRUCTURED_GRID/POLYDATA without retaining attribute arrays."""
    with Path(path).open("r", encoding="utf-8-sig") as source:
        if not source.readline().strip().startswith("# vtk DataFile"):
            raise ValueError("not a legacy VTK file")
        if not source.readline():
            raise ValueError("missing VTK title")
        if source.readline().strip().upper() != "ASCII":
            raise ValueError("only ASCII legacy VTK is supported")
        tokens = (token for line in source for token in line.split())
        if read_token(tokens, "DATASET declaration").upper() != "DATASET":
            raise ValueError("missing DATASET declaration")
        dataset = read_token(tokens, "DATASET type").upper()
        if dataset not in {"UNSTRUCTURED_GRID", "POLYDATA"}:
            raise ValueError(f"unsupported DATASET type: {dataset}")

        mesh = VtkMesh([], [], [])
        have_points = have_cells = have_cell_types = False
        for keyword in tokens:
            keyword = keyword.upper()
            if keyword in {"POINT_DATA", "CELL_DATA", "FIELD"}:
                mesh.attributes_omitted = True
                break
            if keyword == "POINTS":
                if have_points:
                    raise ValueError("duplicate POINTS section")
                count = read_size(tokens, "POINTS count")
                read_token(tokens, "POINTS scalar type")
                mesh.points = [
                    (read_coordinate(tokens), read_coordinate(tokens), read_coordinate(tokens))
                    for _ in range(count)
                ]
                have_points = True
            elif dataset == "UNSTRUCTURED_GRID" and keyword == "CELLS":
                count = read_size(tokens, "CELLS count")
                integer_count = read_size(tokens, "CELLS integer count")
                mesh.cells.extend(read_cells(tokens, count, integer_count))
                have_cells = True
            elif dataset == "UNSTRUCTURED_GRID" and keyword == "CELL_TYPES":
                count = read_size(tokens, "CELL_TYPES count")
                mesh.cell_types = [read_integer(tokens, "cell type") for _ in range(count)]
                have_cell_types = True
            elif dataset == "POLYDATA" and keyword in POLYDATA_SECTIONS:
                count = read_size(tokens, "POLYDATA cell count")
                integer_count = read_size(tokens, "POLYDATA integer count")
                cells = read_cells(tokens, count, integer_count)
                mesh.cells.extend(cells)
                mesh.cell_types.extend(polydata_cell_type(keyword, len(cell)) for cell in cells)
                have_cells = have_cell_types = True
            else:
                raise ValueError(f"unsupported or misplaced VTK section: {keyword}")
        if not have_points:
            raise ValueError("missing POINTS section")
        if dataset == "UNSTRUCTURED_GRID" and not (have_cells and have_cell_types):
            raise ValueError("UNSTRUCTURED_GRID requires CELLS and CELL_TYPES")
    validate_mesh(mesh)
    return mesh


# Bounding-box construction.


def load_template() -> VtkMesh:
    """Load the verified points and triangles of the original bounding-box template."""
    with TEMPLATE_PATH.open("r", encoding="utf-8") as source:
        data = json.load(source)
    try:
        points = [tuple(point) for point in data["points"]]
        triangles = [tuple(triangle) for triangle in data["triangles"]]
        if len(points) != 2288 or len(triangles) != 4572:
            raise ValueError("bounding-box template must contain 2288 points and 4572 triangles")
        if any(
            len(cell) != 3 or any(type(index) is not int for index in cell) for cell in triangles
        ):
            raise ValueError("bounding-box template requires integer triangle indices")
        mesh = VtkMesh(points, triangles, [5] * len(triangles))
        validate_mesh(mesh)
    except (KeyError, TypeError) as error:
        raise ValueError("invalid bounding-box template") from error
    return mesh


def bounding_limits(points: Sequence[Point]) -> tuple[Point, Point]:
    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    return minimum, maximum


def add_bounding_box(mesh: VtkMesh, scale: float) -> VtkMesh:
    """Append a box centered on the input with mean side length multiplied by scale.

    This is uniform scaling of the bundled surface, not independent per-axis
    expansion. In particular, a scale of one need not enclose an elongated mesh.
    """
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be finite and greater than zero")
    validate_mesh(mesh)
    template = load_template()
    input_min, input_max = bounding_limits(mesh.points)
    box_min, box_max = bounding_limits(template.points)
    input_mean = sum(high - low for low, high in zip(input_min, input_max)) / 3
    box_mean = sum(high - low for low, high in zip(box_min, box_max)) / 3
    if not math.isfinite(input_mean) or input_mean <= 0:
        raise ValueError("input mesh has zero or invalid characteristic size")
    if not math.isfinite(box_mean) or box_mean <= 0:
        raise ValueError("bundled bounding box has zero or invalid characteristic size")
    input_center = tuple((low + high) / 2 for low, high in zip(input_min, input_max))
    box_center = tuple((low + high) / 2 for low, high in zip(box_min, box_max))
    ratio = input_mean * scale / box_mean
    if not math.isfinite(ratio):
        raise ValueError("scaled bounding box exceeds the finite coordinate range")
    box_points = [
        tuple((point[axis] - box_center[axis]) * ratio + input_center[axis] for axis in range(3))
        for point in template.points
    ]
    offset = len(mesh.points)
    triangles = [tuple(index + offset for index in cell) for cell in template.cells]
    result = VtkMesh(
        mesh.points + box_points,
        mesh.cells + triangles,
        mesh.cell_types + template.cell_types,
        mesh.attributes_omitted,
    )
    validate_mesh(result)
    return result


# VTK output.


def write_geometry(output: TextIO, mesh: VtkMesh) -> None:
    output.write(
        "# vtk DataFile Version 3.0\nMesh with bounding box\nASCII\n"
        f"DATASET UNSTRUCTURED_GRID\nPOINTS {len(mesh.points)} double\n"
    )
    for point in mesh.points:
        output.write(" ".join(format(coordinate, ".17g") for coordinate in point) + "\n")
    integer_count = sum(len(cell) + 1 for cell in mesh.cells)
    output.write(f"CELLS {len(mesh.cells)} {integer_count}\n")
    for cell in mesh.cells:
        output.write(" ".join(map(str, (len(cell), *cell))) + "\n")
    output.write(f"CELL_TYPES {len(mesh.cell_types)}\n")
    for cell_type in mesh.cell_types:
        output.write(f"{cell_type}\n")


def write_legacy_vtk(path: Path, mesh: VtkMesh) -> None:
    """Write complete geometry before replacing the destination file."""
    validate_mesh(mesh)
    path = Path(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            write_geometry(output, mesh)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


# Command-line interface.


def positive_scale(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError("scale must be a finite positive number") from error
    if "_" in text or not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("scale must be a finite positive number")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append a triangulated bounding box to an ASCII legacy VTK mesh.",
        epilog=(
            "Supports UNSTRUCTURED_GRID and POLYDATA, including VTK 5.1 connectivity. "
            "Preserves geometry and cell types; point/cell attributes and FIELD data are not copied."
        ),
    )
    parser.add_argument("input", type=Path, help="input .vtk file")
    parser.add_argument("output", type=Path, help="output .vtk file (must differ from input)")
    parser.add_argument(
        "scale",
        type=positive_scale,
        help="output box mean side length / input bounding-box mean side length (positive)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        if arguments.input.resolve() == arguments.output.resolve() or (
            arguments.output.exists() and arguments.input.samefile(arguments.output)
        ):
            raise ValueError("input and output must be different files")
        mesh = add_bounding_box(read_legacy_vtk(arguments.input), arguments.scale)
        write_legacy_vtk(arguments.output, mesh)
        if mesh.attributes_omitted:
            print("Warning: point/cell attributes and FIELD data were not copied.", file=sys.stderr)
        print(f"Wrote {len(mesh.points)} points and {len(mesh.cells)} cells to {arguments.output}")
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
