# com.github.copilot

GitHub Copilot CLI extension namespace.

## Public API

The `agents/` directory contains Copilot CLI custom agent definitions exposed
through `plugin.json.extensions["com.github.copilot"]`.

## Design criteria

Keep Copilot custom agents in this namespace because Agent Plugins 1.0.0 does
not define a core custom-agent component.
