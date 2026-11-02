"""Convert mesh geometry between supported formats.

Sections cover format definitions, conversion, surface processing, VTK reading,
CGNS library access and mesh I/O, FACET parsing, and command-line arguments."""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
from ctypes import (
    CDLL,
    POINTER,
    byref,
    c_char_p,
    c_int,
    c_longlong,
    c_void_p,
    cast,
    create_string_buffer,
)
from pathlib import Path
from typing import TYPE_CHECKING, Sequence, TextIO

if TYPE_CHECKING:
    import meshio

# Format definitions and element metadata.

FORMATS = ("VTK", "VTU", "NAS", "PLY", "STL", "OBJ", "MSH", "CGNS", "FACET")

MESHIO_FORMATS = {
    "VTK": "vtk",
    "VTU": "vtu",
    "NAS": "nastran",
    "PLY": "ply",
    "STL": "stl",
    "OBJ": "obj",
    "MSH": "gmsh22",
}

SURFACE_FORMATS = {"PLY", "STL", "OBJ", "FACET"}

SURFACE_TYPES = {"triangle", "quad", "polygon"}

VOLUME_FACES = {
    "tetra": ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)),
    "hexahedron": (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ),
    "wedge": ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
    "pyramid": ((0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
}

LINEAR_TYPES = {"vertex", "line", *SURFACE_TYPES, *VOLUME_FACES}

CG_MODE_WRITE = 1
CG_MODE_READ = 0
CG_FILE_HDF5 = 2
CGNS_UNSTRUCTURED = 3
CGNS_REAL_DOUBLE = 4

# meshio name -> (CGNS element ID, node count, dimension, VTK cell ID).
CGNS_CELL_TYPES = {
    "vertex": (2, 1, 0, 1),
    "line": (3, 2, 1, 3),
    "triangle": (5, 3, 2, 5),
    "quad": (7, 4, 2, 9),
    "tetra": (10, 4, 3, 10),
    "pyramid": (12, 5, 3, 14),
    "wedge": (14, 6, 3, 13),
    "hexahedron": (17, 8, 3, 12),
}

Point = tuple[float, float, float]
Triangle = tuple[int, int, int]


# Conversion pipeline.


def convert_mesh(source, target, input_format, output_format, cgns_dll=None, verify=True):
    source, target = Path(source).expanduser(), Path(target).expanduser()
    if input_format not in FORMATS or output_format not in FORMATS:
        raise ValueError("Unknown input or output mesh format")
    if not source.is_file():
        raise ValueError(f"Input file does not exist: {source}")
    if source.resolve() == target.resolve() or (target.exists() and source.samefile(target)):
        raise ValueError("Input and output must be different files")
    mesh = read_mesh(source, input_format)
    validate_mesh(mesh)
    if output_format in SURFACE_FORMATS:
        mesh = prepare_surface(mesh, output_format)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp" + target.suffix,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        write_mesh(temporary, mesh, output_format, cgns_dll, verify)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Converted {input_format} -> {output_format}: {target}")
    print(f"Points: {len(mesh.points)}; cells: {sum(len(c.data) for c in mesh.cells)}")


def read_mesh(path: Path, fmt: str) -> meshio.Mesh:
    import meshio
    import numpy as np

    if fmt == "FACET":
        points, triangles, ids = read_facet(path)
        return meshio.Mesh(
            np.asarray(points, dtype=float),
            [("triangle", np.asarray(triangles, dtype=np.int64).reshape(-1, 3))],
            cell_data={"face_id": [np.asarray(ids, dtype=np.int64)]},
        )
    if fmt == "CGNS":
        return read_cgns(path)
    if fmt == "VTK":
        with path.open("rb") as stream:
            header = [stream.readline(4096).strip().upper() for _ in range(4)]
        if header[3].split() == [b"DATASET", b"POLYDATA"]:
            return _read_polydata(path)
    try:
        return meshio.read(path, file_format="gmsh" if fmt == "MSH" else MESHIO_FORMATS[fmt])
    except SystemExit as error:
        raise ValueError(f"Unable to read {fmt} mesh: {path}") from error


