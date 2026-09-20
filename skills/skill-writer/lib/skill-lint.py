#!/usr/bin/env python3
"""Validate Agent Skills against the agentskills.io specification.

A malformed skill fails silently: the runtime skips it, no error is written to any
log, and the authoring agent never learns the skill is missing. This linter is the
feedback loop that absence creates.

Findings are split into two severities that are never conflated:

  ERROR  Spec violations. The skill will not load, or breaks the published contract.
  WARN   Conventions and spec recommendations. The skill loads fine.

Spec: https://agentskills.io/specification
"""

import argparse
import json
import os
import re
import sys

SPEC_URL = "https://agentskills.io/specification"

DESC_MAX = 1024
DESC_HEADROOM = 950
NAME_MAX = 64
COMPAT_MAX = 500
BODY_MAX_LINES = 500
BODY_MAX_TOKENS = 5000

KNOWN_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDERS = ("SHORT_SUMMARY", "DESCRIBE_THE_TRIGGER", "ONE_LINE_PURPOSE",
                "CONCRETE_GOTCHA", "SKILL_NAME")
FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?$", re.S)
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "tests", "test"}
WALK_SKIP_DIRS = {"node_modules", "__pycache__", ".git"}

#: Python is the only accepted source language (see SKILL.md, "Python only").
SHELL_SUFFIXES = {".sh", ".bash", ".zsh", ".ksh"}
SHELL_SHEBANG_RE = re.compile(rb"^#![^\n]*\b(?:ba|da|k|z)?sh\b")
PY_SHEBANG = "#!/usr/bin/env python3"
SHARED_LIB = "_lib"
#: Reference depth for an entry point at `<skills>/<skill>/lib/<entry>.py`.
BOOTSTRAP_PARENTS = 2
BOOTSTRAP_RE = re.compile(r"parents\[(\d+)\]")
SHARED_LIB_REF_RE = re.compile(r"[\"']" + SHARED_LIB + r"[\"']")
#: Only an actual import implies the bootstrap is required; prose that merely
#: names the package (docs, lint messages) must not be flagged.
SKILLKIT_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+skillkit\b", re.M)
#: An importable module needs no shebang or exec bit; an entry point does.
ENTRYPOINT_RE = re.compile(r"^if\s+__name__\s*==\s*[\"']__main__[\"']", re.M)
#: A shell-out finding is only meaningful in a file that actually spawns processes.
SPAWNS_RE = re.compile(r"\bsubprocess\b|\bPopen\b|\bcheck_output\b|\bproc\.(?:run|detach)\b")
#: A line that compiles a pattern names a command without running it — this
#: linter's own table would otherwise flag itself.
PATTERN_DEF_RE = re.compile(r"\bre\.compile\(")

#: Shelling out to these reintroduces exactly the GNU-vs-BSD breakage the port
#: exists to remove; `_lib/skillkit` provides a portable equivalent for each.
NON_PORTABLE = [
    (re.compile(r"\b(?:sha1sum|shasum|md5sum)\b"), "paths.short_hash() from _lib/skillkit"),
    (re.compile(r"\bstat\s+-[cf]\b"), "os.stat / paths.file_size()"),
    (re.compile(r"\bdate\s+-[dv]\b"), "datetime"),
    (re.compile(r"\bsetsid\b"), "proc.detach() from _lib/skillkit"),
    (re.compile(r"\bmktemp\b"), "tempfile"),
    (re.compile(r"\bshell\s*=\s*True\b"), "an argv list, never a shell string"),
]
FILE_LIST_CAP = 6


def _summarise(paths):
    """Render a capped, deterministic file list for a single finding."""
    shown = sorted(paths)
    if len(shown) <= FILE_LIST_CAP:
        return ", ".join(shown)
    return ", ".join(shown[:FILE_LIST_CAP]) + f", +{len(shown) - FILE_LIST_CAP} more"


def walk_files(skill_dir):
    """Yield (relative_path, absolute_path) for every real file in the skill."""
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            path = os.path.join(root, fname)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            yield os.path.relpath(path, skill_dir), path


def is_shell_script(path, rel):
    if os.path.splitext(rel)[1].lower() in SHELL_SUFFIXES:
        return True
    try:
        with open(path, "rb") as handle:
            return bool(SHELL_SHEBANG_RE.match(handle.readline()))
    except OSError:
        return False


