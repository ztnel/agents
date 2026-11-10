# Agents

[![CI](https://img.shields.io/github/actions/workflow/status/ztnel/agents/ci.yml?branch=main)](https://github.com/ztnel/agents/actions/workflows/ci.yml?query=branch%3Amain)
[![Latest release](https://img.shields.io/github/v/release/ztnel/agents?display_name=tag)](https://github.com/ztnel/agents/releases/latest)

GitHub Copilot CLI plugin for personal agent infrastructure.

## Standard

This repository is an [Agent Plugins 1.0.0](https://agent-plugins.org/specification) package. The canonical schema identifier in `plugin.json` pins the package format to exactly `1.0.0`.

- `plugin.json` is the Copilot plugin manifest.
- `mcp.json` configures the GitHub MCP endpoint and the skill-owned review-feedback servers.
- `skills/` contains Agent Skills discovered as immediate child directories with `SKILL.md`.
- `com.github.copilot/` contains Copilot CLI custom agents exposed through the manifest extension namespace.

This repository supports GitHub Copilot CLI only. Compatibility with other
clients is not tested or maintained.

## Install

The commands below assume the repository has been published as `ztnel/agents`. Replace `<version>` with a release tag such as `v0.2.0`.

Copilot CLI can install the repository as a plugin:

```console
copilot plugin install ztnel/agents
```

The installer does not expose a tag option. To load an exact release without installing it globally, check out the tag and pass the plugin root:

```console
git clone --branch <version> --depth 1 https://github.com/ztnel/agents.git
copilot --plugin-dir ./agents
```

Copilot loads the skills, the custom agents under `com.github.copilot/`, and the
root MCP configuration.
The review-feedback servers require `uv`; see the [shared feedback contract](skills/_lib/README.md).

## Documentation

- [Infrastructure megadrawing](docs/INFRASTRUCTURE.md)
- [Architecture and design criteria](docs/ARCH.md)
- [CI and validation](docs/CI.md)
