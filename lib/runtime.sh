# Shared resolution of the Copilot collector's Python runtime. Sourced by both
# setup-copilot-runtime and omarchy-agent-usage-update, which must agree on the
# path or the collector silently reports a missing runtime.

# The runtime used to live under "$XDG_DATA_HOME/omarchy", but a system upgraded
# to Omarchy Quattro leaves that directory as a compatibility symlink into the
# root-owned package tree, where the user cannot create a venv. Keep the runtime
# beside it instead, and still accept the historical locations so installs made
# before this change keep working.

agent_usage_data_home() {
  printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}"
}

agent_usage_runtime_root() {
  printf '%s/omarchy-agent-usage-extensions' "$(agent_usage_data_home)"
}

# Historical runtime roots, newest first.
agent_usage_legacy_pythons() {
  local data_home
  data_home="$(agent_usage_data_home)"
  printf '%s\n' \
    "$data_home/omarchy/agent-usage-extensions/.venv/bin/python" \
    "$data_home/omarchy/copilot-usage/.venv/bin/python"
}

# An explicit override always wins, so a runtime kept anywhere else stays usable.
agent_usage_python() {
  if [[ -n ${OMARCHY_AGENT_USAGE_PYTHON:-} ]]; then
    printf '%s' "$OMARCHY_AGENT_USAGE_PYTHON"
    return
  fi

  local python legacy
  python="$(agent_usage_runtime_root)/.venv/bin/python"
  if [[ ! -x $python ]]; then
    while IFS= read -r legacy; do
      if [[ -x $legacy ]]; then
        python="$legacy"
        break
      fi
    done < <(agent_usage_legacy_pythons)
  fi

  printf '%s' "$python"
}