def check_language(skill_dir, rep):
    """Enforce Python as the sole source language and the `_lib` shared package.

    None of these findings block loading, so all are warnings: `--list-names`
    reports loadability, and demoting a still-loading skill out of that list
    would break the drift check in SKILL.md. `--strict` is the enforcing gate.
    """
    shell, no_shebang, not_exec, bad_bootstrap = [], [], [], []
    unportable = {}

    for rel, path in walk_files(skill_dir):
        if is_shell_script(path, rel):
            shell.append(rel)
            continue
        if os.path.splitext(rel)[1] != ".py":
            continue

        try:
            source = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue

        if ENTRYPOINT_RE.search(source):
            if not source.startswith(PY_SHEBANG):
                no_shebang.append(rel)
            elif not os.access(path, os.X_OK):
                not_exec.append(rel)

        # The bootstrap is required by an actual `import skillkit`; a file that only
        # names `_lib` (docs, constants) needs one solely if it already has one to
        # get wrong.
        found = {int(m) for m in BOOTSTRAP_RE.findall(source)}
        imports_kit = SKILLKIT_IMPORT_RE.search(source)
        if imports_kit or (SHARED_LIB_REF_RE.search(source) and found):
            # `_lib` sits in the skills root, so the correct depth is however many
            # directories separate this file from the skill directory, plus one:
            # `<skill>/lib/x.py` needs parents[2], `<skill>/x.py` needs parents[1].
            want = rel.count(os.sep) + 1
            if not found:
                bad_bootstrap.append(f"{rel} (imports skillkit, no {SHARED_LIB} bootstrap)")
            elif want not in found:
                depths = ", ".join(f"parents[{d}]" for d in sorted(found))
                bad_bootstrap.append(f"{rel} (has {depths}, needs parents[{want}])")

        code = [line for line in source.splitlines()
                if not line.lstrip().startswith("#") and not PATTERN_DEF_RE.search(line)]
        if not SPAWNS_RE.search("\n".join(code)):
            continue
        for line in code:
            for pattern, replacement in NON_PORTABLE:
                if pattern.search(line):
                    unportable.setdefault(replacement, set()).add(rel)

    if shell:
        rep.warn(f"shell script(s) present: {_summarise(shell)}; Python is the only "
                 f"accepted source language — port real logic to .py and reuse "
                 f"{SHARED_LIB}/skillkit; delete any 'exec python3' shim and point "
                 f"callers at the .py directly")
    if no_shebang:
        rep.warn(f"entry point(s) without a '{PY_SHEBANG}' shebang: "
                 f"{_summarise(no_shebang)}; they cannot be invoked directly")
    if not_exec:
        rep.warn(f"entry point(s) not executable: {_summarise(not_exec)}; "
                 f"chmod +x so the documented invocation works")
    if bad_bootstrap:
        rep.warn(f"broken {SHARED_LIB} import bootstrap: {_summarise(bad_bootstrap)}; "
                 f"a script needs sys.path.insert(0, str(Path(__file__).resolve()"
                 f".parents[N] / \"{SHARED_LIB}\")) where N reaches the skills root "
                 f"({BOOTSTRAP_PARENTS} from lib/, 1 from the skill root)")
    for replacement, files in sorted(unportable.items()):
        rep.warn(f"non-portable subprocess usage in {_summarise(files)}: "
                 f"use {replacement}")



class FrontmatterError(ValueError):
    """Raised when `SKILL.md` frontmatter is outside the supported YAML subset."""


def _indent(line):
    """Count leading spaces before the first non-space character."""
    return len(line) - len(line.lstrip(" "))


def _parse_scalar(raw):
    """Parse a scalar value using the YAML subset Agent Skills frontmatter needs."""
    value = raw.strip()
    if value == "":
        return None
    if value[0] in "\"'":
        quote = value[0]
        if len(value) < 2 or value[-1] != quote:
            raise FrontmatterError("unterminated quoted scalar")
        inner = value[1:-1]
        if quote == "'":
            return inner.replace("''", "'")
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise FrontmatterError(f"invalid double-quoted scalar: {exc.msg}") from exc
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise FrontmatterError(f"invalid flow collection: {exc.msg}") from exc
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", value):
        return float(value)
    return value


