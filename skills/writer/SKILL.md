---
name: writer
description: "Distill human-facing documentation to the fewest clear words without changing its meaning, requirements, decisions, warnings, or tone. AUTO-INVOKE whenever drafting or revising documentation for human review, even when concision is not requested explicitly. Remove repetition, tangents, and unnecessary history or implementation detail; keep one coherent theme. Ask before omitting uncertain details. Use when writing or editing documentation a human will review."
---

# `writer` — concise documentation without semantic loss

Make human-facing documentation faster to understand. Optimize for meaning per
character, not brevity alone.

## Contract

Preserve:

- Claims, requirements, decisions, warnings, qualifications, and intended tone.
- Information needed to understand, use, review, or safely change the subject.
- Exact commands, identifiers, links, and examples when their precision matters.

Remove or merge:

- Repetition, throat-clearing, filler, and restated conclusions.
- Historical context that does not explain current behavior or a current decision.
- Technical detail that does not help the intended reader act or review.
- Side topics that break the document's central theme.

Never silently resolve uncertain scope. If a detail might be material, ask whether
to keep it before omitting it.

## Workflow

1. Identify the audience, purpose, and single organizing theme.
2. Extract the facts and obligations that must survive.
3. Rewrite around the theme; prefer direct verbs, concrete nouns, and one statement
   per idea.
4. Remove details only when clearly redundant or irrelevant. Ask about ambiguous
   details.
5. Compare source and result. The result must be shorter unless the human requested
   necessary clarification.
6. Re-read for semantic equivalence: no changed claim, softened requirement, lost
   warning, or invented certainty.

Use `lib/compare.py` when source and result are available as files:

```bash
~/.agents/skills/writer/lib/compare.py SOURCE RESULT \
  [--require TEXT]... [--json]
```

The helper verifies character reduction and any exact text the caller explicitly
requires. It cannot prove semantic equivalence; the final reread and uncertainty
gate remain mandatory.

## Output

Return the distilled text, not an essay about the rewrite. Mention a meaningful
omission only when the human needs that decision surfaced.

## Boundaries

- Do not shorten code, machine-readable data, quotations, legal text, or generated
  artifacts unless explicitly asked.
- Do not trade accuracy for a target ratio.
- Do not add background merely to make the document self-contained when its audience
  already has that context.
