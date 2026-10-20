# MKV Scene Splitter 与 TTS 剪映工具

把一部**带中文字幕的 MKV 视频**，自动变成一份**剪映草稿**：检测并切分镜头、把解说稿合成为与原片对齐的配音和字幕、直接写进 `JianyingPro Drafts` 目录。

拆镜头、语音合成、词级时间戳对齐、静音裁剪、剪映草稿生成全部内置，**不依赖 NarratoAI 或其他外部服务**。

```text
MKV 视频 ──► 镜头分割(重新编码) ──► TTS 合成(词级对齐) ──► 静音裁剪分段 ──► 剪映草稿
     └──────► 简体中文字幕提取 ─────────────────────────────────────────────┘
```

## 功能一览

| 能力 | 说明 |
|---|---|
| 镜头检测 | PySceneDetect `detect-content`（默认）或 `detect-adaptive` |
| 镜头切割 | FFmpeg 重新编码，切割点强制 I 帧，保证每段从准确边界开始；音频流直接复制 |
| 字幕提取 | 从 MKV 自动识别简体中文文本轨，统一转换为 UTF-8 SRT |
| TTS 合成 | Azure Speech（SSML 风格/角色/语速）或 Fish Audio（SSE 流式 + 时间戳） |
| 字幕对齐 | Azure 用词边界；Fish Audio 用 alignment，**严格校验**字符完整性，不匹配即报错重试 |
| 静音裁剪 | 按字幕区间切片，用 ffmpeg `silenceremove` 去掉首尾与句内停顿 |
| 原片占位 | 解说稿中写 `【播放原片 5 秒】` 即可在配音里插入一段不配音的原片时间 |
| 剪映草稿 | 生成含音频轨 + 字幕轨的完整草稿包（`draft_content.json`、素材索引、`meta_info` 等） |

## 环境要求

- Python 3.10+
- **PySceneDetect**（命令 `scenedetect`）
- **MKVToolNix**（`mkvmerge`、`mkvextract`）
- **FFmpeg / FFprobe**（`ffmpeg`、`ffprobe`）

```bash
# Ubuntu / Debian
sudo apt-get install mkvtoolnix ffmpeg

# PySceneDetect（虚拟环境）
python3 -m venv ~/.venvs/pyscenedetect
~/.venvs/pyscenedetect/bin/pip install scenedetect

# 本项目依赖
uv sync            # 或：pip install -e .
```

> 脚本会自动在 `PATH` 和 `~/.venvs/pyscenedetect/bin/` 中查找 `scenedetect`；装在其他位置时用 `--scenedetect` 指定。

### GPU 加速（可选）

`video_encoder = "auto"` 时的选择逻辑：

1. FFmpeg 未编译 `h264_nvenc` → 用 CPU `libx264`；
2. 编译了 `h264_nvenc`，但会**实际编码一帧测试**（`ffmpeg -encoders` 只说明编译进去，不代表能用）；
3. 测试失败 → 打印提示改用 `libx264`；
4. 编码中 NVENC 初始化失败 → 自动清理半成品片段并回退到 `libx264` 重来。

WSL2 下还需要 `nvidia-smi` 能正常列出 GPU。如果 `PATH` 里的 FFmpeg 不带 NVENC 而 `/usr/bin/ffmpeg` 带，脚本会自动探测并切换；也可以显式指定：

```toml
[scene_split]
ffmpeg = "/usr/bin/ffmpeg"
video_encoder = "h264_nvenc"   # 或 "libx264"
```

## 快速开始

### 1. 只切镜头

```bash
./split_scenes.py 'video.mkv'
```

输出在源视频旁的 `<文件名>_scenes/`：

```text
<文件名>_scenes/
├── scenes.csv                       # PySceneDetect 的时间码列表
├── shots/
│   ├── <文件名>-Shot-001.mkv
│   └── ...
└── subtitles/
    └── <文件名>.zh-Hans.srt
```

常用参数：

