# Agents

GitHub Copilot CLI plugin for personal agent infrastructure.

## Standard

This repository is an [Agent Plugins 1.0.0](https://agent-plugins.org/specification) package. The canonical schema identifier in `plugin.json` pins the package format to exactly `1.0.0`.

- `plugin.json` is the Copilot plugin manifest.
- `skills/` contains Agent Skills discovered as immediate child directories with `SKILL.md`.
- `com.github.copilot/` contains Copilot CLI custom agents exposed through the manifest extension namespace.

This repository supports GitHub Copilot CLI only. Compatibility with other
clients is not tested or maintained.

## Install

The commands below assume the repository has been published as `ztnel/agents`. Replace `<version>` with a release tag such as `v0.1.0`.

Copilot CLI can install the repository as a plugin:

```console
copilot plugin install ztnel/agents
```

The installer does not expose a tag option. To load an exact release without installing it globally, check out the tag and pass the plugin root:

```console
git clone --branch <version> --depth 1 https://github.com/ztnel/agents.git
copilot --plugin-dir ./agents
```

Copilot loads the skills and the custom agent under `com.github.copilot/`.

## Design criteria

Use Agent Plugins 1.0.0 for the package shape and Copilot's reverse-domain
extension namespace for Copilot CLI custom agents. Optimize all documented
installation and runtime behavior for Copilot CLI rather than preserving
cross-client compatibility.
