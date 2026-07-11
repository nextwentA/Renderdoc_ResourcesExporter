# ResourcesExporter

RenderDoc 资源导出插件，支持导出 FBX/OBJ 格式，附带贴图和 Shader 导出。

基于 [renderdoc2fbx](https://github.com/aRincloud/renderdoc2fbx-main) 和 [csv2fbx](https://github.com/chineseoldghost/csv2fbx) 重构。

---

## 安装

1. 运行 `install.bat`，脚本会自动定位 RenderDoc 扩展目录并复制文件
2. 重启 RenderDoc或者`Tools-Manage Extensions-ResourcesExporter-Reload`
3. 在 Mesh Preview 面板中点击插件图标（一个黄色拼图图案），菜单里会出现 **Export Resource** 和 **Quick Export**

**系统要求**：RenderDoc >= 1.17，Windows

---

## 用法

### 普通导出

Mesh Preview -> 插件 -> Export Resource，弹出选项对话框后配置好参数点 OK，选择保存路径。

### 快速导出

Mesh Preview -> 插件 -> Quick Export，跳过对话框，直接用上次保存的配置，只弹路径选择框。

### 批量导出

在对话框"批量 EID"输入框里填 EID 范围，点 OK 后选择输出根目录，每个 EID 单独生成子文件夹 `eid_00100/`。

格式：`100,200-210,300`（逗号分隔单个 EID，短横线表示含两端的连续范围）

---

## 选项说明

### 导出模式

| 选项 | 说明 |
|---|---|
| VS Input | 原始顶点缓冲区数据，物体空间坐标 |
| VS Output | 顶点着色器处理后数据，需要 GPU Replay |

两个可以同时勾选，会各自生成独立文件。

### 引擎预设

选择引擎后会自动填充属性映射表。如果自动填充的结果不对（常见于 Vulkan 抓帧），点 **Auto-detect Attributes** 让插件根据实际存在的属性名重新匹配。

### 属性映射

对应 Mesh Viewer 表格中的列名，填写时区分大小写。

常见规律：

- Unity / DX11：`POSITION`、`NORMAL`、`TEXCOORD0`、`TEXCOORD1`
- Unreal / DX12：`ATTRIBUTE0`（位置）、`ATTRIBUTE2`（法线）、`ATTRIBUTE5`（UV）
- Vulkan：`_input0`、`_input1`、`_input2` ...

留空的通道不会写入输出文件。

### UV 选项

如果导入引擎后 UV 上下左右颠倒，勾上 **Flip V**或者**Flip U**（DX 和 OpenGL UV 原点定义不同）。

### 贴图导出

- **Export Inputs**：导出当前 Draw Call 绑定的所有输入贴图
- **Export Outputs**：导出渲染目标（颜色 + 深度）
- 支持格式：PNG / DDS / TGA / BMP / HDR / EXR

### Shader 导出

选择阶段（VS / PS / GS / HS / DS / CS），格式选 Disasm txt 会输出反汇编文本，Binary 输出原始 DXBC/SPIRV 字节码。

---

## 目录结构

```
ResourcesExporter/
  __init__.py
  __main__.py          # RenderDoc 扩展入口
  extension.json
  install.bat
  core/
    mesh_io.py         # 读取 RenderDoc Qt 表格数据
    vertex_attr.py     # 属性别名和回退规则
    math_utils.py      # 坐标变换矩阵
  exporters/
    fbx_writer.py      # FBX ASCII 7.3 导出
    obj_writer.py      # Wavefront OBJ 导出
    fbx_skin.py        # 骨骼/蒙皮导出（stub）
    vsout_writer.py    # VS Output 数据读取
    tex_writer.py      # 贴图导出
    shader_writer.py   # Shader 导出
  pipeline/
    single.py          # 单次导出流程
    batch.py           # 批量导出流程
    helpers.py         # 公共工具函数
  ui/
    dialog.py          # 导出选项对话框
    progress.py        # 进度条组件
  util/
    fbx_binary.py      # FBX 二进制格式编码
```

---

## 已知限制

- VS Output 需要 GPU Replay 支持，部分抓帧文件不可用
- 批量导出期间 RenderDoc UI 会跳帧，属正常现象
- 导出的 FBX 不含 GlobalSettings 坐标系元数据，需要在目标软件里手动设置导入轴向

---

## 参考

- [aRincloud/renderdoc2fbx-main](https://github.com/aRincloud/renderdoc2fbx-main)
- [chineseoldghost/csv2fbx](https://github.com/chineseoldghost/csv2fbx)
- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)

---

# ResourcesExporter

A RenderDoc resource export plugin supporting FBX/OBJ mesh export, along with texture and shader export.

Rebuilt from [renderdoc2fbx](https://github.com/aRincloud/renderdoc2fbx-main) and [csv2fbx](https://github.com/chineseoldghost/csv2fbx).

---

## Installation

1. Run `install.bat` — the script automatically locates your RenderDoc extensions directory and copies the files
2. Restart RenderDoc, or go to `Tools → Manage Extensions → ResourcesExporter → Reload`
3. In the Mesh Preview panel, click the plugin icon (a yellow puzzle piece) — the menu will show **Export Resource** and **Quick Export**

**System Requirements**: RenderDoc >= 1.17, Windows

---

## Usage

### Standard Export

Mesh Preview → Plugin → Export Resource. A dialog with export options will appear. Configure as needed, click OK, then choose a save path.

### Quick Export

Mesh Preview → Plugin → Quick Export. Skips the options dialog and uses the last saved configuration — only prompts for a save location.

### Batch Export

Enter an EID range in the **Batch EID** field of the dialog, click OK, then choose an output root directory. Each EID gets its own subfolder, e.g. `eid_00100/`.

Format: `100,200-210,300` (comma-separated individual EIDs; hyphen denotes an inclusive range)

---

## Options Reference

### Export Mode

| Option | Description |
|---|---|
| VS Input | Raw vertex buffer data in object space coordinates |
| VS Output | Vertex shader post-transform data; requires GPU Replay |

Both can be enabled simultaneously — each produces a separate file.

### Engine Preset

Selecting an engine auto-fills the attribute mapping table. If the auto-filled result is incorrect (common with Vulkan captures), click **Auto-detect Attributes** to re-match based on the actual attribute names present.

### Attribute Mapping

These correspond to the column names shown in the Mesh Viewer table. Names are **case-sensitive**.

Common patterns:

- Unity / DX11: `POSITION`, `NORMAL`, `TEXCOORD0`, `TEXCOORD1`
- Unreal / DX12: `ATTRIBUTE0` (position), `ATTRIBUTE2` (normal), `ATTRIBUTE5` (UV)
- Vulkan: `_input0`, `_input1`, `_input2`, …

Empty channels are not written to the output file.

### UV Options

If UVs appear flipped vertically or horizontally after importing into your engine, toggle **Flip V** or **Flip U** (DX and OpenGL define UV origins differently).

### Texture Export

- **Export Inputs**: Export all input textures bound to the current draw call
- **Export Outputs**: Export render targets (color + depth)
- Supported formats: PNG / DDS / TGA / BMP / HDR / EXR

### Shader Export

Select the shader stage (VS / PS / GS / HS / DS / CS). Choose **Disasm txt** for disassembly text output, or **Binary** for raw DXBC/SPIR-V bytecode.

---

## Directory Structure

```
ResourcesExporter/
  __init__.py
  __main__.py          # RenderDoc extension entry point
  extension.json
  install.bat
  core/
    mesh_io.py         # Reads RenderDoc Qt table data
    vertex_attr.py     # Attribute aliases and fallback rules
    math_utils.py      # Coordinate transformation matrices
  exporters/
    fbx_writer.py      # FBX ASCII 7.3 export
    obj_writer.py      # Wavefront OBJ export
    fbx_skin.py        # Skeleton / skinning export (stub)
    vsout_writer.py    # VS Output data reader
    tex_writer.py      # Texture export
    shader_writer.py   # Shader export
  pipeline/
    single.py          # Single-shot export pipeline
    batch.py           # Batch export pipeline
    helpers.py         # Shared utility functions
  ui/
    dialog.py          # Export options dialog
    progress.py        # Progress bar component
  util/
    fbx_binary.py      # FBX binary format encoding
```

---

## Known Limitations

- VS Output requires GPU Replay support and may not be available for some captures
- During batch export the RenderDoc UI will skip frames — this is expected
- Exported FBX files do not include GlobalSettings coordinate system metadata; you may need to adjust the import axis manually in your target application

---

## References

- [aRincloud/renderdoc2fbx-main](https://github.com/aRincloud/renderdoc2fbx-main)
- [chineseoldghost/csv2fbx](https://github.com/chineseoldghost/csv2fbx)
- [RenderDoc Python API](https://renderdoc.org/docs/python_api/index.html)
