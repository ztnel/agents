#!/usr/bin/env bash
# Tests for review.py, the validated front door to `tuicr review`.
#
# It exists to remove two failure modes agents hit when invoking tuicr
# free-hand:
#   - the subcommand omitted (`tuicr review --repo X`), which tuicr reports as
#     `unexpected argument '--repo' found` without naming the real problem;
#   - a reply that does not reproduce its parent's anchor exactly, which
#     silently threads somewhere other than under the comment it answers.
#
# tuicr is mocked via a PATH shim that records the argv it was handed; the REAL
# review.py and skillkit.tuicrio run.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$(cd "${SCRIPT_DIR}/../lib" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

FAILED=0
fail() { echo "FAIL: $*"; FAILED=1; }

REPO="${TMP}/repo"
mkdir -p "${REPO}"
ARGV_LOG="${TMP}/argv.log"

# --- tuicr shim -------------------------------------------------------------
# A single multi-line comment (lines 10-14, old side) is the interesting case:
# a reply that drops --end-line or flips --side lands at a different anchor.
mkdir -p "${TMP}/bin"
cat > "${TMP}/bin/tuicr" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${ARGV_LOG}"
case "$1 $2" in
  "review list")
    echo '[{"slug":"sess-a","active":true,"comment_count":2}]' ;;
  "review comments")
    cat <<'JSON'
[
  {"id":"c1","comment_type":"issue","content":"multi-line concern","created_at":"2026-01-01T00:00:00Z",
   "path":"src/app.c","start_line":10,"end_line":14,"side":"old"},
  {"id":"c2","comment_type":"question","content":"answered already","created_at":"2026-01-01T00:00:01Z",
   "path":"src/app.c","start_line":3,"end_line":3,"side":"new"},
  {"id":"c3","comment_type":"reply","content":"prior reply","created_at":"2026-01-01T00:00:02Z",
   "path":"src/app.c","start_line":3,"end_line":3,"side":"new"}
]
JSON
    ;;
  "review add")
    echo '{"id":"new-1"}' ;;
  *)
    echo "unexpected argument '$2' found" >&2; exit 2 ;;
esac
SHIM
chmod +x "${TMP}/bin/tuicr"
export ARGV_LOG
export PATH="${TMP}/bin:${PATH}"

run_review() { python3 "${LIB}/review.py" --repo "${REPO}" "$@" 2>&1; }

# --- 1. the reported failure: subcommand omitted ----------------------------
out="$(python3 "${LIB}/review.py" --repo "${REPO}" 2>&1)"; rc=$?
if [ "${rc}" -eq 0 ]; then
  fail "omitting the subcommand should be an error"
fi
case "${out}" in
  *"{list,comments,reply,reviewed}"*) : ;;
  *) fail "omitted-subcommand error should name the valid subcommands, got: ${out}" ;;
esac
case "${out}" in
  *"unexpected argument"*) fail "should not surface tuicr's misleading unexpected-argument error" ;;
esac

# --- 2. list ----------------------------------------------------------------
out="$(run_review list)"
case "${out}" in
  *"sess-a"*active*) : ;;
  *) fail "list should report the active session, got: ${out}" ;;
esac

# --- 3. reply inherits the parent anchor EXACTLY ----------------------------
: > "${ARGV_LOG}"
out="$(run_review reply --to c1 --username model-x "agree - queued: fix")"
if [ $? -ne 0 ]; then fail "reply should succeed, got: ${out}"; fi

add_line="$(grep '^review add' "${ARGV_LOG}" || true)"
if [ -z "${add_line}" ]; then
  fail "reply did not invoke 'tuicr review add'"
else
  # All four anchor fields must match the parent comment c1.
  case "${add_line}" in
    *"--target-file src/app.c"*) : ;;
    *) fail "reply lost the parent's file: ${add_line}" ;;
  esac
  case "${add_line}" in
    *"--line 10"*) : ;;
    *) fail "reply lost the parent's start line: ${add_line}" ;;
  esac
  case "${add_line}" in
    *"--end-line 14"*) : ;;
    *) fail "reply dropped --end-line (would re-anchor to a single line): ${add_line}" ;;
  esac
  case "${add_line}" in
    *"--side old"*) : ;;
    *) fail "reply lost the parent's side: ${add_line}" ;;
  esac
  case "${add_line}" in
    *"--type reply"*) : ;;
    *) fail "reply must be typed 'reply' so it never wakes the watcher: ${add_line}" ;;
  esac
