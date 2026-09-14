"""Credential resolution: the shared opencode/kilo readers and the GLM collector.

Run from the repository root with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import agent_credentials  # noqa: E402


def load_collector(name: str):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(ROOT / "bin" / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    # Dataclasses look their module up by name while the class is built.
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


glm = load_collector("omarchy-agent-usage-glm")


class FixtureHome(unittest.TestCase):
    """Each test runs against an empty home and an environment holding only HOME."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, relative: str, content) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return path

    def tool_config(self, tool: str, options: dict, provider: str = "zai-coding-plan") -> Path:
        return self.write(f".config/{tool}/{tool}.json", {"provider": {provider: {"options": options}}})

    def credential_db(self, tool: str, rows) -> None:
        path = self.home / f".local/share/{tool}/{tool}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE credential (id text PRIMARY KEY, integration_id text, label text NOT NULL,"
                " value text NOT NULL, connector_id text, method_id text, active integer,"
                " time_created integer NOT NULL, time_updated integer NOT NULL)"
            )
            for index, (integration, value, active) in enumerate(rows):
                db.execute(
                    "INSERT INTO credential VALUES (?, ?, 'Imported', ?, NULL, NULL, ?, ?, ?)",
                    (f"cred{index}", integration, json.dumps(value), active, index, index),
                )
            db.commit()


