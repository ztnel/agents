# agents

Copilot CLI custom agent definitions.

## Public API

Each `*.agent.md` file defines a main-session Copilot CLI agent:

| Agent | Responsibility |
| --- | --- |
| `agents:developer` | Brief feature alignment through `presentation`, implementation, and review-gated delivery through `tuicr` (in a `tmux` pane when available) and `git-commit`; issue reporting through `github-issue` |
| `agents:demystifier` | Sourced concept explanations and interactive discussion through `presentation` |

## Design criteria

Keep these files separate from Agent Skills. They rely on Copilot CLI custom
agent semantics, not the Agent Plugins 1.0.0 core component model.
Agents own content and workflow policy; `presentation` owns document display
and comments. Presentation closure is not implementation or code approval.