fi
case "${out}" in
  *"ANCHOR=src/app.c:10-14(old)"*) : ;;
  *) fail "reply should report the anchor it used, got: ${out}" ;;
esac

# --- 4. unknown comment id is rejected, and says what exists ----------------
out="$(run_review reply --to nope "body")"; rc=$?
if [ "${rc}" -eq 0 ]; then fail "replying to an unknown id should fail"; fi
case "${out}" in
  *c1*c2*) : ;;
  *) fail "unknown-id error should list available ids, got: ${out}" ;;
esac

# --- 5. the reply size budget is enforced ----------------------------------
big="$(python3 -c 'print("x" * 1501, end="")')"
out="$(run_review reply --to c1 "${big}")"; rc=$?
if [ "${rc}" -eq 0 ]; then fail "an over-budget reply should be rejected"; fi
case "${out}" in
  *1501*) : ;;
  *) fail "budget error should report the actual size, got: ${out}" ;;
esac
# One char under the limit is accepted.
ok="$(python3 -c 'print("x" * 1500, end="")')"
if ! run_review reply --to c1 "${ok}" >/dev/null 2>&1; then
  fail "a reply exactly at the budget should be accepted"
fi

# --- 6. --unanswered hides replied-to and reply-typed comments -------------
out="$(run_review comments --unanswered)"
case "${out}" in
  *c1*) : ;;
  *) fail "--unanswered should keep the unanswered comment c1, got: ${out}" ;;
esac
case "${out}" in
  *c2*) fail "--unanswered should hide c2, which already has a later reply: ${out}" ;;
esac
case "${out}" in
  *c3*) fail "--unanswered should hide reply-typed comments: ${out}" ;;
esac

# --- 7. the session is auto-resolved from the active session ---------------
: > "${ARGV_LOG}"
run_review comments >/dev/null 2>&1
if ! grep -q -- "--session sess-a" "${ARGV_LOG}"; then
  fail "comments should default to the active session, log: $(cat "${ARGV_LOG}")"
fi

# --- 8. flag position must not matter --------------------------------------
# The raw CLI's positional sensitivity is the bug being designed out, so
# --repo/--session are accepted on either side of the subcommand.
for form in \
  "--repo ${REPO} --session sess-a comments" \
  "comments --repo ${REPO} --session sess-a" \
  "--repo ${REPO} comments --session sess-a" \
  "--session sess-a --repo ${REPO} comments"
do
  : > "${ARGV_LOG}"
  # shellcheck disable=SC2086
  if ! out="$(python3 "${LIB}/review.py" ${form} 2>&1)"; then
    fail "flag order should not matter, but '${form}' failed: ${out}"
  fi
  case "${out}" in
    *c1*) : ;;
    *) fail "'${form}' did not return the session's comments: ${out}" ;;
  esac
  # Assert the values actually reached tuicr. Checking only the command's exit
  # status would pass even if a subparser default silently overwrote --repo,
  # because the shim answers regardless of which repo it is handed.
  if ! grep -q -- "--repo ${REPO} " "${ARGV_LOG}"; then
    fail "'${form}' did not pass --repo ${REPO} through, sent: $(cat "${ARGV_LOG}")"
  fi
  if ! grep -q -- "--session sess-a" "${ARGV_LOG}"; then
    fail "'${form}' did not pass --session through, sent: $(cat "${ARGV_LOG}")"
  fi
done

# An explicit --session after the subcommand must win over the active default.
: > "${ARGV_LOG}"
python3 "${LIB}/review.py" --repo "${REPO}" comments --session sess-explicit >/dev/null 2>&1
if ! grep -q -- "--session sess-explicit" "${ARGV_LOG}"; then
  fail "a subcommand-side --session should override the active-session default"
fi

if [ "${FAILED}" -ne 0 ]; then
  echo "FAIL: review.py front-door tests"
  exit 1
fi
echo "PASS: review.py front-door tests"
