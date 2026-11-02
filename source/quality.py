#!/usr/bin/env python3
"""Report signed volumes and skewness of Legacy ASCII VTK volume meshes."""

from __future__ import annotations

import argparse
import binascii
import math
import struct
import sys
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

VTK_TETRA = 10
VTK_HEXAHEDRON = 12
VTK_WEDGE = 13
VTK_PYRAMID = 14

Point = tuple[float, float, float]
Cell = tuple[int, ...]
Face = tuple[int, ...]
Tet = tuple[int, int, int, int]


# Mesh representation and VTK parsing.


class VtkFormatError(ValueError):
    pass


@dataclass(frozen=True)
class MeshData:
    points: tuple[Point, ...]
    cells: tuple[Cell, ...]
    cell_types: tuple[int, ...]


def _subtract(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def signed_tetra_volume(a: Point, b: Point, c: Point, d: Point) -> float:
    ab = _subtract(b, a)
    ac = _subtract(c, a)
    ad = _subtract(d, a)
    cross_x = ac[1] * ad[2] - ac[2] * ad[1]
    cross_y = ac[2] * ad[0] - ac[0] * ad[2]
    cross_z = ac[0] * ad[1] - ac[1] * ad[0]
    return (ab[0] * cross_x + ab[1] * cross_y + ab[2] * cross_z) / 6.0


def _find_token(tokens: Sequence[str], name: str) -> int:
    try:
        return tokens.index(name)
    except ValueError as error:
        raise VtkFormatError(f"Missing required {name} section") from error


def _parse_integer(value: str, context: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise VtkFormatError(f"Invalid integer in {context}: {value}") from error


def _parse_float(value: str, context: str) -> float:
    try:
        return float(value)
    except ValueError as error:
        raise VtkFormatError(f"Invalid number in {context}: {value}") from error


def read_legacy_ascii_vtk(path: Path) -> MeshData:
    input_path = Path(path)
    try:
        text = input_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VtkFormatError(f"Unable to read VTK file: {input_path}") from error

    lines = text.splitlines()
    if len(lines) < 4 or not lines[0].lstrip().startswith("# vtk DataFile"):
        raise VtkFormatError("Input is not a Legacy VTK file")
    if lines[2].strip().upper() != "ASCII":
        raise VtkFormatError("Only Legacy VTK ASCII files are supported")
    if lines[3].strip().upper() != "DATASET UNSTRUCTURED_GRID":
        raise VtkFormatError("DATASET must be UNSTRUCTURED_GRID")

    tokens = "\n".join(lines[4:]).split()
    points_index = _find_token(tokens, "POINTS")
    cells_index = _find_token(tokens, "CELLS")
    types_index = _find_token(tokens, "CELL_TYPES")
    if not points_index < cells_index < types_index:
        raise VtkFormatError("POINTS, CELLS, and CELL_TYPES sections are out of order")

    try:
        point_count = _parse_integer(tokens[points_index + 1], "POINTS")
        point_start = points_index + 3
        point_end = point_start + point_count * 3
        point_values = tokens[point_start:point_end]
        if len(point_values) != point_count * 3 or point_end > cells_index:
            raise VtkFormatError("POINTS data count does not match its declaration")
        points = tuple(
            (
                _parse_float(point_values[offset], "POINTS"),
                _parse_float(point_values[offset + 1], "POINTS"),
                _parse_float(point_values[offset + 2], "POINTS"),
            )
            for offset in range(0, len(point_values), 3)
        )

        cell_count = _parse_integer(tokens[cells_index + 1], "CELLS")
        declared_cell_size = _parse_integer(tokens[cells_index + 2], "CELLS")
        cursor = cells_index + 3
        cell_data_start = cursor
        parsed_cells = []
        for cell_id in range(cell_count):
            if cursor >= types_index:
                raise VtkFormatError(f"CELLS data ended before cell ID {cell_id} was complete")
            node_count = _parse_integer(tokens[cursor], "CELLS")
            cursor += 1
            node_end = cursor + node_count
            if node_count < 0 or node_end > types_index:
                raise VtkFormatError(f"Invalid connectivity for cell ID {cell_id}")
            parsed_cells.append(
                tuple(_parse_integer(value, "CELLS") for value in tokens[cursor:node_end])
            )
            cursor = node_end
        if cursor - cell_data_start != declared_cell_size or cursor != types_index:
            raise VtkFormatError("CELLS data size does not match its declaration")

        type_count = _parse_integer(tokens[types_index + 1], "CELL_TYPES")
        type_start = types_index + 2
        type_end = type_start + type_count
        type_values = tokens[type_start:type_end]
        if len(type_values) != type_count:
            raise VtkFormatError("CELL_TYPES data count does not match its declaration")
        cell_types = tuple(_parse_integer(value, "CELL_TYPES") for value in type_values)
    except IndexError as error:
        raise VtkFormatError("A required VTK section is incomplete") from error

    if len(parsed_cells) != len(cell_types):
        raise VtkFormatError("CELL_TYPES count must equal the number of cells in CELLS")
    return MeshData(points=points, cells=tuple(parsed_cells), cell_types=cell_types)


# Cell definitions and quality metrics.


@dataclass(frozen=True)
class CellDefinition:
    name: str
    node_count: int
    faces: tuple[Face, ...]
    tetrahedra: tuple[Tet, ...]


CELL_DEFINITIONS: dict[int, CellDefinition] = {
    VTK_TETRA: CellDefinition(
        "tetra",
        4,
        ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)),
        ((0, 1, 2, 3),),
    ),
    VTK_HEXAHEDRON: CellDefinition(
        "hexahedron",
        8,
        (
            (0, 3, 2, 1),
            (4, 5, 6, 7),
            (0, 1, 5, 4),
            (1, 2, 6, 5),
            (2, 3, 7, 6),
            (3, 0, 4, 7),
        ),
        (
            (0, 1, 3, 4),
            (1, 2, 3, 6),
            (1, 4, 5, 6),
            (3, 4, 6, 7),
            (1, 3, 4, 6),
        ),
    ),
    VTK_WEDGE: CellDefinition(
        "wedge",
        6,
        (
            (0, 2, 1),
            (3, 4, 5),
            (0, 1, 4, 3),
            (1, 2, 5, 4),
            (2, 0, 3, 5),
        ),
        ((0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5)),
    ),
    VTK_PYRAMID: CellDefinition(
        "pyramid",
        5,
        ((0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
        ((0, 1, 2, 4), (0, 2, 3, 4)),
    ),
}


BIN_LABELS = tuple(
    [f"[{index / 10:.1f}, {(index + 1) / 10:.1f})" for index in range(9)] + ["[0.9, 1.0]", "> 1.0"]
)


@dataclass(frozen=True)
class CellQuality:
    cell_id: int
    cell_type: int
    skewness: float
    signed_volume: float


@dataclass(frozen=True)
class QualityReport:
    total_cell_count: int
    analyzed_cell_count: int
    type_counts: tuple[tuple[int, int], ...]
    skipped_type_counts: tuple[tuple[int, int], ...]
    bins: tuple[int, ...]
    maximum_skewness: float
    maximum_skewness_cell_ids: tuple[int, ...]
    minimum_volume: float
    minimum_volume_cell_ids: tuple[int, ...]
    negative_volume_cell_ids: tuple[int, ...]


def signed_cell_volume(cell_type: int, points: Sequence[Point]) -> float:
    definition = CELL_DEFINITIONS.get(cell_type)
    if definition is None:
        raise VtkFormatError(f"Unsupported volume cell type: {cell_type}")
    if len(points) != definition.node_count:
        raise VtkFormatError(
            f"VTK {definition.name} requires {definition.node_count} points, received {len(points)}"
        )
    volume = sum(
        signed_tetra_volume(*(points[index] for index in tetrahedron))
        for tetrahedron in definition.tetrahedra
    )
    if not math.isfinite(volume):
        raise VtkFormatError("Cell volume is not finite")
    return volume


def face_corner_angle(a: Point, vertex: Point, b: Point) -> float | None:
    first = tuple(a[index] - vertex[index] for index in range(3))
    second = tuple(b[index] - vertex[index] for index in range(3))
    first_length = math.sqrt(sum(value * value for value in first))
    second_length = math.sqrt(sum(value * value for value in second))
    if first_length == 0.0 or second_length == 0.0:
        return None
    cosine = sum(first[index] * second[index] for index in range(3)) / (
        first_length * second_length
    )
    cosine = max(-1.0, min(1.0, cosine))
    angle = math.degrees(math.acos(cosine))
    return angle if math.isfinite(angle) else None


def _angle_skewness(angle: float, ideal: float) -> float:
    if angle >= ideal:
        return (angle - ideal) / (180.0 - ideal)
    return (ideal - angle) / ideal


def equiangle_skewness(cell_type: int, points: Sequence[Point]) -> float:
    definition = CELL_DEFINITIONS.get(cell_type)
    if definition is None:
        raise VtkFormatError(f"Unsupported volume cell type: {cell_type}")
    if len(points) != definition.node_count:
        raise VtkFormatError(
            f"VTK {definition.name} requires {definition.node_count} points, received {len(points)}"
        )
    if signed_cell_volume(cell_type, points) == 0.0:
        return 1.0

    skewness_values = []
    for face in definition.faces:
        ideal = 60.0 if len(face) == 3 else 90.0
        for offset, vertex_id in enumerate(face):
            angle = face_corner_angle(
                points[face[offset - 1]],
                points[vertex_id],
                points[face[(offset + 1) % len(face)]],
            )
            if angle is None:
                return 1.0
            skewness_values.append(_angle_skewness(angle, ideal))

    result = max(skewness_values)
    if not math.isfinite(result) or result < 0.0:
        raise VtkFormatError("Cell skewness is not a finite non-negative value")
    return result


def skewness_bin_index(value: float) -> int:
    if not math.isfinite(value) or value < 0.0:
        raise VtkFormatError("Skewness must be finite and non-negative")
    if value > 1.0:
        return 10
    if value >= 0.9:
        return 9
    return int(value * 10.0)


def analyze_mesh(mesh: MeshData) -> QualityReport:
    qualities = []
    supported_counts = Counter()
    skipped_counts = Counter()
    point_count = len(mesh.points)

    for cell_id, (cell, cell_type) in enumerate(zip(mesh.cells, mesh.cell_types)):
        definition = CELL_DEFINITIONS.get(cell_type)
        if definition is None:
            skipped_counts[cell_type] += 1
            continue
        if len(cell) != definition.node_count:
            raise VtkFormatError(
                f"VTK {definition.name} cell ID {cell_id} requires "
                f"{definition.node_count} point IDs"
            )
        for point_id in cell:
            if not 0 <= point_id < point_count:
                raise VtkFormatError(f"Cell ID {cell_id} contains out-of-range point ID {point_id}")
        cell_points = tuple(mesh.points[point_id] for point_id in cell)
        if any(not math.isfinite(value) for point in cell_points for value in point):
            raise VtkFormatError(f"Cell ID {cell_id} contains a non-finite coordinate")
        qualities.append(
            CellQuality(
                cell_id,
                cell_type,
                equiangle_skewness(cell_type, cell_points),
                signed_cell_volume(cell_type, cell_points),
            )
        )
        supported_counts[cell_type] += 1

    if not qualities:
        raise VtkFormatError("The VTK file contains no supported volume cells")

    bins = [0] * len(BIN_LABELS)
    for quality in qualities:
        bins[skewness_bin_index(quality.skewness)] += 1
    maximum_skewness = max(item.skewness for item in qualities)
    minimum_volume = min(item.signed_volume for item in qualities)

    return QualityReport(
        total_cell_count=len(mesh.cells),
        analyzed_cell_count=len(qualities),
        type_counts=tuple(sorted(supported_counts.items())),
        skipped_type_counts=tuple(sorted(skipped_counts.items())),
        bins=tuple(bins),
        maximum_skewness=maximum_skewness,
        maximum_skewness_cell_ids=tuple(
            item.cell_id for item in qualities if item.skewness == maximum_skewness
        ),
        minimum_volume=minimum_volume,
        minimum_volume_cell_ids=tuple(
            item.cell_id for item in qualities if item.signed_volume == minimum_volume
        ),
        negative_volume_cell_ids=tuple(
            item.cell_id for item in qualities if item.signed_volume < 0.0
        ),
    )


# PNG histogram rendering.


_FONT = {
    " ": ("000", "000", "000", "000", "000"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("111", "100", "100", "100", "111"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "I": ("111", "010", "010", "010", "111"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("111", "101", "101", "101", "111"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("111", "100", "111", "001", "111"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "W": ("101", "101", "111", "111", "101"),
    ".": ("000", "000", "000", "000", "010"),
    ",": ("000", "000", "000", "010", "100"),
    "[": ("110", "100", "100", "100", "110"),
    "]": ("011", "001", "001", "001", "011"),
    "(": ("010", "100", "100", "100", "010"),
    ")": ("010", "001", "001", "001", "010"),
    ">": ("100", "010", "001", "010", "100"),
    "-": ("000", "000", "111", "000", "000"),
}


def _draw_text(rectangle, x: int, y: int, text: str, scale: int = 2) -> None:
    cursor = x
    for character in text.upper():
        glyph = _FONT.get(character, _FONT[" "])
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    rectangle(
                        cursor + column * scale,
                        y + row * scale,
                        cursor + (column + 1) * scale,
                        y + (row + 1) * scale,
                        (20, 20, 20),
                    )
        cursor += 4 * scale


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return (
        struct.pack(">I", len(payload))
        + body
        + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)
    )


def write_histogram_png(path: Path, counts: Sequence[int]) -> None:
    if len(counts) != 11:
        raise ValueError("Histogram requires exactly 11 bin counts")
    if any(count < 0 for count in counts):
        raise ValueError("Histogram counts cannot be negative")

    width, height = 1100, 600
    pixels = bytearray([255] * width * height * 3)

    def rectangle(left, top, right, bottom, color):
        left, right = max(0, left), min(width, right)
        top, bottom = max(0, top), min(height, bottom)
        color_bytes = bytes(color)
        for y in range(top, bottom):
            offset = (y * width + left) * 3
            for _ in range(left, right):
                pixels[offset : offset + 3] = color_bytes
                offset += 3

    _draw_text(rectangle, 450, 12, "SKEWNESS DISTRIBUTION", scale=2)
    plot_left, plot_top, plot_right, plot_bottom = 60, 40, 1080, 540
    rectangle(plot_left, plot_bottom, plot_right, plot_bottom + 2, (20, 20, 20))
    rectangle(plot_left, plot_top, plot_left + 2, plot_bottom, (20, 20, 20))
    maximum = max(counts) or 1
    slot = (plot_right - plot_left) / len(counts)

    for index, count in enumerate(counts):
        bar_height = round((plot_bottom - plot_top - 30) * count / maximum)
        left = round(plot_left + index * slot + 8)
        right = round(plot_left + (index + 1) * slot - 8)
        rectangle(
            left,
            plot_bottom - bar_height,
            right,
            plot_bottom,
            (52, 120, 190),
        )
        count_text = str(count)
        count_x = left + max(0, (right - left - len(count_text) * 8) // 2)
        _draw_text(
            rectangle,
            count_x,
            max(plot_top, plot_bottom - bar_height - 16),
            count_text,
            scale=2,
        )
        _draw_text(
            rectangle,
            round(plot_left + index * slot + 4),
            plot_bottom + 12,
            BIN_LABELS[index],
            scale=1,
        )

    scanlines = bytearray()
    stride = width * 3
    for y in range(height):
        scanlines.append(0)
        scanlines.extend(pixels[y * stride : (y + 1) * stride])
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    encoded = (
        signature
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + _png_chunk(b"IEND", b"")
    )
    try:
        Path(path).write_bytes(encoded)
    except OSError as error:
        raise VtkFormatError(f"Unable to write PNG file: {path}") from error


# Command-line interface.


def _format_ids(cell_ids: Sequence[int]) -> str:
    return ", ".join(map(str, cell_ids)) if cell_ids else "none"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Analyze skewness and signed volume of a Legacy ASCII VTK volume mesh.")
    )
    parser.add_argument("vtk_file", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    output = arguments.output or arguments.vtk_file.with_name(
        f"{arguments.vtk_file.stem}_skewness_histogram.png"
    )

    try:
        if arguments.vtk_file.resolve() == output.resolve() or (
            output.exists() and arguments.vtk_file.samefile(output)
        ):
            raise ValueError("Input and histogram output must be different paths")
        report = analyze_mesh(read_legacy_ascii_vtk(arguments.vtk_file))
        write_histogram_png(output, report.bins)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Input file: {arguments.vtk_file}")
    print(f"Histogram PNG: {output}")
    print(f"Total cells: {report.total_cell_count}")
    print(f"Analyzed volume cells: {report.analyzed_cell_count}")
    for cell_type, count in report.type_counts:
        print(f"{CELL_DEFINITIONS[cell_type].name} cells (VTK {cell_type}): {count}")
    for cell_type, count in report.skipped_type_counts:
        print(f"Skipped VTK type {cell_type}: {count}")
    print("Skewness histogram:")
    for label, count in zip(BIN_LABELS, report.bins):
        print(f"  {label}: {count}")
    print(f"Maximum skewness: {report.maximum_skewness:.17g}")
    print("Maximum skewness cell ID(s): " + _format_ids(report.maximum_skewness_cell_ids))
    print(f"Minimum signed volume: {report.minimum_volume:.17g}")
    print("Minimum signed volume cell ID(s): " + _format_ids(report.minimum_volume_cell_ids))
    print(f"Negative-volume cells: {len(report.negative_volume_cell_ids)}")
    print("Negative-volume cell ID(s): " + _format_ids(report.negative_volume_cell_ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
