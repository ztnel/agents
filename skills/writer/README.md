# `writer`

Distills human-facing documentation while preserving meaning and decisions.

## Public API

### `lib/compare.py`

```bash
lib/compare.py SOURCE RESULT [--require TEXT]... [--json]
```

Reads two UTF-8 files and verifies that `RESULT` contains fewer characters than
`SOURCE`. Each `--require` value must occur unchanged in the result.

| Exit | Meaning |
|---|---|
| `0` | Result is shorter and contains every required value. |
| `1` | Result is not shorter. |
| `2` | A required value is missing or an argument/file is invalid. |

The human-readable output reports source characters, result characters, characters
removed, and reduction percentage. `--json` emits those fields plus missing required
values.

## Design

The skill owns semantic rewriting; the script measures only deterministic properties.
Character reduction is evidence of distillation, not proof that meaning survived.

## Dependencies

Python 3.8+ standard library only.
