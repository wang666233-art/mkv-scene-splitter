"""Configuration loader and setting resolution for mkv-scene-splitter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 兼容
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG_LOCATIONS = [
    Path("config.toml"),
    Path.home() / ".config" / "mkv-scene-splitter" / "config.toml",
    Path.home() / ".mkv-scene-splitter.toml",
]


def find_config_file(explicit_path: str | Path | None = None) -> Path | None:
    """Find configuration file from explicit path or default search locations."""
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"未找到指定的配置文件：{path}")

    repo_config = Path(__file__).resolve().parent / "config.toml"
    candidates = [Path("config.toml").resolve(), repo_config] + [
        loc.expanduser().resolve() for loc in DEFAULT_CONFIG_LOCATIONS[1:]
    ]

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load configuration dictionary from TOML file."""
    config_file = find_config_file(path)
    if not config_file:
        return {}
    with open(config_file, "rb") as f:
        return tomllib.load(f)


def clean_string_value(value: Any) -> str:
    """Normalize string value, stripping whitespace and enclosing single/double quotes."""
    if value is None:
        return ""
    text = str(value).strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    return text


def pick_setting(
    cli_value: Any,
    env_name: str | None = None,
    config_value: Any = None,
    default: Any = None,
    cast_type: type | None = None,
) -> Any:
    """Resolve a setting with precedence: CLI > Environment variable > Config TOML > Default."""
    chosen = None
    if cli_value is not None:
        chosen = cli_value
    elif env_name and os.environ.get(env_name) is not None:
        raw = os.environ.get(env_name, "").strip()
        if raw != "":
            chosen = raw
    elif config_value is not None:
        if isinstance(config_value, str):
            clean_str = clean_string_value(config_value)
            if clean_str != "":
                chosen = clean_str
        else:
            chosen = config_value

    if chosen is None:
        chosen = default

    if chosen is not None and cast_type is not None:
        if cast_type is bool and isinstance(chosen, str):
            return chosen.strip().lower() in {"1", "true", "yes", "on"}
        try:
            return cast_type(chosen)
        except (ValueError, TypeError):
            return default
    return chosen
