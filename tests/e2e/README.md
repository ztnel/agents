# End-to-end workflow tests

`run_e2e.py` runs the developer workflow on a fresh fixture repo while a scripted human reviews it, then judges the pushed result with deterministic assertions.

```bash
uv run --project skills/_lib python tests/e2e/run_e2e.py --agent fake
uv run --project skills/_lib python tests/e2e/run_e2e.py --agent copilot --model gpt-6-luna --artifacts /tmp/e2e
```

Watch a run with the `tmux -L <socket> attach` command it prints. Requires tmux, tuicr 0.28+ and uv; the live tier also needs an authenticated `copilot`.

## Tiers

| Tier | Agent | Runs | Checks |
|---|---|---|---|
| Protocol | `fake_agent.py`, a scripted MCP client | every PR via `test_e2e_fake.py` | the feedback servers, launchers, ledger and commit helper work together |
| Live | the developer agent on a real model | nightly and on demand via `.github/workflows/e2e-live.yml` | a real model follows the workflow |

## Isolation

Each run gets a temp root with its own `XDG_STATE_HOME` and `XDG_DATA_HOME`, a bare origin, a private tmux server, and `BROWSER=true`. On macOS, tuicr ignores XDG, so the run removes its own sessions from the real tuicr store afterwards. `--keep` leaves the temp root and tmux server for inspection.

## Scenarios

`scenarios/<name>.json` declares:

- `fixture`: the base repo
- `prompt`: the human's request
- `presentation`: one brief comment
- `tuicr`: one step per review round, either a `comment` or `approve`
- `expect`: the oracle's inputs
- `fake.rounds`: the scripted agent's solution per round

The hidden `acceptance` test is copied into a fresh clone of the pushed origin and must pass. A run fails on any of the following:

- wrong commit count or stray paths
- missing `Co-authored-by` or `Approved-by` trailers
- unanswered comments or unclean review closure
- a stalled agent that needed a nudge
- exceeding `max_minutes`