```bash
./split_scenes.py video.mkv --dry-run              # 只检查依赖/轨道/输出路径
./split_scenes.py video.mkv --output /path/result  # 指定输出目录
./split_scenes.py video.mkv --force                # 覆盖已有结果
./split_scenes.py video.mkv --subtitle-track 2     # 手动指定字幕轨
./split_scenes.py video.mkv --threshold 3.5 --min-scene-len 1.0s
```

输入可以只写文件名，脚本会在当前目录与用户主目录递归查找；只有一个匹配时自动使用，多个同名文件时列出路径供选择。

### 2. 从 MKV 一路生成剪映草稿

```bash
./azure_tts_to_jianying.py
```

交互模式第一步会询问本次要做什么：

```text
请选择本次要执行的操作：
  1. 先分割 MKV 镜头，再生成 TTS → 剪映草稿
  2. 直接生成 TTS → 剪映草稿（跳过镜头分割）
  3. 仅分割 MKV 镜头，不生成 TTS
请输入数字 [1-3]，直接回车默认 1：
```

非交互用法：

```bash
# 分割 + TTS + 草稿
./azure_tts_to_jianying.py --mkv 'video.mkv' --input '解说词.txt'

# 只做 TTS → 草稿
./azure_tts_to_jianying.py --skip-scene-split --input '解说词.txt'

# 只切镜头
./azure_tts_to_jianying.py --split-only --mkv 'video.mkv'

# 指定镜头目录并覆盖旧结果
./azure_tts_to_jianying.py --mkv 'video.mkv' --scene-output /path/scenes --force-scenes

# 查看当前环境与配置
./azure_tts_to_jianying.py --wrapper-info
```

## 配置

配置优先级：**命令行参数 > 环境变量 > `config.toml` > 默认值**。

```bash
cp config.example.toml config.toml
```

`config.toml` 已被 `.gitignore` 忽略，**不会进仓库**；密钥请只写在这里或环境变量里。

配置文件的查找顺序：`./config.toml` → 仓库根目录 `config.toml` → `~/.config/mkv-scene-splitter/config.toml` → `~/.mkv-scene-splitter.toml`，也可用 `-c/--config` 显式指定。

### `[scene_split]`

| 键 | 默认 | 说明 |
|---|---|---|
| `detector` | `content` | `content` 直接比较相邻帧；`adaptive` 适合抑制镜头内运动误检 |
| `threshold` | `12.0`（adaptive 为 `2.0`） | 越低检测到的镜头越多 |
| `min_content_val` | `8.0` | 最小内容变化值（仅 `adaptive`） |
| `min_scene_len` | `0.3s` | 最短镜头长度 |
| `video_encoder` | `auto` | `auto` / `h264_nvenc` / `libx264` |
| `output_dir` | 空 | 空则用源视频旁 `<文件名>_scenes` |
| `subtitle_track` | 空 | 手动指定字幕轨 ID |
| `ffmpeg` / `scenedetect` / `mkvmerge` / `mkvextract` | 空 | 可执行文件路径覆盖 |

对应环境变量：`SCENE_DETECTOR`、`SCENE_THRESHOLD`、`SCENE_MIN_CONTENT_VAL`、`SCENE_MIN_SCENE_LEN`、`SCENE_VIDEO_ENCODER`、`SCENE_OUTPUT_DIR`、`SCENE_SUBTITLE_TRACK`、`FFMPEG_BIN`、`SCENEDETECT_BIN`、`MKVMERGE_BIN`、`MKVEXTRACT_BIN`。

### `[tts]`

