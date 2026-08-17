# MKV Scene Splitter 与 TTS 剪映工具

使用 PySceneDetect 检测镜头，通过 MKVToolNix 直接复制码流进行拆分，并从原始 MKV 中导出简体中文字幕。

视频、音频和字幕均不重新编码。由于无损拆分只能发生在 HEVC/H.264 等视频的关键帧处，最终片段数可能少于 PySceneDetect 检测到的候选镜头数。

## 依赖

- Python 3.10+
- PySceneDetect（命令名 `scenedetect`）
- MKVToolNix（`mkvmerge` 和 `mkvextract`）

Ubuntu 安装 MKVToolNix：

```bash
sudo apt-get install mkvtoolnix
```

本机若按 `~/.venvs/pyscenedetect` 安装 PySceneDetect，脚本会自动找到；其他位置请激活对应虚拟环境，或传入 `--scenedetect`。

## 使用

直接传入文件：

```bash
./split_scenes.py '/path/to/video.mkv'
```

也可以不传参数，按提示粘贴路径：

```bash
./split_scenes.py
```

默认在源视频旁创建 `<原文件名>_scenes`：

```text
<原文件名>_scenes/
├── scenes.csv
├── shots/
│   ├── <原文件名>-Shot-001.mkv
│   └── ...
└── subtitles/
    └── <原文件名>.zh-Hans.srt
```

指定输出目录：

```bash
./split_scenes.py video.mkv --output /path/to/result
```

只检查环境和字幕轨选择，不生成文件：

```bash
./split_scenes.py video.mkv --dry-run
```

如果字幕元数据无法唯一判断简体轨，脚本会列出候选轨道。此时可明确指定：

```bash
./split_scenes.py video.mkv --subtitle-track 2
```

重新生成已有结果：

```bash
./split_scenes.py video.mkv --force
```

调整检测灵敏度：

```bash
./split_scenes.py video.mkv --threshold 3.5 --min-scene-len 1.0s
```

阈值越低通常检测到的镜头越多。默认使用 PySceneDetect 的 `detect-adaptive`，阈值为 `3.0`，最短镜头为 `0.6s`。

## 字幕选择规则

脚本只导出原视频中的文本字幕，不执行 OCR 或简繁转换。它优先匹配以下元数据：

- `简体`、`简中`、`zh-Hans`、`zh-CN`、`CHS`
- 中文语言代码 `chi`、`zho`、`zh`
- 自动排除标为 `繁体`、`繁中`、`zh-Hant`、`zh-TW`、`CHT` 的轨道

支持导出 SRT、ASS/SSA 和 WebVTT。若多个轨道仍无法区分，会要求使用 `--subtitle-track`，避免误选。

## Azure/Fish TTS → 剪映草稿

仓库同时提供 `azure_tts_to_jianying.py` 入口。它会自动定位相邻的
`NarratoAI-local`，并使用该项目的 `.venv`、配置、数据模型和剪映草稿构建器运行原始实现：

```bash
./azure_tts_to_jianying.py
```

原脚本的所有参数都会原样转发，例如：

```bash
./azure_tts_to_jianying.py \
  --input '/path/to/解说词.txt' \
  --draft-dir '/path/to/JianyingPro Drafts'
```

查看实际使用的项目、Python 和脚本位置：

```bash
./azure_tts_to_jianying.py --wrapper-info
```

如果 NarratoAI 不在当前仓库旁边，可显式指定：

```bash
./azure_tts_to_jianying.py \
  --narrato-root '/path/to/NarratoAI-local' \
  --input '/path/to/解说词.txt'
```

也可以设置环境变量：

```bash
export NARRATOAI_ROOT='/path/to/NarratoAI-local'
./azure_tts_to_jianying.py
```

这个入口没有复制 NarratoAI 的内部模块。TTS 脚本依赖
`app.models.schema` 和 `app.services.jianying_draft_builder`，因此始终调用指定
NarratoAI 工作树中的当前版本，避免两份草稿生成逻辑失去同步。
