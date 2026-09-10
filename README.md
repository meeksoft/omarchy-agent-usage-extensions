# Omarchy Agent Usage Extensions

An Omarchy bar plugin that extends the built-in Agents panel with subscription
quota meters for GitHub Copilot and Z.ai GLM Coding Plan.

The plugin retains Omarchy's built-in Claude, Codex, and Fireworks records. Its
provider selector uses a two-column layout with balanced multiline names so
additional providers remain readable.

## Requirements

- Omarchy Quattro with the `omarchy.agents` plugin
- `jq`, Python 3, and `uv`
- GitHub Copilot CLI for Copilot quota
- A Z.ai or BigModel Coding Plan used through Claude Code for GLM quota

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

The collector reads credentials at runtime, in this order:

1. Process environment variables.
2. Claude Code's `settings.json` or `settings.local.json` `env` values.
3. `~/.config/claude-profiles/glm.env`.

Supported token names are `ZAI_CODING_PLAN_API_KEY`, `ZAI_API_KEY`,
`ZHIPU_API_KEY`, and `ZHIPUAI_API_KEY`, in that order, so a coding-plan key
outranks a generic platform key. `ANTHROPIC_AUTH_TOKEN` is not accepted: the
default endpoint is Z.ai, and reading Anthropic's own variable would send an
Anthropic key to a third party. `ANTHROPIC_BASE_URL` selects international Z.ai or
BigModel China. Credentials are never copied into this repository or generated
usage records.

GLM reports subscription quotas only. Claude Code transcript totals remain in
the Claude provider, avoiding duplicate local statistics.

## Usage

The panel refreshes automatically. To update manually:

```bash
./bin/omarchy-agent-usage-update copilot
./bin/omarchy-agent-usage-update glm
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