def write_mesh(path: Path, mesh: meshio.Mesh, fmt: str, cgns_dll=None, verify=True) -> None:
    import meshio

    if fmt == "FACET":
        face_count = sum(len(block.data) for block in mesh.cells)
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"FACET FILE V3.0\n1\nmesh\n0 0 0\n{len(mesh.points)}\n")
            for point in mesh.points:
                stream.write(" ".join(format(value, ".17g") for value in point) + "\n")
            stream.write(f"1\nsurface\n{face_count} 3\n")
            for bi, block in enumerate(mesh.cells):
                for ci, cell in enumerate(block.data):
                    stream.write(
                        " ".join(str(int(index) + 1) for index in cell)
                        + f" 0 {_face_id(mesh, bi, ci)}\n"
                    )
        return
    if fmt == "CGNS":
        write_cgns(path, mesh, cgns_dll, verify)
        return
    if fmt in {"NAS", "MSH"} and any(c.type == "polygon" for c in mesh.cells):
        raise ValueError(f"{fmt} cannot preserve polygon cells; use VTK/VTU or a surface format")
    if fmt == "MSH":
        # meshio 5.x serializes ASCII NodeData/ElementData with repr(np.scalar).
        # NumPy 2 emits e.g. np.float64(0.0), which is not a Gmsh number. Keep
        # geometry and native element tags; report every omitted data field.
        native_tags = {"gmsh:physical", "gmsh:geometrical"}
        omitted = [f"point:{name}" for name in mesh.point_data]
        omitted.extend(f"cell:{name}" for name in mesh.cell_data if name not in native_tags)
        if omitted:
            print("MSH geometry export omitted unsupported data fields: " + ", ".join(omitted))
        mesh = meshio.Mesh(
            mesh.points,
            mesh.cells,
            cell_data={key: value for key, value in mesh.cell_data.items() if key in native_tags},
            field_data=mesh.field_data,
        )
    options = {"binary": False} if fmt in {"VTK", "PLY", "STL", "MSH"} else {}
    if fmt == "NAS":
        options = {"point_format": "fixed-large", "cell_format": "fixed-small"}
    writer_format = "vtk42" if fmt == "VTK" else MESHIO_FORMATS[fmt]
    meshio.write(path, mesh, file_format=writer_format, **options)


def validate_mesh(mesh: meshio.Mesh) -> None:
    import numpy as np

    if mesh.points.ndim != 2 or mesh.points.shape[1] not in (2, 3) or not len(mesh.points):
        raise ValueError("Mesh must contain 2D or 3D points")
    if not np.isfinite(mesh.points).all():
        raise ValueError("Mesh contains non-finite point coordinates")
    if mesh.points.shape[1] == 2:
        mesh.points = np.column_stack([mesh.points, np.zeros(len(mesh.points))])
    mesh.points = np.asarray(mesh.points, dtype=np.float64)
    for block in mesh.cells:
        if block.type not in LINEAR_TYPES:
            raise ValueError(
                f"Unsupported cell type: {block.type}; only linear cells are supported"
            )
        if not np.issubdtype(block.data.dtype, np.integer):
            raise ValueError(f"Non-integer connectivity in {block.type}")
        if block.data.size and (block.data.min() < 0 or block.data.max() >= len(mesh.points)):
            raise ValueError(f"Invalid point index in {block.type}")


# Surface extraction and triangulation.


def _face_id(mesh: meshio.Mesh, block_index: int, cell_index: int) -> int:
    import numpy as np

    arrays = mesh.cell_data.get("face_id")
    if arrays is None:
        return 1
    value = np.asarray(arrays[block_index][cell_index]).reshape(-1)
    if len(value) != 1 or not np.isfinite(value[0]) or int(value[0]) != value[0]:
        raise ValueError("face_id must contain one integer per cell")
    return int(value[0])