| 键 | 默认 | 说明 |
|---|---|---|
| `engine` | `azure_speech` | `azure_speech` 或 `fish_tts` |
| `rate` | `1.0` | 语速倍率 |
| `chunk_size` | `2500` | 单次 TTS 请求最大字符数（不得小于 100） |
| `subtitle_max_chars` | `30` | 单条字幕最大字符数 |
| `silence_threshold_db` | `-45.0` | 静音检测阈值，取值区间 `-80 ~ -20` |
| `internal_silence_seconds` | `0.08` | 句内停顿裁剪触发长度，区间 `0.03 ~ 2.0` |
| `segment_gap_seconds` | `0.1` | 相邻音频素材间隔，区间 `0 ~ 10` |
| `narration_dir` | 空 | 解说稿查找目录 |
| `input_file` | 空 | 固定解说稿路径 |

对应环境变量：`TTS_ENGINE`、`TTS_RATE`、`TTS_CHUNK_SIZE`、`TTS_SUBTITLE_MAX_CHARS`、`TTS_SILENCE_THRESHOLD_DB`、`TTS_INTERNAL_SILENCE_SECONDS`、`TTS_SEGMENT_GAP_SECONDS`、`NARRATION_DIR`。

### `[azure]`

`speech_key`（必填）、`speech_region`、`voice`、`pitch`、`volume`、`style`、`role`、`style_degree`。

环境变量：`AZURE_SPEECH_KEY`、`AZURE_SPEECH_REGION`、`AZURE_SPEECH_VOICE`、`AZURE_SPEECH_PITCH`、`AZURE_SPEECH_VOLUME`、`AZURE_SPEECH_STYLE`、`AZURE_SPEECH_ROLE`、`AZURE_SPEECH_STYLE_DEGREE`。

### `[fish_tts]`

`api_key`（必填）、`model`、`reference_id`、`api_url`、`temperature`、`top_p`、`chunk_length`、`latency`、`timeout`。

环境变量：`FISH_API_KEY`、`FISH_MODEL`、`FISH_REFERENCE_ID`、`FISH_API_URL`、`FISH_TEMPERATURE`、`FISH_TOP_P`、`FISH_CHUNK_LENGTH`、`FISH_LATENCY`、`FISH_TIMEOUT`。

`api_url` 可以只写域名或 `/v1`，脚本会自动补全为 `/v1/tts/stream/with-timestamp`。

### `[jianying]`

`draft_dir`（必填）、`width`、`height`、`font_name`、`font_size`、`text_fore_color`、`stroke_color`、`stroke_width`、`subtitle_position`（`bottom` / `top` / `center` / `custom`）、`custom_position`、`track_volume`。

环境变量：`JIANYING_DRAFT_DIR`、`DRAFT_WIDTH`、`DRAFT_HEIGHT`、`DRAFT_NAME`、`JIANYING_TRACK_VOLUME`。

### `[proxy]`

`enabled`、`http`、`https`，用于 Fish Audio 请求走代理。

## 解说稿

支持 `--input 文件`、`--text '文本'`、或直接通过标准输入管道传入。

未指定时的自动查找顺序：

1. `[tts].input_file` 配置的路径；
2. 视频同目录下 `<视频名>.txt`、`<视频名>_解说.txt`、`<视频名>_解说词.txt`、`<视频名>_文案.txt`、`<视频名>_稿.txt`；
3. 在 `narration_dir`、视频目录、用户主目录、当前目录中递归找 `<视频名>.txt`；
4. 视频目录下**唯一**的 `.txt` 文件；
5. 当前目录下的 `解说词.txt`、`narration.txt`、`稿.txt`、`文案.txt`。

### 插入原片片段

解说稿中写：

```text
他推开门，看到了三十年后的自己。【播放原片 5 秒】镜头缓缓拉远……
```

`【播放原片 N 秒】`（正整数或小数）会让时间轴跳过 N 秒不做配音，方便把原片画面和字幕对上。

## 工作流程细节

```text
解说稿
  │  按段落/句号分块（不超过 chunk_size）
  ├─► TTS 逐块合成 ──► 词级/字级时间戳
  │                        │
  │                        └─► 按 subtitle_max_chars 切字幕行 ──► 逐行对齐时间
  │
  └─► 按对齐后的字幕区间用 ffmpeg 切片
        首尾留 0.08s / 0.12s 保护量 + 自动静音裁剪
        ──► 每段独立 WAV，按 segment_gap_seconds 排布时间轴
               ──► 写入剪映草稿（音频轨 + 字幕轨）
```

