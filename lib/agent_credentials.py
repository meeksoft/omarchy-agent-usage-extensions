"""Shared credential resolution for this plugin's collectors.

Every collector needs the same thing: a token that may sit in Omarchy's
per-agent config, in some tool's config file, or in the environment. Keeping
one implementation means a fix or a review applies to every agent at once —
the alternative let ANTHROPIC_AUTH_TOKEN sit unexamined in the GLM key list
while the default endpoint pointed at a third party.

The bar panel runs under quickshell, which never sources a shell rc, so a key
exported from ~/.zshrc reaches a terminal but not the panel. Files are the
reliable source there; the environment is honoured for terminal use.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Sequence

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def agent_config_path(agent_id: str) -> Path:
    """Omarchy's per-agent config file, the preferred home for a key."""
    return config_home() / "omarchy" / "agents" / f"{agent_id}.json"


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=value profile file without evaluating shell code."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def read_json_env(path: Path) -> dict[str, str]:
    """Read env values from a JSON config file.

    Claude Code nests them under "env"; an Omarchy agent config keeps its
    settings at the top level. Accept both, with the nested block winning.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    values = {str(key): str(value) for key, value in data.items()
              if isinstance(value, (str, int, float))}
    env = data.get("env")
    if isinstance(env, dict):
        values.update({str(key): str(value) for key, value in env.items()
                       if value is not None})
    return values


def read_any(path: Path) -> dict[str, str]:
    return read_env_file(path) if path.suffix == ".env" else read_json_env(path)


def agent_values(agent_id: str, extra_paths: Sequence[Path] = ()) -> dict[str, str]:
    """Settings for one agent.

    Omarchy's per-agent config is consulted first and an earlier file always
    wins, so a legacy location can stay supported without overriding the
    preferred one. The process environment is applied last and overrides every
    file, which keeps a one-off shell export useful for debugging.
    """
    values: dict[str, str] = {}
    for path in (agent_config_path(agent_id), *extra_paths):
        for key, value in read_any(path).items():
            values.setdefault(key, value)
    values.update({key: value for key, value in os.environ.items() if value})
    return values


def first_present(values: dict[str, str], names: Iterable[str]) -> str:
    """The first name that carries a value, so callers order by specificity."""
    return next((values.get(name, "") for name in names if values.get(name)), "")
