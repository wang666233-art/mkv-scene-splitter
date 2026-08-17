#!/usr/bin/env python3
"""Split an MKV, then run NarratoAI's TTS-to-Jianying workflow.

The implementation intentionally stays in NarratoAI because it imports that
project's schema and draft builder.  This entry point locates NarratoAI and
re-executes the implementation with NarratoAI's own virtual environment.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


TARGET_RELATIVE_PATH = Path("scripts/azure_tts_to_jianying.py")


def extract_wrapper_options(
    arguments: list[str],
) -> tuple[str | None, str | None, str | None, bool, bool, bool, list[str]]:
    """Remove wrapper-only options and leave all TTS options untouched."""
    narrato_root: str | None = None
    mkv_path: str | None = None
    scene_output: str | None = None
    show_info = False
    force_scenes = False
    skip_scene_split = False
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--wrapper-info":
            show_info = True
        elif argument == "--force-scenes":
            force_scenes = True
        elif argument == "--skip-scene-split":
            skip_scene_split = True
        elif argument in {"--narrato-root", "--mkv", "--scene-output"}:
            index += 1
            if index >= len(arguments):
                raise RuntimeError(f"{argument} 后缺少路径")
            if argument == "--narrato-root":
                narrato_root = arguments[index]
            elif argument == "--mkv":
                mkv_path = arguments[index]
            else:
                scene_output = arguments[index]
        elif argument.startswith("--narrato-root="):
            narrato_root = argument.split("=", 1)[1]
        elif argument.startswith("--mkv="):
            mkv_path = argument.split("=", 1)[1]
        elif argument.startswith("--scene-output="):
            scene_output = argument.split("=", 1)[1]
        else:
            forwarded.append(argument)
        index += 1
    return (
        narrato_root,
        mkv_path,
        scene_output,
        show_info,
        force_scenes,
        skip_scene_split,
        forwarded,
    )


def resolve_narrato_root(explicit: str | None = None) -> Path:
    repo_root = Path(__file__).resolve().parent
    configured = explicit or os.environ.get("NARRATOAI_ROOT")
    candidates = (
        [configured]
        if configured
        else [
            str(repo_root.parent / "NarratoAI-local"),
            str(Path.cwd() / "NarratoAI-local"),
            str(Path.home() / "NarratoAI-local"),
        ]
    )
    checked: list[Path] = []
    for raw_candidate in candidates:
        if not raw_candidate:
            continue
        candidate = Path(raw_candidate).expanduser().resolve()
        if candidate in checked:
            continue
        checked.append(candidate)
        if (
            (candidate / TARGET_RELATIVE_PATH).is_file()
            and (candidate / "app/models/schema.py").is_file()
            and (candidate / "app/services/jianying_draft_builder.py").is_file()
        ):
            return candidate
    locations = "、".join(str(path) for path in checked) or "无"
    raise RuntimeError(
        "找不到完整的 NarratoAI 项目。请设置 NARRATOAI_ROOT，或传入 "
        f"--narrato-root。已检查：{locations}"
    )


def choose_python(narrato_root: Path) -> Path:
    candidates = [
        narrato_root / ".venv/bin/python",
        narrato_root / ".venv/Scripts/python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            # Do not resolve the symlink: Python uses the .venv path to locate
            # pyvenv.cfg and activate that environment's site-packages.
            return candidate.absolute()
    raise RuntimeError(
        f"找不到 NarratoAI 虚拟环境：{narrato_root / '.venv'}。"
        "请先在 NarratoAI 项目中安装依赖。"
    )


def normalize_interactive_path(value: str) -> str:
    return value.strip().strip('"').strip("'")


def run_scene_split(
    mkv_path: str,
    scene_output: str | None = None,
    force: bool = False,
) -> None:
    splitter = Path(__file__).resolve().with_name("split_scenes.py")
    command = [sys.executable, str(splitter), mkv_path]
    if scene_output:
        command.extend(["--output", scene_output])
    if force:
        command.append("--force")
    print("\n第一步：检测并无损切割 MKV，同时提取简体中文字幕。", flush=True)
    subprocess.run(command, check=True)
    print("\n第二步：进入 TTS → 剪映草稿流程。\n", flush=True)


def main(arguments: list[str] | None = None) -> int:
    (
        explicit_root,
        mkv_path,
        scene_output,
        show_info,
        force_scenes,
        skip_scene_split,
        forwarded,
    ) = extract_wrapper_options(list(sys.argv[1:] if arguments is None else arguments))
    narrato_root = resolve_narrato_root(explicit_root)
    python = choose_python(narrato_root)
    target = (narrato_root / TARGET_RELATIVE_PATH).resolve()

    if show_info:
        print(f"NarratoAI：{narrato_root}")
        print(f"Python：{python}")
        print(f"脚本：{target}")
        return 0

    showing_help = "--help" in forwarded or "-h" in forwarded
    if not skip_scene_split and not showing_help:
        if not mkv_path:
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "非交互模式下请用 --mkv 指定待分割文件，"
                    "或用 --skip-scene-split 跳过镜头切割。"
                )
            mkv_path = normalize_interactive_path(input("请输入要分割的 MKV 文件路径："))
        if not mkv_path:
            raise RuntimeError("MKV 文件路径不能为空")
        run_scene_split(mkv_path, scene_output, force_scenes)

    os.execv(str(python), [str(python), str(target), *forwarded])
    return 0  # pragma: no cover - os.execv only returns on failure.


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