class StripJsonc(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        text = '{\n  // line\n  "a": "x // kept", /* block */ "b": [1, 2,],\n}'
        self.assertEqual(json.loads(agent_credentials.strip_jsonc(text)), {"a": "x // kept", "b": [1, 2]})

    def test_strings_are_left_alone(self):
        text = '{"a": "say \\"hi\\", }", "b": "/* no */"}'
        self.assertEqual(json.loads(agent_credentials.strip_jsonc(text)), {"a": 'say "hi", }', "b": "/* no */"})


class OpencodeFamilyKeys(FixtureHome):
    def keys(self, env=None):
        return list(agent_credentials.opencode_family_keys(glm.PROVIDER_IDS, env or {}))

    def test_literal_config_key(self):
        self.tool_config("opencode", {"apiKey": "literal", "baseURL": "https://api.z.ai/api/coding/paas/v4"})
        [found] = self.keys()
        self.assertEqual(found, agent_credentials.ToolKey(
            "literal", "https://api.z.ai/api/coding/paas/v4", "zai-coding-plan", "~/.config/opencode/opencode.json"))

    def test_jsonc_config(self):
        self.write(".config/opencode/opencode.jsonc",
                   '{ // comment\n "provider": {"zai": {"options": {"apiKey": "k",},},},\n}')
        self.assertEqual([k.key for k in self.keys()], ["k"])

    def test_env_placeholder_resolves_only_when_set(self):
        self.tool_config("kilo", {"apiKey": "{env:ZAI_CODING_PLAN_API_KEY}"})
        self.assertEqual(self.keys(), [])
        self.assertEqual([k.key for k in self.keys({"ZAI_CODING_PLAN_API_KEY": "from-env"})], ["from-env"])

    def test_file_placeholder_is_relative_to_the_config(self):
        self.write(".config/opencode/zai.key", "from-file\n")
        self.tool_config("opencode", {"apiKey": "{file:zai.key}"})
        self.assertEqual([k.key for k in self.keys()], ["from-file"])

    def test_auth_json_api_entry(self):
        self.write(".local/share/opencode/auth.json", {
            "zai-coding-plan": {"type": "api", "key": "stored"},
            "github-copilot": {"type": "oauth", "refresh": "r", "access": "a", "expires": 0},
        })
        self.assertEqual([(k.key, k.source) for k in self.keys()], [("stored", "~/.local/share/opencode/auth.json")])

    def test_credential_database_newest_active_key(self):
        self.credential_db("kilo", [
            ("zai-coding-plan", {"type": "key", "key": "old"}, None),
            ("zai-coding-plan", {"type": "key", "key": "new"}, 1),
            ("zai-coding-plan", {"type": "key", "key": "inactive"}, 0),
            ("zai-coding-plan", {"type": "oauth", "access": "a"}, None),
            ("openai", {"type": "key", "key": "other"}, None),
        ])
        self.assertEqual([(k.key, k.source) for k in self.keys()], [("new", "~/.local/share/kilo/kilo.db")])

    def test_config_key_outranks_stored_key(self):
        self.tool_config("opencode", {"apiKey": "config"})
        self.write(".local/share/opencode/auth.json", {"zai-coding-plan": {"type": "api", "key": "stored"}})
        self.assertEqual([k.key for k in self.keys()], ["config", "stored"])

    def test_coding_plan_provider_outranks_generic_across_tools(self):
        self.tool_config("opencode", {"apiKey": "generic"}, provider="zai")
        self.tool_config("kilo", {"apiKey": "plan"})
        self.assertEqual([k.key for k in self.keys()], ["plan", "generic"])

    def test_config_path_override(self):
        self.tool_config("opencode", {"apiKey": "global"}, provider="zai")
        self.write("elsewhere/custom.json", {"provider": {"zai": {"options": {"apiKey": "custom"}}}})
        os.environ["OPENCODE_CONFIG"] = str(self.home / "elsewhere/custom.json")
        self.assertEqual([(k.key, k.source) for k in self.keys()],
                         [("custom", "~/elsewhere/custom.json"), ("global", "~/.config/opencode/opencode.json")])

    def test_inline_config_content(self):
        os.environ["KILO_CONFIG_CONTENT"] = '{"provider": {"zai": {"options": {"apiKey": "inline"}}}}'
        self.assertEqual([(k.key, k.source) for k in self.keys()], [("inline", "$KILO_CONFIG_CONTENT")])

    def test_xdg_directories(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "cfg")
        os.environ["XDG_DATA_HOME"] = str(self.home / "data")
        self.tool_config("kilo", {"apiKey": "default-location"})
        self.write("cfg/kilo/kilo.json", {"provider": {"zai-coding-plan": {"options": {"apiKey": "cfg"}}}})
        self.write("data/kilo/auth.json", {"zai": {"type": "api", "key": "data"}})
        self.assertEqual([k.key for k in self.keys()], ["cfg", "data"])


class GlmCredentials(FixtureHome):
    def test_nothing_found(self):
        self.assertIsNone(glm.credentials())
        record = glm.collect()
        self.assertFalse(record["ready"])
        for place in ("~/.config/omarchy/agents/glm.json", "opencode", "kilo", "~/.config/claude-profiles/glm.env"):
            self.assertIn(place, record["authHelpText"])

    def test_named_key_outranks_tools(self):
        self.write(".config/omarchy/agents/glm.json", {"ZAI_CODING_PLAN_API_KEY": "omarchy"})
        self.tool_config("opencode", {"apiKey": "tool"})
        found = glm.credentials()
        self.assertEqual((found.token, found.base_url, found.source),
                         ("omarchy", glm.DEFAULT_BASE_URL, "~/.config/omarchy/agents/glm.json"))

    def test_environment_outranks_files(self):
        os.environ["ZAI_API_KEY"] = "env"
        self.write(".config/omarchy/agents/glm.json", {"ZAI_CODING_PLAN_API_KEY": "file"})
        self.assertEqual((glm.credentials().token, glm.credentials().source), ("env", "the environment"))

    def test_named_key_borrows_a_zai_base_url_from_another_source(self):
        self.write(".config/omarchy/agents/glm.json", {"ZAI_API_KEY": "k"})
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"}})
        self.assertEqual(glm.credentials().base_url, "https://open.bigmodel.cn/api/anthropic")
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}})
        self.assertEqual(glm.credentials().base_url, glm.DEFAULT_BASE_URL)

    def test_named_key_with_a_foreign_base_url_in_its_own_source_is_refused(self):
        self.write(".claude/settings.json", {"env": {"ZAI_API_KEY": "k", "ANTHROPIC_BASE_URL": "https://example.com"}})
        with mock.patch.object(glm.urllib.request, "urlopen") as urlopen:
            record = glm.collect()
        urlopen.assert_not_called()
        self.assertIn("not a supported Z.ai endpoint", record["authHelpText"])
        self.assertIn("~/.claude/settings.json", record["authHelpText"])

    def test_tool_key_and_its_base_url(self):
        self.tool_config("opencode", {"apiKey": "tool", "baseURL": "https://open.bigmodel.cn/api/coding/paas/v4"})
        found = glm.credentials()
        self.assertEqual((found.token, found.base_url, found.source),
                         ("tool", "https://open.bigmodel.cn/api/coding/paas/v4", "~/.config/opencode/opencode.json"))

    def test_tool_key_with_a_foreign_base_url_uses_the_provider_host(self):
        self.tool_config("kilo", {"apiKey": "tool", "baseURL": "https://proxy.example.com/v1"},
                         provider="zhipuai-coding-plan")
        self.assertEqual(glm.credentials().base_url, "https://open.bigmodel.cn")

    def test_tool_placeholder_resolves_from_glm_files(self):
        self.tool_config("opencode", {"apiKey": "{env:GLM_KEY_FOR_TOOLS}"})
        self.write(".config/claude-profiles/glm.env", "export GLM_KEY_FOR_TOOLS=shared\n")
        self.assertEqual(glm.credentials().token, "shared")

    def test_glm_profile_token_without_a_base_url(self):
        self.write(".config/claude-profiles/glm.env", "export ANTHROPIC_AUTH_TOKEN=profile\n")
        found = glm.credentials()
        self.assertEqual((found.token, found.base_url, found.source),
                         ("profile", glm.DEFAULT_BASE_URL, "~/.config/claude-profiles/glm.env"))

    def test_glm_profile_token_with_a_foreign_base_url(self):
        self.write(".config/claude-profiles/glm.env",
                   "ANTHROPIC_AUTH_TOKEN=profile\nANTHROPIC_BASE_URL=https://api.anthropic.com\n")
        self.assertIsNone(glm.credentials())

    def test_settings_token_needs_a_zai_base_url_in_the_same_file(self):
        self.write(".claude/settings.json", {"env": {"ANTHROPIC_AUTH_TOKEN": "anthropic"}})
        os.environ["ANTHROPIC_BASE_URL"] = "https://api.z.ai/api/anthropic"
        self.assertIsNone(glm.credentials())
        self.write(".claude/settings.json", {"env": {
            "ANTHROPIC_AUTH_TOKEN": "glm", "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"}})
        self.assertEqual((glm.credentials().token, glm.credentials().source), ("glm", "~/.claude/settings.json"))

    def test_environment_token_needs_a_zai_base_url(self):
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "anthropic"
        self.assertIsNone(glm.credentials())
        os.environ["ANTHROPIC_BASE_URL"] = "https://api.z.ai/api/anthropic"
        self.assertEqual(glm.credentials().token, "anthropic")

    def test_rejection_names_the_key_source(self):
        self.tool_config("opencode", {"apiKey": "bad"})
        error = urllib.error.HTTPError("https://api.z.ai", 401, "Unauthorized", None, None)
        with mock.patch.object(glm.urllib.request, "urlopen", side_effect=error) as urlopen:
            record = glm.collect()
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://api.z.ai/api/monitor/usage/quota/limit")
        self.assertEqual(urlopen.call_args.args[0].get_header("Authorization"), "bad")
        self.assertIn("HTTP 401", record["authHelpText"])
        self.assertIn("~/.config/opencode/opencode.json", record["authHelpText"])


if __name__ == "__main__":
    unittest.main()
