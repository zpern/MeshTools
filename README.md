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

## 克隆后构建 EXE

构建环境为 Windows x64，需要安装 Git 和 64 位 Python 3.9+。当前版本在 Python 3.9 上验证过。`release/MeshTools.exe` 已被 Git 忽略，克隆后通过以下步骤生成。

### 1. 克隆并安装依赖

将 `<仓库地址>` 替换为实际地址，在 PowerShell 中执行。

```powershell
git clone <仓库地址> tools
cd tools
python -m venv source/.venv
.\source\.venv\Scripts\python.exe -m pip install -r source/requirements.txt
```

以下命令直接调用虚拟环境中的 Python，无需激活环境。`requirements.txt` 包含运行、测试、格式检查和打包依赖。

### 2. 准备 CGNS 库

构建机器需要提供 64 位 `cgns.dll` 及其配套依赖。脚本会自动查找本机常见 ANSYS 安装目录。如果未找到，或需要指定库版本，在同一个 PowerShell 窗口设置实际路径。

```powershell
$env:MESHTOOLS_CGNS_DLL = "D:\实际目录\cgns.dll"
```

保留该 DLL 所在目录中的配套依赖，脚本会自动收集相邻依赖。仓库内的 `source/resources/` 也需要完整保留，其中包含 DT 程序、DLL 和包围盒模板。CGNS 库需在构建机器上另外准备。

### 3. 打包

在仓库根目录执行。

```powershell
.\source\.venv\Scripts\python.exe -X utf8 -B source/build_exe.py
```

成功后生成 `release/MeshTools.exe`。打包配置和中间文件位于 `source/.build/`。生成的 EXE 已包含运行依赖，可单独复制到其他 Windows x64 机器使用。

### 4. 验证

继续在仓库根目录执行。

```powershell
.\release\MeshTools.exe --help
.\source\.venv\Scripts\python.exe -X utf8 -B source/tests/run_tests.py
.\source\.venv\Scripts\python.exe -X utf8 -B source/tests/smoke_exe.py release/MeshTools.exe --quick
```

去掉 `--quick` 可验证完整的 81 种格式转换组合。
