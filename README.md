# Omarchy Agent Usage Extensions

An Omarchy bar plugin that extends the built-in Agents panel with GitHub
Copilot and Z.ai GLM Coding Plan quota meters, plus an xAI Grok prepaid-credit
meter.

The plugin retains Omarchy's built-in Claude, Codex, and Fireworks records. Its
provider selector uses a two-column layout with balanced multiline names so
additional providers remain readable.

## Requirements

- Omarchy Quattro with the `omarchy.agents` plugin
- `jq`, `timeout` (coreutils), and Bash 4 or newer
- Python 3.11 or newer, and `uv` to build the Copilot collector's environment
- GitHub Copilot CLI and an authenticated GitHub CLI for Copilot quota
- A Z.ai or BigModel Coding Plan key for GLM quota
- An xAI management key and team ID for the optional Grok prepaid balance

## Install from a local checkout

From the repository root:

```bash
./bin/setup-copilot-runtime
ln -s "$PWD" "$HOME/.config/omarchy/plugins/meeksoft.agent-usage-extensions"
omarchy plugin enable meeksoft.agent-usage-extensions
omarchy restart shell
```

The plugin declares that it replaces `omarchy.agents`, preserving the Agents
widget's role in the bar. For a published repository, use Omarchy's standard
command instead of creating the local development link:

```bash
omarchy plugin add REPOSITORY_URL --enable
```

Third-party Omarchy plugins execute as the current user. Review the repository
before enabling it.

## Copilot collector runtime

`bin/setup-copilot-runtime` creates a virtual environment for the Copilot SDK in
`$XDG_DATA_HOME/omarchy-agent-usage-extensions` (`~/.local/share/...` by
default). It is deliberately a sibling of `$XDG_DATA_HOME/omarchy`: a system
upgraded to Omarchy Quattro leaves that directory as a compatibility symlink
into the root-owned package tree, where the venv cannot be created.

Runtimes created by earlier versions, under `omarchy/agent-usage-extensions` or
`omarchy/copilot-usage`, are still used when present. To keep the interpreter
somewhere else entirely, set `OMARCHY_AGENT_USAGE_PYTHON` to its path; the panel
reads it from the session environment, so `~/.config/environment.d/` is the
place to set it for a graphical session.

## Authentication

### Where a key is read from

Every collector here resolves credentials the same way, in `lib/agent_credentials.py`:

1. The process environment, which overrides everything below.
2. `~/.config/omarchy/agents/<id>.json` — keys at the top level, or nested
   under `env`. Keep it `0600` in a `0700` directory.
3. Any per-agent legacy path that collector still supports.

Where an agent's own tool already keeps a credential on disk, the collector
reads that rather than asking for a copy: Copilot uses the authenticated GitHub
CLI, and the packaged Claude and Codex collectors read their own state
directories.

The bar panel runs under quickshell, which never sources a shell rc. A key
exported from `~/.zshrc` therefore reaches a terminal but not the panel, so an
agent that only has an exported key shows as unavailable there. Put it in the
agent config file, or in a store the agent's own tool writes.

### GitHub Copilot

Authenticate both CLIs:

```bash
gh auth login -h github.com
copilot login
```

The collector first uses `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN`
when present. Otherwise it asks the authenticated GitHub CLI for its OAuth
token and holds it only in process memory. No token is written to an Agent Panel
record.

Copilot CLI can report its local `isAuthenticated` flag as false while its
account quota RPC is available. A successful quota response is therefore the
source of truth.

### GLM Coding Plan

The collector reads credentials at runtime. Later sources never override an
earlier one, except the process environment, which wins over every file:

1. `~/.config/omarchy/agents/glm.json` — the preferred location. Keys sit at
   the top level, or nested under `env`. Keep it `0600`.
2. Claude Code's `settings.json` or `settings.local.json` `env` values.
3. `~/.config/claude-profiles/glm.env`, still read for existing installs.
4. Process environment variables, which override the files above.

The bar panel runs under quickshell, which never sources a shell rc, so a key
exported from `~/.zshrc` reaches a terminal but not the panel. Put it in one of
the files above.

Supported token names are `ZAI_CODING_PLAN_API_KEY`, `ZAI_API_KEY`,
`ZHIPU_API_KEY`, and `ZHIPUAI_API_KEY`, in that order, so a coding-plan key
outranks a generic platform key. `ANTHROPIC_AUTH_TOKEN` is not accepted: the
default endpoint is Z.ai, and reading Anthropic's own variable would send an
Anthropic key to a third party. `ANTHROPIC_BASE_URL` selects international Z.ai or
BigModel China. Credentials are never copied into this repository or generated
usage records.

GLM reports subscription quotas only. Claude Code transcript totals remain in
the Claude provider, avoiding duplicate local statistics.

### Grok (xAI)

The Grok collector reads an xAI team prepaid balance from the management API.
Create `~/.config/omarchy/agents/grok.json`, keep it `0600`, and provide:

```json
{
  "XAI_MANAGEMENT_KEY": "your-management-key",
  "XAI_TEAM_ID": "your-team-id"
}
```

Use a management key, not an `xai-` inference key. Management keys can carry
powerful account permissions, so grant only the billing access the collector
needs and do not place the key in this repository. The collector sends it only
to `management-api.x.ai` over HTTPS.

## Usage

### Exhaustion alarm

A provider is alarming when its fullest limit window reaches 90%, or when a
prepaid balance falls to its last 10% of funded credits.

The bar icon turns urgent when **any** enabled provider is alarming, not only
the one whose tab happens to be selected. The icon is only ever read while the
panel is shut, when the selection is invisible state, so scoping it to the
selection would hide an exhausted agent behind whichever tab was left open.
This differs from the packaged `omarchy.agents` widget, which colours the icon
from the selected provider alone.

The tab strip names the culprit: an alarming provider's tab is drawn in the
urgent colour. The active theme pins the selected tab's label to a fixed
colour, so this shows on the tabs you are not currently reading — which is the
case the strip needs to answer. The selected provider's own meters already turn
urgent in the panel body.

The panel refreshes automatically. Opening it requests fresh account values
from every enabled provider while reusing recent local transcript scans. Press
`R` while the panel is open to force a complete refresh. Copilot and GLM quota
values and Grok's prepaid balance come from their provider accounts, so the
meters include activity from other devices signed in to the same accounts.

To update from a terminal:

```bash
./bin/omarchy-agent-usage-update copilot
./bin/omarchy-agent-usage-update glm
./bin/omarchy-agent-usage-update grok
./bin/omarchy-agent-usage-update --force
```

Display records are written beneath the current user's XDG state directory.
Z.ai's monitor endpoint is used by its official `glm-plan-usage` plugin but is
not part of its public API reference, so its response format may change.

## Remove

If installed from Git, use Omarchy's standard removal command:

```bash
omarchy plugin remove meeksoft.agent-usage-extensions
```

For a local development link, disable the plugin and unlink that exact path:

```bash
omarchy plugin disable meeksoft.agent-usage-extensions
unlink "$HOME/.config/omarchy/plugins/meeksoft.agent-usage-extensions"
omarchy restart shell
```

The optional Copilot SDK runtime remains in
`$XDG_DATA_HOME/omarchy-agent-usage-extensions` so other checkouts can reuse it.
It can be removed separately if no longer needed.

## Privacy and security

- No credentials are stored in this repository.
- Generated usage records and local environment files are ignored by Git.
- Collectors return quota metadata only; they do not transmit source code.
- Copilot contacts GitHub, and GLM contacts the configured Z.ai/BigModel host.

## License

MIT
