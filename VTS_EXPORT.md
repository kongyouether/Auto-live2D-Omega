# VTube Studio 导出（实验性）

本工程的预览运行时是自研 WebGL 网格，不是 Live2D Cubism。VTube Studio 只接受包含
`.model3.json`、`.moc3` 和纹理的 Cubism Runtime 模型，因此不能把浏览器里的顶点数组直接
改名后导入。

当前桌面版提供一个隔离的导出桥接：

1. 将已加载的 PSD 交给外部 `image2live2d` 适配器。
2. 生成 Cubism 参数、ArtMesh、纹理图集、物理和动作。
3. 生成完整 VTube Studio 模型文件夹与 ZIP。
4. 校验 MOC3 文件头及 `model3.json` 的全部关键引用。

## 使用

1. 通过 `run.bat` 启动 Python 桌面版。
2. 加载分层 PSD，等待模型显示完成。
3. 勾选“我了解该功能使用实验性的非官方 MOC3 写入器”。
4. 点击“导出 VTube Studio 模型”。
5. 输出位于 `exports/vts/<时间>-<模型名>/`。

其中 `<模型名>_vts/` 是可复制到 VTube Studio `Live2DModels` 的文件夹，旁边的 ZIP 是同一
模型的压缩包，`export-report.json` 记录适配器与结构校验结果。

## 适配器位置

程序按以下顺序寻找适配器：

1. 环境变量 `AUTO_VTS_ADAPTER_ROOT` 指向的目录。
2. 本工程相邻的 `../_research/image2live2d`。

适配器目录必须包含：

- `.venv/Scripts/python.exe`
- `tools/emit_cubism_bundle.py`

可使用以下命令安装并运行固定版本的适配器测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_vts_export.ps1 -IUnderstandExperimentalMoc3
```

## 验证记录

2026-08-28 使用 `Live2D_简化平面版_v4.psd` 完成端到端验证：

- 生成 35 个 ArtMesh、38 个参数、10 条物理链。
- 104 项相关格式与 PSD 测试通过（2 项按条件跳过）。
- Cubism Viewer 5.3.03 成功打开生成的 `model.model3.json`。
- VTube Studio 1.35.10 识别模型并自动生成 `model.vtube.json`。

## 许可边界

`.model3.json`、`.physics3.json` 等 JSON 文件可公开生成；`.moc3` 是 Cubism Runtime 的关键
二进制模型。当前适配器使用非官方、逆向实现的 MOC3 写入器，因此本功能明确标为实验性，
不应在未自行确认 Live2D 许可条件前用于商业发布或再分发。稳妥的正式发布流程仍是使用
Cubism Editor 创建/确认模型，并由 Editor 执行“导出为 MOC3”。