def prepare_surface(mesh: meshio.Mesh, fmt: str) -> meshio.Mesh:
    """Extract linear volume boundaries, retain surface IDs, and triangulate as needed."""
    import meshio
    import numpy as np

    unsupported = {
        block.type
        for block in mesh.cells
        if len(block.data) and block.type not in SURFACE_TYPES | VOLUME_FACES.keys()
    }
    if unsupported:
        raise ValueError(f"{fmt} surface output cannot preserve cell types: {sorted(unsupported)}")
    volume_faces = {}
    explicit_faces = []
    volume_count = 0
    for block_index, block in enumerate(mesh.cells):
        for cell_index, cell in enumerate(block.data):
            if block.type in VOLUME_FACES:
                volume_count += 1
                for indices in VOLUME_FACES[block.type]:
                    face = tuple(int(cell[index]) for index in indices)
                    key = tuple(sorted(face))
                    if key in volume_faces:
                        volume_faces[key][0] += 1
                    else:
                        volume_faces[key] = [1, face]
            else:
                explicit_faces.append(
                    (tuple(map(int, cell)), _face_id(mesh, block_index, cell_index))
                )
    if any(count > 2 for count, _ in volume_faces.values()):
        raise ValueError("Cannot extract boundary: a volume face belongs to more than two cells")
    faces = []
    seen = set()
    for face, face_id in explicit_faces:
        key = tuple(sorted(face))
        if key in seen or (key in volume_faces and volume_faces[key][0] != 1):
            continue
        faces.append((face, face_id))
        seen.add(key)
    for key, (count, face) in volume_faces.items():
        if count == 1 and key not in seen:
            faces.append((face, 1))
            seen.add(key)
    if volume_count:
        boundary_count = sum(count == 1 for count, _ in volume_faces.values())
        print(f"Extracted {boundary_count} boundary faces from {volume_count} volume cells.")
    grouped = {}
    ids = {}
    triangulated = 0
    for face, face_id in faces:
        if len(face) < 3:
            raise ValueError("Surface face has fewer than three vertices")
        # PLY intentionally limits output to triangles/quads for broad interoperability.
        triangulate = len(face) > 3 and (
            fmt in {"STL", "FACET"} or (fmt == "PLY" and len(face) > 4)
        )
        if triangulate:
            triangles = _triangulate_polygon(mesh.points, face)
            grouped.setdefault(3, []).extend(triangles)
            ids.setdefault(3, []).extend([face_id] * len(triangles))
            triangulated += 1
        else:
            grouped.setdefault(len(face), []).append(face)
            ids.setdefault(len(face), []).append(face_id)
    if triangulated:
        print(f"Triangulated {triangulated} polygon/quad faces for {fmt} output.")
    if not grouped:
        raise ValueError(f"No surface faces available for {fmt} output")
    cells = [
        (
            "triangle" if n == 3 else "quad" if n == 4 else "polygon",
            np.asarray(group, dtype=np.int32),
        )
        for n, group in grouped.items()
    ]
    return meshio.Mesh(
        mesh.points.copy(), cells, cell_data={"face_id": [np.asarray(ids[n]) for n in grouped]}
    )


def _triangulate_polygon(points, face):
    """Ear clipping handles simple concave polygons as well as quads."""
    import numpy as np

    vertices = points[list(face)]
    normal = np.sum(np.cross(vertices, np.roll(vertices, -1, axis=0)), axis=0)
    axis = int(np.argmax(np.abs(normal)))
    projected = np.delete(vertices, axis, axis=1)
    extent = float(np.ptp(projected, axis=0).max())
    tolerance = max(extent * extent * 1e-12, np.finfo(float).tiny)

    def cross(a, b, c):
        u, v = b - a, c - a
        return u[0] * v[1] - u[1] * v[0]

    area = sum(
        cross(np.zeros(2), projected[i], projected[(i + 1) % len(face)]) for i in range(len(face))
    )
    if abs(area) <= tolerance:
        raise ValueError("Cannot triangulate a degenerate polygon")
    direction = 1 if area > 0 else -1
    remaining = list(range(len(face)))
    triangles = []
    while len(remaining) > 3:
        for position, current in enumerate(remaining):
            before, after = remaining[position - 1], remaining[(position + 1) % len(remaining)]
            a, b, c = projected[[before, current, after]]
            if direction * cross(a, b, c) <= tolerance:
                continue
            others = [i for i in remaining if i not in (before, current, after)]
            if any(
                all(
                    direction * cross(x, y, projected[i]) >= -tolerance
                    for x, y in ((a, b), (b, c), (c, a))
                )
                for i in others
            ):
                continue
            triangles.append((face[before], face[current], face[after]))
            remaining.pop(position)
            break
        else:
            raise ValueError("Cannot triangulate a self-intersecting or degenerate polygon")
    triangles.append(tuple(face[i] for i in remaining))
    return triangles


