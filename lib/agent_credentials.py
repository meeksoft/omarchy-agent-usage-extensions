"""Shared credential resolution for this plugin's collectors.

Every collector needs the same thing: a token that may sit in Omarchy's
per-agent config, in some tool's config file, or in the environment. Keeping
one implementation means a fix or a review applies to every agent at once —
the alternative let ANTHROPIC_AUTH_TOKEN sit unexamined in the GLM key list
while the default endpoint pointed at a third party.

The bar panel runs under quickshell, which never sources a shell rc, so a key
exported from ~/.zshrc reaches a terminal but not the panel. Files are the
reliable source there; the environment is honoured for terminal use. Where a
coding tool already keeps a provider key on disk — opencode, and kilo, its
fork — it is read in place rather than asking the user for a copy.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_PLACEHOLDER = re.compile(r"\{(env|file):([^}]*)\}")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))


def display_path(path: Path) -> str:
    """A path as help text shows it, with the home directory folded to ~."""
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


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


@dataclass(frozen=True)
class Source:
    """One place an agent's settings came from, labelled for help text."""

    label: str
    path: Path | None
    values: dict[str, str]


def agent_sources(agent_id: str, extra_paths: Sequence[Path] = ()) -> list[Source]:
    """Every place an agent's settings may live, highest precedence first.

    The process environment leads, which keeps a one-off shell export useful
    for debugging. Omarchy's per-agent config follows, then any legacy paths,
    so a legacy location can stay supported without overriding the preferred
    one. Sources that hold nothing are left out.
    """
    sources = [Source("the environment", None, {key: value for key, value in os.environ.items() if value})]
    for path in (agent_config_path(agent_id), *extra_paths):
        if values := read_any(path):
            sources.append(Source(display_path(path), path, values))
    return sources


def merged_values(sources: Iterable[Source]) -> dict[str, str]:
    """One mapping in which an earlier source always wins."""
    values: dict[str, str] = {}
    for source in sources:
        for key, value in source.values.items():
            values.setdefault(key, value)
    return values


def agent_values(agent_id: str, extra_paths: Sequence[Path] = ()) -> dict[str, str]:
    """Settings for one agent, merged in agent_sources order."""
    return merged_values(agent_sources(agent_id, extra_paths))


def first_present(values: Mapping[str, str], names: Iterable[str]) -> str:
    """The first name that carries a value, so callers order by specificity."""
    return next((values.get(name, "") for name in names if values.get(name)), "")


# ------------------------------------------------------------ opencode family


@dataclass(frozen=True)
class ToolKey:
    """An API key a coding tool would send to one provider."""

    key: str
    base_url: str
    provider_id: str
    source: str


# (tool, environment prefix). Kilo is an opencode fork that kept its layout and
# renamed the directories and variables.
OPENCODE_FAMILY = (("opencode", "OPENCODE"), ("kilo", "KILO"))