**Fish Audio 的严格对齐**：返回的 SSE 事件里既有音频也有 alignment。脚本会校验 alignment 拼出的字符序列与输入文本**完全一致**（忽略标点与空白），否则直接报错并重试（默认 3 次）——宁可失败，也不用估算时间戳切分导致吞字。`queue`/时长越界同样会被拒绝。

**字幕行对齐**：Azure 走 `align_subtitle_lines`（按词边界精确匹配，剩余部分按字数权重分配时长）；Fish Audio 走 `align_subtitle_lines_strict`（按字符比例在事件内插值，任何不一致立即抛错）。

## 字幕选择规则

只导出原视频已有的文本字幕，**不做 OCR、不做简繁转换**。

- 加分：`简体`、`簡體`、`简中`、`zh-Hans`、`zh-CN`、`chs`；语言为 `chi`/`zho`/`zh`
- 直接排除：`繁体`、`繁中`、`traditional`、`zh-Hant`、`zh-TW`、`cht`
- 支持 SRT、ASS/SSA、WebVTT，输出统一转成 UTF-8 SRT 并**清除 ASS 覆盖块与 HTML 标签**
- 最高分并列或全部无匹配时，列出候选并要求 `--subtitle-track`

> 完整字幕单独导出、而不是从每个镜头里抠出来，是为了避免跨镜头的字幕事件把片段时长拉长、造成末帧静止。

## 项目结构

```text
├── split_scenes.py            # 镜头检测 + 重新编码切割 + 字幕提取（CLI）
├── azure_tts_to_jianying.py   # 组合入口：分割 → TTS → 剪映草稿（CLI）
├── tts_service.py             # 分块、TTS 合成、时间戳对齐、静音裁剪、SRT 写出
├── jianying_draft.py          # 剪映草稿包生成（draft_content.json / 素材索引 / meta_info）
├── config.py                  # TOML 加载与 CLI > env > toml > 默认值 的优先级解析
├── config.example.toml        # 配置模板
├── pyproject.toml / uv.lock   # 依赖与脚本入口
└── tests/                     # pytest 单元测试
```

`pyproject.toml` 暴露了两个命令入口：

```bash
mkv-scene-splitter        # = split_scenes:main
azure-tts-to-jianying     # = azure_tts_to_jianying:main
```

## 测试

```bash
uv run pytest        # 或 .venv/bin/python -m pytest
```

## 故障排查

| 现象 | 处理 |
|---|---|
| `找不到 scenedetect` | 装到 `~/.venvs/pyscenedetect` 或传 `--scenedetect` |
| `FFmpeg 不支持视频编码器 h264_nvenc` | 换带 NVENC 的 FFmpeg，或设 `video_encoder = "libx264"` |
| NVENC 提示回退 CPU | WSL2 下先确认 `nvidia-smi` 正常，或直接固定 `libx264` |
| `无法唯一判断简体中文字幕轨` | 用 `--subtitle-track <ID>` 指定，ID 见报错里的候选列表 |
| `输出文件已存在` | 加 `--force` / `--force-scenes` |
| `Fish Audio alignment 与输入文本不完整匹配` | 合成结果被截断或含异常字符；脚本已自动重试 3 次，持续失败请缩短 `chunk_size` |
| `未配置剪映草稿根目录` | 设置 `[jianying].draft_dir` 或传 `--draft-dir` |

## 安全提示

`config.toml` 含 API Key，已被 `.gitignore` 忽略。提交前请确认没有任何密钥进入版本库：

```bash
git grep -nE 'sk-[A-Za-z0-9]|api_key *= *".+"'
```

`config.toml.save` 是一份**已脱敏**的个人参数备份（不含任何密钥），仅用于记录调好的非敏感选项。