# VTK POLYDATA reading.


def _read_polydata(path: Path) -> meshio.Mesh:
    """Preserve polygons; expand VTK aggregate cells into equivalent linear cells."""
    import meshio
    import numpy as np
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkIOLegacy import vtkPolyDataReader

    reader = vtkPolyDataReader()
    errors = []
    reader.AddObserver("ErrorEvent", lambda *_: errors.append(True))
    reader.SetFileName(str(path))
    reader.ReadAllScalarsOn()
    reader.ReadAllVectorsOn()
    reader.ReadAllFieldsOn()
    reader.Update()
    data = reader.GetOutput()
    if errors or reader.GetErrorCode() or data.GetPoints() is None:
        raise ValueError(f"Unable to read VTK POLYDATA: {path}")
    groups = {}
    source_ids = {}
    expanded = {"polyvertex": 0, "polyline": 0, "triangle strip": 0}

    def append(kind, vertices, source_id):
        key = (kind, len(vertices))
        groups.setdefault(key, []).append(vertices)
        source_ids.setdefault(key, []).append(source_id)

    for index in range(data.GetNumberOfCells()):
        cell = data.GetCell(index)
        vertices = [cell.GetPointId(i) for i in range(cell.GetNumberOfPoints())]
        kind = cell.GetCellType()
        if kind in (1, 2):
            for vertex in vertices:
                append("vertex", [vertex], index)
            expanded["polyvertex"] += kind == 2
        elif kind in (3, 4):
            for start, end in zip(vertices, vertices[1:]):
                append("line", [start, end], index)
            expanded["polyline"] += kind == 4
        elif kind == 6:
            for offset in range(len(vertices) - 2):
                a, b, c = vertices[offset : offset + 3]
                append("triangle", [a, b, c] if offset % 2 == 0 else [b, a, c], index)
            expanded["triangle strip"] += 1
        elif kind in (5, 7, 9):
            name = {3: "triangle", 4: "quad"}.get(len(vertices), "polygon")
            append(name, vertices, index)
        else:
            raise ValueError(f"Unsupported VTK POLYDATA cell type: {kind}")
    for kind, count in expanded.items():
        if count:
            print(f"Expanded {count} VTK {kind} cells into equivalent linear cells.")
    point_data = {}
    cell_data = {}
    for attributes, output, is_cell in (
        (data.GetPointData(), point_data, False),
        (data.GetCellData(), cell_data, True),
    ):
        for index in range(attributes.GetNumberOfArrays()):
            array = attributes.GetArray(index)
            name = attributes.GetArrayName(index) or f"data_{index}"
            if array is None:
                print(f"VTK POLYDATA geometry import omitted non-numeric field: {name}")
                continue
            values = vtk_to_numpy(array).copy()
            output[name] = [values[source_ids[key]] for key in groups] if is_cell else values
    return meshio.Mesh(
        vtk_to_numpy(data.GetPoints().GetData()).copy(),
        [(key[0], np.asarray(values, dtype=np.int64)) for key, values in groups.items()],
        point_data=point_data,
        cell_data=cell_data,
    )


# CGNS native library.


def find_cgns_dll(explicit: str | Path | None = None) -> Path:
    """Return an official ANSYS CGNS library, preferring an explicit path."""
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"CGNS DLL does not exist: {candidate}")
        return candidate.resolve()

    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "native" / "cgns" / "cgns.dll"
        if bundled.is_file():
            return bundled

    roots = []
    for value in (
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        r"F:\Program Files",
    ):
        if value:
            root = Path(value)
            if root not in roots:
                roots.append(root)

    patterns = (
        "ANSYS Inc/v*/aisol/AUTODYN/winx64/cgns.dll",
        "ANSYS Inc/v*/ansys/bin/winx64/cgnsdll.dll",
    )
    for pattern in patterns:
        matches = [path for root in roots for path in root.glob(pattern) if path.is_file()]
        if matches:
            return sorted(matches, reverse=True)[0].resolve()
    searched = ", ".join(str(root) for root in roots)
    raise FileNotFoundError(f"No ANSYS CGNS DLL found. Searched: {searched}")


