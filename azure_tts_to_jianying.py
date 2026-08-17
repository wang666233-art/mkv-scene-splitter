#!/usr/bin/env python3
"""Run NarratoAI's TTS-to-Jianying tool from this utility repository.

The implementation intentionally stays in NarratoAI because it imports that
project's schema and draft builder.  This entry point locates NarratoAI and
re-executes the implementation with NarratoAI's own virtual environment.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


TARGET_RELATIVE_PATH = Path("scripts/azure_tts_to_jianying.py")


def extract_wrapper_options(arguments: list[str]) -> tuple[str | None, bool, list[str]]:
    """Remove wrapper-only options and leave all TTS options untouched."""
    narrato_root: str | None = None
    show_info = False
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--wrapper-info":
            show_info = True
        elif argument == "--narrato-root":
            index += 1
            if index >= len(arguments):
                raise RuntimeError("--narrato-root 后缺少路径")
            narrato_root = arguments[index]
        elif argument.startswith("--narrato-root="):
            narrato_root = argument.split("=", 1)[1]
        else:
            forwarded.append(argument)
        index += 1
    return narrato_root, show_info, forwarded


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


def main(arguments: list[str] | None = None) -> int:
    explicit_root, show_info, forwarded = extract_wrapper_options(
        list(sys.argv[1:] if arguments is None else arguments)
    )
    narrato_root = resolve_narrato_root(explicit_root)
    python = choose_python(narrato_root)
    target = (narrato_root / TARGET_RELATIVE_PATH).resolve()

    if show_info:
        print(f"NarratoAI：{narrato_root}")
        print(f"Python：{python}")
        print(f"脚本：{target}")
        return 0

    os.execv(str(python), [str(python), str(target), *forwarded])
    return 0  # pragma: no cover - os.execv only returns on failure.


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
