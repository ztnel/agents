# CI and validation

The [`ci` workflow](../.github/workflows/ci.yml) runs on pushes to `main` and
pull requests. Jobs use Ubuntu; newer runs cancel older runs for the same ref.
Workflow permissions are limited to `contents: read`.

## Regression

The `regression` job uses Python 3.14 and runs [`tests/run_ci.py`](../tests/run_ci.py):

- Compile Python sources and validate the workflow, plugin, and MCP manifests.
- Run every Python `test_*.py` and the tuicr shell suites.
- Lint all skills, with strict linting for `github-issue`.
- Check whitespace errors with `git diff --check`.

Run the same harness locally from the repository root:

```console
python3 tests/run_ci.py
```

## Package smoke

After regression succeeds, `smoke` installs the pinned Copilot CLI baseline on
Node 22, installs the checkout as a plugin, and runs
[`tests/copilot_smoke.py`](../tests/copilot_smoke.py). An isolated `COPILOT_HOME`
keeps discovery independent of existing user configuration.

The harness checks the declared plugin, skill, and MCP inventory and exercises
each custom agent in a live noninteractive session. Set the repository secret
`COPILOT_GITHUB_TOKEN` to a fine-grained PAT with Copilot Requests permission.
Trusted pushes and pull requests require live checks via `--expect-live`;
forked pull requests use `--skip-live` and still check package discovery.

The CLI version is pinned in the workflow. Change it deliberately and rerun
discovery and live-agent checks before accepting a new baseline.