def _bind_cgns(dll_path: Path):
    lib = CDLL(str(dll_path))
    lib.cg_get_error.restype = c_char_p
    lib.cg_set_file_type.argtypes = [c_int]
    lib.cg_open.argtypes = [c_char_p, c_int, POINTER(c_int)]
    lib.cg_base_write.argtypes = [c_int, c_char_p, c_int, c_int, POINTER(c_int)]
    lib.cg_zone_write.argtypes = [c_int, c_int, c_char_p, c_void_p, c_int, POINTER(c_int)]
    lib.cg_zone_read.argtypes = [c_int, c_int, c_int, c_char_p, c_void_p]
    lib.cg_coord_write.argtypes = [c_int, c_int, c_int, c_int, c_char_p, c_void_p, POINTER(c_int)]
    lib.cg_section_write.argtypes = [
        c_int,
        c_int,
        c_int,
        c_char_p,
        c_int,
        c_longlong,
        c_longlong,
        c_int,
        c_void_p,
        POINTER(c_int),
    ]
    lib.cg_close.argtypes = [c_int]
    return lib


def _check_cgns_result(lib, code, operation):
    if code:
        raw = lib.cg_get_error()
        detail = raw.decode("utf-8", errors="replace") if raw else str(code)
        raise RuntimeError(f"{operation}: {detail}")


def _native_path(path: Path) -> bytes:
    """Encode paths for the vendor library's native Windows file API."""
    return str(path).encode("mbcs") if os.name == "nt" else os.fsencode(path)


def _detect_cgsize_type(lib):
    """Probe whether the loaded CGNS library uses 32- or 64-bit cgsize_t."""
    import numpy as np

    with tempfile.TemporaryDirectory() as folder:
        probe = Path(folder) / "cgsize_probe.cgns"
        _check_cgns_result(lib, lib.cg_set_file_type(CG_FILE_HDF5), "cg_set_file_type")
        file_index = c_int()
        _check_cgns_result(
            lib,
            lib.cg_open(_native_path(probe), CG_MODE_WRITE, byref(file_index)),
            "cg_open(probe)",
        )
        try:
            base_index = c_int()
            _check_cgns_result(
                lib,
                lib.cg_base_write(file_index.value, b"Base", 2, 3, byref(base_index)),
                "cg_base_write(probe)",
            )
            zone_index = c_int()
            # A 64-bit DLL reads [2,1,0]; a 32-bit DLL reads the first three
            # 32-bit words [2,0,1]. Both are valid probe zone dimensions.
            values = (c_longlong * 3)(2, 1, 0)
            _check_cgns_result(
                lib,
                lib.cg_zone_write(
                    file_index.value,
                    base_index.value,
                    b"Probe",
                    values,
                    CGNS_UNSTRUCTURED,
                    byref(zone_index),
                ),
                "cg_zone_write(probe)",
            )
        finally:
            _check_cgns_result(lib, lib.cg_close(file_index.value), "cg_close(probe)")
        read_index = c_int()
        _check_cgns_result(
            lib,
            lib.cg_open(_native_path(probe), CG_MODE_READ, byref(read_index)),
            "cg_open(probe read)",
        )
        read_values = (c_longlong * 3)(-1, -1, -1)
        try:
            zone_name = create_string_buffer(33)
            _check_cgns_result(
                lib,
                lib.cg_zone_read(read_index.value, 1, 1, zone_name, read_values),
                "cg_zone_read(probe)",
            )
        finally:
            _check_cgns_result(lib, lib.cg_close(read_index.value), "cg_close(probe read)")
        words32 = [cast(read_values, POINTER(c_int))[i] for i in range(6)]
        words64 = list(read_values)
    if words32[:3] == [2, 0, 1] and words32[3:] == [-1, -1, -1]:
        return c_int, np.int32
    if words64 == [2, 1, 0]:
        return c_longlong, np.int64
    raise RuntimeError(
        f"Unable to determine CGNS cgsize_t ABI; probe read 32-bit {words32}, 64-bit {words64}"
    )


# CGNS mesh reading, writing and verification.