def _strip_block_indent(lines):
    """Remove the common positive indent from a block-scalar body."""
    nonblank = [line for line in lines if line.strip()]
    if not nonblank:
        return ""
    min_indent = min(_indent(line) for line in nonblank)
    if min_indent == 0:
        raise FrontmatterError("block scalar content must be indented")
    return "\n".join(line[min_indent:] if len(line) >= min_indent else "" for line in lines)


def _parse_nested(lines, start, parent_indent):
    """Parse a one-level nested mapping or sequence starting at `start`."""
    first = start
    while first < len(lines) and not lines[first].strip():
        first += 1
    if first >= len(lines) or _indent(lines[first]) <= parent_indent:
        return None, start

    nested_indent = _indent(lines[first])
    is_sequence = lines[first].lstrip().startswith("-")
    value = [] if is_sequence else {}
    seen = set()
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        indent = _indent(line)
        if indent <= parent_indent:
            break
        if indent != nested_indent:
            raise FrontmatterError(f"unsupported nested indentation on line {i + 1}")
        text = line[indent:]
        if is_sequence:
            if not text.startswith("-"):
                raise FrontmatterError(f"cannot mix sequence and mapping on line {i + 1}")
            value.append(_parse_scalar(text[1:].strip()))
            i += 1
            continue
        if text.startswith("-"):
            raise FrontmatterError(f"cannot mix mapping and sequence on line {i + 1}")
        if ":" not in text:
            raise FrontmatterError(f"missing ':' in nested mapping on line {i + 1}")
        key, raw_value = text.split(":", 1)
        key = key.strip()
        if not key:
            raise FrontmatterError(f"empty nested key on line {i + 1}")
        if key in seen:
            raise FrontmatterError(f"duplicate key {key!r} in frontmatter")
        seen.add(key)
        value[key] = _parse_scalar(raw_value.strip())
        i += 1
    return value, i


