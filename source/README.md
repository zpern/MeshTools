# 命令行网格工具源码

使用 Python 3.9+。所有功能通过 `tool.py` 调用，发布程序为 `../release/MeshTools.exe`。

## 文件用途

| 文件 | 用途 |
| --- | --- |
| `tool.py` | 统一命令行入口，分派四种功能并返回退出码 |
| `mesh_convert.py` | 通用格式转换，包含 CGNS 原生库调用、读写验证及 FACET 解析 |
| `quality.py` | 网格质量分析与 PNG 直方图 |
| `surface_check.py` | 调用 DT 检查表面网格并保留日志 |
| `bounding_box.py` | 追加三角化包围盒 |
| `build_exe.py` | EXE 打包和原生依赖收集 |
| `resources/` | 必要的包围盒模板、DT 程序及 DLL |
| `tests/` | 回归测试和发布程序验证 |
| `requirements.txt` | 按运行、测试与格式检查、EXE 打包分组的依赖清单 |
| `pyproject.toml`、`.editorconfig` | 代码格式配置 |
| `.gitignore` | 缓存及构建中间文件排除规则 |

## 代码分段

`mesh_convert.py` 按格式定义、转换流程、表面处理、VTK 读取、CGNS 原生库、CGNS 网格读写、FACET 解析、命令行接口排列，各段使用明确的英文标题。

质量分析和包围盒模块按数据读取、算法处理、结果输出、命令行接口分段。四个功能模块均使用 `build_parser()` 定义参数，`main()` 执行处理，`tool.py` 统一分派命令。

安装 `requirements.txt` 会同时安装运行、测试、格式检查和打包依赖。直接使用 EXE 无需安装这些依赖。

## 源码调用

以下命令在 `source` 目录执行。

```powershell
python -m pip install -r requirements.txt
python -X utf8 -B tool.py --help
python -X utf8 -B tool.py convert input.vtk output.stl --input-format VTK --output-format STL
python -X utf8 -B tool.py quality input.vtk --output quality.png
python -X utf8 -B tool.py surface-check input.vtk --log-path surface.log
python -X utf8 -B tool.py bounding-box input.vtk boxed.vtk 2.0
```

格式与功能限制见 [使用说明](../release/README.md)。

## 测试与打包

首次克隆后的环境准备、CGNS 库配置和完整构建步骤见[根目录构建说明](../README.md#克隆后构建-exe)。以下命令在 `source` 目录、安装依赖后的 Python 环境中执行。

```powershell
python -X utf8 -B tests/run_tests.py
python -m ruff check .
python -m ruff format --check .
python -X utf8 -B build_exe.py
python -X utf8 -B tests/smoke_exe.py ../release/MeshTools.exe
```

`smoke_exe.py` 通过真实命令行验证全部 81 种格式配对，以及质量分析、包围盒和 DT 检查。添加 `--quick` 可执行精简检查。

构建需找到 ANSYS 的 `cgns.dll`，可通过环境变量 `MESHTOOLS_CGNS_DLL` 指定。脚本收集相邻依赖并将生成的 spec 和中间文件放入 `.build`，最终输出到 `../release/MeshTools.exe`。