def read_cgns(path: Path):
    """Read all zones and boundary sections, concatenating their node arrays.

    Blocks sharing the same VTK point array reuse node indices. Independent zones
    retain separate nodes even at identical coordinates; interfaces are not welded.
    """
    import meshio
    import numpy as np
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    if not Path(path).is_file():
        raise FileNotFoundError(f"Input CGNS does not exist: {path}")
    reader = vtk.vtkCGNSReader()
    errors = []
    reader.AddObserver("ErrorEvent", lambda obj, event: errors.append(event))
    reader.SetFileName(str(path))
    # BC patches can duplicate section geometry; surface sections already contain it.
    reader.LoadBndPatchOff()
    reader.LoadSurfacePatchOn()
    reader.UpdateInformation()
    reader.EnableAllBases()
    reader.Update()
    if reader.GetErrorCode() or errors:
        raise ValueError(f"VTK could not read CGNS: {path}")
    mapping = {info[3]: name for name, info in CGNS_CELL_TYPES.items()}
    points, cells = [], {}
    point_offsets = {}
    offset = 0

    def visit(item):
        nonlocal offset
        if item is None:
            return
        if item.IsA("vtkCompositeDataSet"):
            iterator = item.NewIterator()
            iterator.InitTraversal()
            while not iterator.IsDoneWithTraversal():
                visit(iterator.GetCurrentDataObject())
                iterator.GoToNextItem()
            return
        if not item.IsA("vtkDataSet"):
            raise ValueError(f"Unsupported CGNS block: {item.GetClassName()}")
        if not item.GetNumberOfPoints():
            return
        if item.IsA("vtkPointSet"):
            coordinates = vtk_to_numpy(item.GetPoints().GetData()).copy()
        else:
            coordinates = np.array([item.GetPoint(i) for i in range(item.GetNumberOfPoints())])
        identity = item.GetPoints().__this__ if item.IsA("vtkPointSet") else id(item)
        if identity not in point_offsets:
            point_offsets[identity] = offset
            points.append(coordinates)
            offset += len(coordinates)
        block_offset = point_offsets[identity]
        for index in range(item.GetNumberOfCells()):
            vtk_type = item.GetCellType(index)
            if vtk_type not in mapping:
                raise ValueError(f"Unsupported CGNS cell type: VTK {vtk_type}")
            name = mapping[vtk_type]
            cell = item.GetCell(index)
            ids = [cell.GetPointId(i) + block_offset for i in range(cell.GetNumberOfPoints())]
            cells.setdefault(name, []).append(ids)

    visit(reader.GetOutput())
    if not points or not cells:
        raise ValueError("CGNS contains no supported mesh cells")
    return meshio.Mesh(np.concatenate(points), [(name, data) for name, data in cells.items()])


def _validate_cgns_mesh(mesh):
    import numpy as np

    points = np.asarray(mesh.points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] not in (2, 3) or not len(points):
        raise ValueError("CGNS requires nonempty 2D or 3D point coordinates")
    if points.shape[1] == 2:
        points = np.column_stack((points, np.zeros(len(points))))
    if not np.isfinite(points).all():
        raise ValueError("CGNS coordinates must be finite")
    cells = []
    for block in mesh.cells:
        if not len(block.data):
            continue
        if block.type not in CGNS_CELL_TYPES:
            raise ValueError(f"Unsupported CGNS cell type: {block.type}")
        array = np.asarray(block.data)
        if array.ndim != 2 or array.shape[1] != CGNS_CELL_TYPES[block.type][1]:
            raise ValueError(f"Invalid {block.type} connectivity shape")
        if not np.issubdtype(array.dtype, np.integer):
            raise ValueError(f"{block.type} connectivity must contain integer indices")
        if array.min() < 0 or array.max() >= len(points):
            raise ValueError(f"{block.type} connectivity contains an invalid vertex index")
        cells.append((block.type, array))
    if not cells:
        raise ValueError("CGNS requires at least one cell")
    dimension = max(CGNS_CELL_TYPES[name][2] for name, _ in cells)
    if dimension == 0:
        raise ValueError("CGNS requires at least one line, surface or volume cell")
    return points, sorted(cells, key=lambda pair: -CGNS_CELL_TYPES[pair[0]][2]), dimension


