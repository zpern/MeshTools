# 命令行网格工具

发布程序位于 `release/MeshTools.exe`，通过 PowerShell 或 CMD 调用。

```powershell
.\release\MeshTools.exe --help
.\release\MeshTools.exe convert input.vtk output.stl --input-format VTK --output-format STL
```

支持格式转换、网格质量分析、DT 表面检查和追加包围盒。

| 目录 | 用途 |
| --- | --- |
| `release/` | 独立运行的命令行 EXE 和使用说明 |
| `source/` | 合并后的 Python 源码、必要资源、测试与打包脚本 |

[使用说明](release/README.md) · [源码文件说明](source/README.md)
