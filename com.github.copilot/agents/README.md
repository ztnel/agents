# agents

Copilot CLI custom agent definitions.

## Public API

Each `*.agent.md` file defines one Copilot CLI agent. The `developer.agent.md` definition is the front-door agent for reviewed feature work.

## Design criteria

Keep these files separate from Agent Skills. They rely on Copilot CLI custom
agent semantics, not the Agent Plugins 1.0.0 core component model.