def write_cgns(path: Path, mesh, cgns_dll: Path | None = None, verify: bool = True) -> None:
    """Write linear elements; callers are responsible for atomic output replacement."""
    import numpy as np

    points, cells, dimension = _validate_cgns_mesh(mesh)
    lib = _bind_cgns(find_cgns_dll(cgns_dll))
    cgsize, index_dtype = _detect_cgsize_type(lib)
    lib.cg_section_write.argtypes = [
        c_int,
        c_int,
        c_int,
        c_char_p,
        c_int,
        cgsize,
        cgsize,
        c_int,
        c_void_p,
        POINTER(c_int),
    ]
    limit = np.iinfo(index_dtype).max
    if len(points) > limit or sum(len(data) for _, data in cells) > limit:
        raise ValueError("Mesh exceeds CGNS library index capacity")

    def check(code, operation):
        _check_cgns_result(lib, code, operation)

    check(lib.cg_set_file_type(CG_FILE_HDF5), "cg_set_file_type")
    file_id, base_id, zone_id = c_int(), c_int(), c_int()
    # ANSYS CGNS/HDF5 uses narrow Windows CRT paths (the active ANSI code page).
    # os.fsencode uses UTF-8 on modern Python and therefore fails for Chinese paths.
    # VTK's own reader accepts Unicode filenames and must keep receiving str paths.
    native_path = _native_path(path)
    check(lib.cg_open(native_path, CG_MODE_WRITE, byref(file_id)), "cg_open")
    try:
        check(lib.cg_base_write(file_id, b"Base", dimension, 3, byref(base_id)), "cg_base_write")
        primary_count = sum(
            len(data) for name, data in cells if CGNS_CELL_TYPES[name][2] == dimension
        )
        size = (cgsize * 3)(len(points), primary_count, 0)
        check(
            lib.cg_zone_write(file_id, base_id, b"Zone1", size, CGNS_UNSTRUCTURED, byref(zone_id)),
            "cg_zone_write",
        )
        for axis, name in enumerate((b"CoordinateX", b"CoordinateY", b"CoordinateZ")):
            values = np.ascontiguousarray(points[:, axis])
            coord_id = c_int()
            check(
                lib.cg_coord_write(
                    file_id,
                    base_id,
                    zone_id,
                    CGNS_REAL_DOUBLE,
                    name,
                    values.ctypes.data_as(c_void_p),
                    byref(coord_id),
                ),
                "cg_coord_write",
            )
        first = 1
        for index, (name, data) in enumerate(cells):
            values = np.ascontiguousarray(data + 1, dtype=index_dtype)
            last = first + len(data) - 1
            section_id = c_int()
            check(
                lib.cg_section_write(
                    file_id,
                    base_id,
                    zone_id,
                    f"{name}_{index}".encode(),
                    CGNS_CELL_TYPES[name][0],
                    first,
                    last,
                    0,
                    values.ctypes.data_as(c_void_p),
                    byref(section_id),
                ),
                f"cg_section_write({name})",
            )
            first = last + 1
    finally:
        code = lib.cg_close(file_id)
        if sys.exc_info()[0] is None:
            check(code, "cg_close")
    if verify:
        _verify_cgns_mesh(path, points, cells)


def _verify_cgns_mesh(path, points, cells):
    import numpy as np

    result = read_cgns(path)
    if result.points.shape != points.shape or not np.allclose(result.points, points):
        raise RuntimeError("CGNS verification mismatch: point coordinates or count changed")
    expected = {}
    for name, data in cells:
        expected.setdefault(name, []).extend(tuple(row) for row in data.tolist())
    actual = {}
    for block in result.cells:
        actual.setdefault(block.type, []).extend(tuple(row) for row in block.data.tolist())
    if {k: sorted(v) for k, v in expected.items()} != {k: sorted(v) for k, v in actual.items()}:
        raise RuntimeError("CGNS verification mismatch: cell connectivity changed")


# FACET parsing.


def _read_facet_fields(stream: TextIO, description: str) -> list[str]:
    """Read one required, nonempty record with a useful error context."""
    line = stream.readline()
    if not line:
        raise ValueError(f"Unexpected end of file while reading {description}.")
    fields = line.split()
    if not fields:
        raise ValueError(f"Empty record while reading {description}.")
    return fields


def _read_facet_count(fields: list[str], description: str) -> int:
    try:
        count = int(fields[0])
    except ValueError as error:
        raise ValueError(f"Invalid {description}: {fields[0]!r}.") from error
    if count < 0:
        raise ValueError(f"The {description} cannot be negative.")
    return count