def parse_frontmatter(frontmatter_text):
    """Parse the limited YAML frontmatter shape accepted by Agent Skills here.

    The standard library has no YAML parser. This intentionally small parser
    covers the fields this linter validates: top-level scalar keys, simple
    nested maps such as `metadata`, simple sequences, and literal/folded block
    scalars. It rejects duplicate keys instead of silently keeping the last one.
    """
    lines = frontmatter_text.splitlines()
    data = {}
    seen = set()
    i = 0
    found = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        found = True
        if _indent(line) != 0:
            raise FrontmatterError(f"unexpected indentation on line {i + 1}")
        if ":" not in line:
            raise FrontmatterError(f"missing ':' on line {i + 1}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not key:
            raise FrontmatterError(f"empty key on line {i + 1}")
        if key in seen:
            raise FrontmatterError(f"duplicate key {key!r} in frontmatter")
        seen.add(key)

        value_text = raw_value.strip()
        if value_text in {"|", ">"}:
            block_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if next_line.strip() and _indent(next_line) == 0:
                    break
                block_lines.append(next_line)
                i += 1
            block = _strip_block_indent(block_lines)
            data[key] = block if value_text == "|" else re.sub(r"\n+", " ", block).strip()
            continue
        if value_text == "":
            nested, next_i = _parse_nested(lines, i + 1, 0)
            data[key] = nested
            i = next_i if next_i != i + 1 else i + 1
            continue
        data[key] = _parse_scalar(value_text)
        i += 1
    return data if found else None


class Report:
    def __init__(self, skill_dir):
        self.dir = skill_dir
        self.name = os.path.basename(os.path.abspath(skill_dir))
        self.declared_name = None
        self.desc_len = None
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    @property
    def loadable(self):
        return not self.errors

    def as_dict(self):
        return {
            "skill": self.name,
            "path": self.dir,
            "declared_name": self.declared_name,
            "description_chars": self.desc_len,
            "loadable": self.loadable,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def raw_description_value(frontmatter_text):
    """Return the literal text following `description:` on its own line, else None."""
    for line in frontmatter_text.splitlines():
        if line.startswith("description:"):
            return line[len("description:"):].strip()
    return None


def check_body(body, rep):
    lines = len(body.splitlines())
    tokens = len(body) // 4
    if lines > BODY_MAX_LINES:
        rep.warn(f"body is {lines} lines (guidance: <={BODY_MAX_LINES}); "
                 f"move detail into reference files")
    if tokens > BODY_MAX_TOKENS:
        rep.warn(f"body is ~{tokens} tokens (guidance: <={BODY_MAX_TOKENS}); "
                 f"the whole body loads on every activation")


def check_placeholders(text, rep):
    found = sorted({p for p in PLACEHOLDERS if p in text})
    if found:
        rep.warn(f"unedited template placeholder(s): {', '.join(found)}; "
                 f"the skeleton was scaffolded but never filled in")


def check_layout(skill_dir, rep):
    if not os.path.isfile(os.path.join(skill_dir, "README.md")):
        rep.warn("no README.md (Convention: every source folder documents its API)")

    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        rel_root = os.path.relpath(root, skill_dir)
        top = rel_root.split(os.sep)[0]
        if top == "lib":
            continue
        for fname in files:
            path = os.path.join(root, fname)
            if os.path.islink(path) or not os.access(path, os.X_OK):
                continue
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, skill_dir)
            rep.warn(f"executable '{rel}' lives outside lib/ (convention)")


def check_frontmatter(data, frontmatter_text, rep):
    name = data.get("name")
    rep.declared_name = name if isinstance(name, str) else None

    if name is None:
        rep.error("frontmatter has no 'name' field")
    elif not isinstance(name, str):
        rep.error(f"'name' must be a string, got {type(name).__name__}")
    elif not name:
        rep.error("'name' is empty")
    else:
        if len(name) > NAME_MAX:
            rep.error(f"'name' is {len(name)} characters (max {NAME_MAX})")
        if not NAME_RE.match(name):
            rep.error(f"'name' {name!r} must be lowercase a-z, 0-9 and single hyphens, "
                      f"with no leading, trailing or consecutive hyphens")
        if name != rep.name:
            rep.error(f"'name' {name!r} does not match directory name {rep.name!r}")

    desc = data.get("description")
    if desc is None:
        rep.error("frontmatter has no 'description' field")
    elif not isinstance(desc, str):
        rep.error(f"'description' must be a string, got {type(desc).__name__}")
    elif not desc.strip():
        rep.error("'description' is empty")
    else:
        rep.desc_len = len(desc)
        if len(desc) > DESC_MAX:
            rep.error(f"'description' is {len(desc)} characters (max {DESC_MAX}) — "
                      f"THE SKILL WILL NOT LOAD, and no error is logged anywhere")
        elif len(desc) > DESC_HEADROOM:
            rep.warn(f"'description' is {len(desc)} characters, within "
                     f"{DESC_MAX - len(desc)} of the {DESC_MAX} hard limit; "
                     f"a small edit could silently break loading")
        if "use when" not in desc.lower():
            rep.warn("'description' has no 'Use when ...' clause describing the trigger "
                     "condition; agents match on when to act, not just what the skill does")

        raw = raw_description_value(frontmatter_text)
        if raw and raw[0] not in "\"'|>" and ":" in raw:
            rep.warn("'description' is unquoted and contains a colon; it parses only "
                     "while no colon is followed by a space — quote the value")

    compat = data.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            rep.error(f"'compatibility' must be a string, got {type(compat).__name__}")
        elif len(compat) > COMPAT_MAX:
            rep.error(f"'compatibility' is {len(compat)} characters (max {COMPAT_MAX})")

    meta = data.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            rep.error(f"'metadata' must be a map, got {type(meta).__name__}")
        else:
            for k, v in meta.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    rep.error(f"'metadata' entry {k!r} must map a string to a string; "
                              f"quote values such as version numbers")
                    break

    tools = data.get("allowed-tools")
    if tools is not None and not isinstance(tools, str):
        rep.error(f"'allowed-tools' must be a space-separated string, "
                  f"got {type(tools).__name__}")

    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        rep.warn(f"unrecognised frontmatter key(s): {', '.join(unknown)}; "
                 f"put custom fields under 'metadata'")


def lint_skill(skill_dir):
    rep = Report(skill_dir)
    path = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(path):
        rep.error("no SKILL.md (a skill directory must contain one)")
        return rep

    raw = open(path, "rb").read()

    if raw.startswith(b"\xef\xbb\xbf"):
        rep.error("file starts with a UTF-8 BOM, which hides the opening '---' and "
                  "defeats frontmatter detection; save as UTF-8 without BOM")
        raw = raw[3:]

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.error(f"SKILL.md is not valid UTF-8: {exc}")
        return rep

    if b"\r\n" in raw:
        rep.warn("file uses CRLF line endings; prefer LF")

    match = FRONTMATTER_RE.match(text)
    if not match:
        rep.error("no valid YAML frontmatter; SKILL.md must open with '---' on line 1 "
                  "and close with '---' on its own line")
        return rep

    frontmatter_text, body = match.group(1), match.group(2) or ""

    if "\t" in frontmatter_text:
        rep.warn("frontmatter contains a tab character; YAML forbids tabs for indentation")

    try:
        data = parse_frontmatter(frontmatter_text)
    except FrontmatterError as exc:
        detail = str(exc).replace("\n", " ")
        rep.error(f"frontmatter is not valid supported YAML: {detail}")
        return rep

    if data is None:
        rep.error("frontmatter is empty")
        return rep
    if not isinstance(data, dict):
        rep.error(f"frontmatter must be a YAML mapping, got {type(data).__name__}")
        return rep

    check_frontmatter(data, frontmatter_text, rep)
    check_body(body, rep)
    check_placeholders(text, rep)
    check_layout(skill_dir, rep)
    check_language(skill_dir, rep)
    return rep


def discover(root):
    if os.path.isfile(os.path.join(root, "SKILL.md")):
        return [root]
    return sorted(
        os.path.join(root, d)
        for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d))
        and not d.startswith(".")
        and d != SHARED_LIB
    )