def strip_jsonc(text: str) -> str:
    """Turn JSONC — comments and trailing commas allowed — into plain JSON."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                out.append(text[i:i + 2])
                i += 2
                continue
            in_string = ch != '"'
        elif ch == '"':
            in_string = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        elif ch in "}]":
            j = len(out)
            while j and out[j - 1].isspace():
                j -= 1
            if j and out[j - 1] == ",":
                del out[j - 1]
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_jsonc(text: str) -> Any:
    try:
        return json.loads(strip_jsonc(text))
    except ValueError:
        return None


def resolve_placeholders(value: str, env: Mapping[str, str], base_dir: Path) -> str:
    """Expand opencode's {env:NAME} and {file:path} substitutions.

    A relative {file:} path is taken from the config file's directory, as the
    tool does. Anything unresolvable expands to nothing, so an unset variable
    reads as no key rather than as the literal placeholder.
    """
    def replace(match: re.Match[str]) -> str:
        kind, argument = match.group(1), match.group(2).strip()
        if kind == "env":
            return env.get(argument, "")
        path = Path(argument).expanduser()
        try:
            return (path if path.is_absolute() else base_dir / path).read_text().strip()
        except OSError:
            return ""

    return _PLACEHOLDER.sub(replace, value)


def _tool_configs(tool: str, prefix: str) -> Iterator[tuple[dict[str, Any], Path, str]]:
    """(config, directory for {file:} paths, label), highest precedence first.

    Mirrors the tool's global lookup: inline content, then the extra config
    directory, then the custom config file, then the user config directory.
    Project configs are skipped; the panel has no project.
    """
    names = tuple(dict.fromkeys((f"{tool}.jsonc", f"{tool}.json", "opencode.jsonc", "opencode.json", "config.json")))
    if content := os.environ.get(f"{prefix}_CONFIG_CONTENT"):
        if isinstance(data := _parse_jsonc(content), dict):
            yield data, Path.cwd(), f"${prefix}_CONFIG_CONTENT"
    paths: list[Path] = []
    if directory := os.environ.get(f"{prefix}_CONFIG_DIR"):
        paths += [Path(directory).expanduser() / name for name in names]
    if custom := os.environ.get(f"{prefix}_CONFIG"):
        paths.append(Path(custom).expanduser())
    paths += [config_home() / tool / name for name in names]
    for path in dict.fromkeys(paths):
        try:
            data = _parse_jsonc(path.read_text())
        except OSError:
            continue
        if isinstance(data, dict):
            yield data, path.parent, display_path(path)


def _provider_options(config: Mapping[str, Any], provider_id: str) -> Mapping[str, Any]:
    provider = config.get("provider")
    entry = provider.get(provider_id) if isinstance(provider, dict) else None
    options = entry.get("options") if isinstance(entry, dict) else None
    return options if isinstance(options, dict) else {}


def _database_key(tool: str, provider_id: str) -> tuple[str, str]:
    """The newest active API key in the tool's credential table."""
    path = data_home() / tool / f"{tool}.db"
    if not path.is_file():
        return "", ""
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)) as db:
            rows = db.execute(
                "SELECT value FROM credential WHERE integration_id = ? AND (active IS NULL OR active != 0)"
                " ORDER BY time_created DESC",
                (provider_id,),
            ).fetchall()
    except (sqlite3.Error, ValueError):
        return "", ""
    for (raw,) in rows:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("type") == "key" and isinstance(value.get("key"), str) and value["key"]:
            return value["key"], display_path(path)
    return "", ""


def _auth_json_key(tool: str, provider_id: str) -> tuple[str, str]:
    """An API key in the tool's older auth.json store, which newer releases import into the database."""
    path = data_home() / tool / "auth.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return "", ""
    entry = data.get(provider_id) if isinstance(data, dict) else None
    if isinstance(entry, dict) and entry.get("type") == "api" and isinstance(entry.get("key"), str) and entry["key"]:
        return entry["key"], display_path(path)
    return "", ""


def opencode_family_keys(provider_ids: Sequence[str], env: Mapping[str, str] | None = None) -> Iterator[ToolKey]:
    """Keys opencode or kilo would use for these providers, best first.

    Providers are taken in the order given, so a caller lists the most
    specific first. For each, every tool is asked in turn, and within a tool a
    key in the provider's config options comes before a stored login, as the
    tool itself decides. {env:} placeholders resolve against `env`, which
    defaults to the process environment.
    """
    env = os.environ if env is None else env
    configs = {tool: list(_tool_configs(tool, prefix)) for tool, prefix in OPENCODE_FAMILY}
    for provider_id in provider_ids:
        for tool, _ in OPENCODE_FAMILY:
            options = [(_provider_options(data, provider_id), base_dir, label) for data, base_dir, label in configs[tool]]
            base_url = next((str(o["baseURL"]) for o, _, _ in options if isinstance(o.get("baseURL"), str) and o["baseURL"]), "")
            for option, base_dir, label in options:
                raw = option.get("apiKey")
                if isinstance(raw, str) and (key := resolve_placeholders(raw, env, base_dir).strip()):
                    yield ToolKey(key, base_url, provider_id, label)
            for reader in (_database_key, _auth_json_key):
                key, label = reader(tool, provider_id)
                if key:
                    yield ToolKey(key, base_url, provider_id, label)
