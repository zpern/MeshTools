# 命令行网格工具

`MeshTools.exe` 是 Windows x64 单文件命令行程序，内含 Python、NumPy、meshio、VTK、CGNS 和 DT 运行库，可单独复制使用。

在 PowerShell 中进入 EXE 所在目录，运行以下命令。首次启动时会解压运行库到系统临时目录。

```powershell
.\MeshTools.exe --help
.\MeshTools.exe convert --help
.\MeshTools.exe convert "D:\模型\input.vtk" "D:\模型\output.stl" --input-format VTK --output-format STL
.\MeshTools.exe quality input.vtk --output quality.png
.\MeshTools.exe surface-check input.vtk --log-path surface.log
.\MeshTools.exe bounding-box input.vtk boxed.vtk 2.0
```

`convert` 要求显式指定输入格式和输出格式。其余三个命令按下表使用固定输入输出格式。相对路径以当前命令行目录为准，带空格的路径请加引号。

格式转换、质量分析和包围盒功能会替换已有输出文件。输入和输出必须是不同文件。表面检查要求日志路径尚不存在。

成功返回退出码 `0`。处理失败通常返回 `1`，参数错误返回 `2`。DT 表面检查中，`1` 表示 DT 检查失败，`2` 表示路径、启动或清理错误。详细信息直接写入终端，支持重定向到文件。

## 功能

| 功能 | 输入 | 输出 |
| --- | --- | --- |
| 格式转换 | 下表中的任一格式 | 下表中的任一格式，可自由组合 |
| 网格质量检查 | Legacy ASCII VTK 非结构体网格 | PNG 直方图，日志含有向体积、等角偏斜度和极值单元编号 |
| DT 表面检查 | VTK 表面网格 | `.log` 检查报告；临时四面体网格自动删除 |
| 追加包围盒 | ASCII Legacy VTK 非结构网格或 POLYDATA | `.vtk`，追加三角化包围盒 |

### 格式转换

不再限制固定转换配对。以下九种格式之间均可选择输入和输出，包括同格式另存：

| 格式 | 扩展名 | 说明 |
| --- | --- | --- |
| VTK | `.vtk` | Legacy VTK，含 POLYDATA 表面；输出为传统 CELLS 的 VTK 4.2 ASCII |
| VTU | `.vtu` | XML 非结构网格 |
| NAS | `.nas`、`.bdf` | Nastran 网格；默认输出 `.nas` |
| PLY | `.ply` | 表面网格 |
| STL | `.stl` | 三角形表面网格 |
| OBJ | `.obj` | 多边形表面网格 |
| MSH | `.msh` | Gmsh 网格；输出为 Gmsh 2.2 ASCII |
| CGNS | `.cgns` | 线性网格；默认读回验证坐标和连接关系 |
| FACET | `.facet` | 单对象、单三角面段；保留/生成 `face_id` |

例如：VTK→STL、STL→NAS、NAS→VTU、CGNS→VTK、FACET→PLY、OBJ→CGNS。

转换以几何和连接关系为主，场变量、材料、边界条件和分区信息不保证跨格式保留。支持线性节点/线段、三角形、四边形、多边形以及四面体、金字塔、棱柱、六面体；具体元素还须符合目标格式约束。不支持的高阶或特殊元素会报错，不会静默丢弃。

- PLY、STL、OBJ、FACET 只能保存表面：体网格会提取外表面、排除内部公共面；STL/FACET 会三角化，PLY 遇到多边形也会三角化。转换日志会说明这些变化。
- 表面格式不能恢复原来的体单元。STL→VTK 得到的仍是三角形表面，不会自动生成四面体。
- NAS/MSH 不保存任意多边形单元；CGNS 不保存任意多边形、高阶单元或纯顶点网格。遇到这些输入会明确提示限制。
- CGNS 读取多个区域时，不自动焊接不同区域的重合节点。不会保留场数据、BC 标签及区域元数据。
- CGNS 默认使用 EXE 内的库。可使用 `--cgns-dll` 指定其他库，使用 `--no-verify` 跳过读回验证。Windows 原生 CGNS 库使用系统代码页，文件名须能由系统代码页表示；本机中文和空格路径已验证。

### 其他功能说明

**网格质量检查**分析四面体、六面体、棱柱和金字塔，其他单元统计为跳过。偏斜度越接近 0 越好；单元编号从 0 开始。输入需要传统 `CELLS` 布局的 Legacy ASCII `UNSTRUCTURED_GRID`；其他格式可先转换成 VTK。

**DT 表面检查**调用内置 DT，不添加包围盒。退出码为 0 且日志无错误时通过。显式输出日志必须是新文件；日志中的编号可能属于 DT 内部网格。这是 DT 的接受性检查。

**追加包围盒**使用固定模板的 2288 点、4572 个三角面，以输入轴对齐包围盒的中心为中心，用三个方向边长的平均值乘以比例缩放。它不是逐轴放大，细长模型需选取足够大的比例。比例必须为有限正数。只保留几何和单元类型，不复制属性数据；不支持几何之前的 `FIELD` 元数据。