def default_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(
        description="Validate Agent Skills against the agentskills.io specification.",
        epilog=f"Spec: {SPEC_URL}",
    )
    parser.add_argument("targets", nargs="*",
                        help="skill directories, or a directory of skills "
                             "(default: the skills root containing this script)")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON")
    parser.add_argument("--list-names", action="store_true",
                        help="print only the names of skills expected to load, one per "
                             "line, for diffing against the skills an agent can actually "
                             "see")
    args = parser.parse_args()

    targets = args.targets or [default_root()]
    skill_dirs = []
    for target in targets:
        if not os.path.isdir(target):
            print(f"error: not a directory: {target}", file=sys.stderr)
            return 2
        skill_dirs.extend(discover(target))

    reports = [lint_skill(d) for d in skill_dirs if os.path.isfile(os.path.join(d, "SKILL.md"))]

    if not reports:
        print("error: no SKILL.md found under: " + ", ".join(targets), file=sys.stderr)
        return 2

    if args.list_names:
        for rep in reports:
            if rep.loadable:
                print(rep.declared_name or rep.name)
        return 0

    n_err = sum(len(r.errors) for r in reports)
    n_warn = sum(len(r.warnings) for r in reports)
    broken = [r for r in reports if r.errors]

    if args.json:
        print(json.dumps({
            "skills": [r.as_dict() for r in reports],
            "totals": {
                "skills": len(reports),
                "errors": n_err,
                "warnings": n_warn,
                "not_loadable": [r.name for r in broken],
            },
        }, indent=2))
    else:
        width = max(len(r.name) for r in reports)
        for rep in reports:
            if rep.errors:
                status = "FAIL"
            elif rep.warnings:
                status = "warn"
            else:
                status = "ok"
            chars = f"{rep.desc_len:>4}" if rep.desc_len is not None else "   ?"
            print(f"{status:>4}  {rep.name:<{width}}  desc={chars}")
            for msg in rep.errors:
                print(f"        ERROR  {msg}")
            for msg in rep.warnings:
                print(f"        warn   {msg}")

        print(f"\n{len(reports)} skill(s): {n_err} error(s), {n_warn} warning(s)")
        if broken:
            names = ", ".join(r.name for r in broken)
            print(f"WILL NOT LOAD: {names}")

    if n_err:
        return 1
    if args.strict and n_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