def read_facet(input_path: Path) -> tuple[list[Point], list[Triangle], list[int]]:
    """Read and validate points, zero-based triangles, and original face IDs."""
    points: list[Point] = []
    triangles: list[Triangle] = []
    face_ids: list[int] = []
    with input_path.open("r", encoding="utf-8-sig") as stream:
        header = _read_facet_fields(stream, "FACET header")
        if header[:2] != ["FACET", "FILE"]:
            raise ValueError("Expected a FACET FILE header.")
        object_count = _read_facet_count(_read_facet_fields(stream, "object count"), "object count")
        if object_count != 1:
            raise ValueError("Only single-object FACET files are supported.")
        _read_facet_fields(stream, "object name")
        _read_facet_fields(stream, "object metadata")
        point_count = _read_facet_count(_read_facet_fields(stream, "point count"), "point count")
        for point_number in range(1, point_count + 1):
            fields = _read_facet_fields(stream, f"point {point_number}")
            if len(fields) < 3:
                raise ValueError(f"Point {point_number} must contain three coordinates.")
            try:
                point = (float(fields[0]), float(fields[1]), float(fields[2]))
            except ValueError as error:
                raise ValueError(f"Invalid coordinates at point {point_number}.") from error
            if not all(math.isfinite(value) for value in point):
                raise ValueError(f"Point {point_number} contains a non-finite coordinate.")
            points.append(point)

        section_count = _read_facet_count(
            _read_facet_fields(stream, "face section count"), "face section count"
        )
        if section_count != 1:
            raise ValueError("Only one triangle section is supported.")
        _read_facet_fields(stream, "face section name")
        face_header = _read_facet_fields(stream, "face count and arity")
        face_count = _read_facet_count(face_header, "face count")
        if len(face_header) < 2 or face_header[1] != "3":
            raise ValueError("Only triangular faces (arity 3) are supported.")
        for face_number in range(1, face_count + 1):
            fields = _read_facet_fields(stream, f"face {face_number}")
            if len(fields) < 5:
                raise ValueError(f"Face {face_number} must contain at least five fields.")
            try:
                triangle = (int(fields[0]) - 1, int(fields[1]) - 1, int(fields[2]) - 1)
                face_id = int(fields[4])
            except ValueError as error:
                raise ValueError(
                    f"Invalid vertex index or face ID at face {face_number}."
                ) from error
            if any(index < 0 or index >= point_count for index in triangle):
                raise ValueError(f"Face {face_number} has a vertex index outside 1..{point_count}.")
            triangles.append(triangle)
            face_ids.append(face_id)
        if any(line.strip() for line in stream):
            raise ValueError("Unexpected data after the triangle section.")
    return points, triangles, face_ids


# Command-line interface.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert mesh files between supported formats.",
        usage="%(prog)s <input> <output> [options]",
        formatter_class=lambda prog: argparse.RawTextHelpFormatter(
            prog, width=100, max_help_position=28
        ),
        add_help=False,
        epilog=(
            "Supported formats\n"
            "  VTK, VTU, NAS (including BDF), PLY, STL, OBJ, MSH, CGNS, FACET\n"
            "  Format names must be uppercase."
        ),
    )
    parser._positionals.title = "Files (in this order)"
    parser.add_argument("input", type=Path, metavar="input", help="Path to the input mesh file.")
    parser.add_argument(
        "output", type=Path, metavar="output", help="Path to save the converted mesh."
    )
    required = parser.add_argument_group("Required parameters")
    required.add_argument(
        "--input-format",
        choices=FORMATS,
        required=True,
        metavar="FORMAT",
        help="Input mesh format. See supported formats below.",
    )
    required.add_argument(
        "--output-format",
        choices=FORMATS,
        required=True,
        metavar="FORMAT",
        help="Output mesh format. See supported formats below.",
    )
    optional = parser.add_argument_group("Optional parameters")
    optional.add_argument(
        "--cgns-dll",
        type=Path,
        metavar="PATH",
        help="CGNS DLL path. The EXE uses its bundled library by default.",
    )
    optional.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip CGNS output readback verification (enabled by default).",
    )
    optional.add_argument("-h", "--help", action="help", help="Show this help message and exit.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        convert_mesh(
            args.input,
            args.output,
            args.input_format,
            args.output_format,
            args.cgns_dll,
            not args.no_verify,
        )
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
