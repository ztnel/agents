#!/usr/bin/env bash
set -euo pipefail

skillDir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sandbox="$(mktemp -d)"
trap 'rm -rf "${sandbox}"' EXIT

remote="${sandbox}/remote.git"
seed="${sandbox}/seed"
work="${sandbox}/work"
workNoUpstream="${sandbox}/work-no-upstream"

git init --bare -q "${remote}"
git init -q -b develop "${seed}"
git -C "${seed}" config user.name Test
git -C "${seed}" config user.email test@example.com
printf 'one\n' > "${seed}/file.txt"
git -C "${seed}" add file.txt
git -C "${seed}" commit -qm initial
git -C "${seed}" remote add origin "${remote}"
git -C "${seed}" push -q -u origin develop
git clone -q -b develop "${remote}" "${work}"

printf 'two\n' >> "${seed}/file.txt"
git -C "${seed}" commit -qam second
git -C "${seed}" push -q

eval "$("${skillDir}/lib/refresh_review_refs.py" \
    --repo "${work}" --base develop --head HEAD --remote origin)"

[ "${REVIEW_BASE_REF}" = "origin/develop" ]
[ "${REVIEW_HEAD_REF}" = "HEAD" ]
[ "${REVIEW_REVSET}" = "origin/develop...HEAD" ]
[ "$(git -C "${work}" rev-parse HEAD)" = "$(git -C "${work}" rev-parse origin/develop)" ]

# A local branch without configured upstream metadata still refreshes from the
# selected remote's branch of the same name.
git clone -q -b develop "${remote}" "${workNoUpstream}"
git -C "${workNoUpstream}" branch --unset-upstream
printf 'three\n' >> "${seed}/file.txt"
git -C "${seed}" commit -qam third
git -C "${seed}" push -q

beforeNoUpstream="$(git -C "${workNoUpstream}" rev-parse HEAD)"
eval "$("${skillDir}/lib/refresh_review_refs.py" \
    --repo "${workNoUpstream}" --base develop --head HEAD --remote origin)"
afterNoUpstream="$(git -C "${workNoUpstream}" rev-parse HEAD)"
[ "${beforeNoUpstream}" != "${afterNoUpstream}" ]
[ "${afterNoUpstream}" = "$(git -C "${seed}" rev-parse HEAD)" ]

printf 'dirty\n' >> "${work}/file.txt"
printf 'four\n' >> "${seed}/file.txt"
git -C "${seed}" commit -qam fourth
git -C "${seed}" push -q

if "${skillDir}/lib/refresh_review_refs.py" \
    --repo "${work}" --base develop --head HEAD --remote origin >/dev/null 2>&1; then
    echo "FAIL: dirty behind HEAD should be rejected" >&2
    exit 1
fi

# The base must default to the remote's own default branch, never a hardcoded
# name, so the helper works in any repository.
git --git-dir="${remote}" symbolic-ref HEAD refs/heads/develop
autoBase="${sandbox}/work-auto"
git clone -q "${remote}" "${autoBase}"
eval "$("${skillDir}/lib/refresh_review_refs.py" --repo "${autoBase}" --remote origin)"
[ "${REVIEW_BASE_REF}" = "origin/develop" ] || {
    echo "FAIL: base did not default to the remote default branch (got ${REVIEW_BASE_REF})" >&2
    exit 1
}

# A clone missing refs/remotes/origin/HEAD must recover it rather than fail.
git -C "${autoBase}" symbolic-ref --delete refs/remotes/origin/HEAD
eval "$("${skillDir}/lib/refresh_review_refs.py" --repo "${autoBase}" --remote origin)"
[ "${REVIEW_BASE_REF}" = "origin/develop" ] || {
    echo "FAIL: default branch was not recovered when origin/HEAD was absent" >&2
    exit 1
}

# --no-ff must review the local HEAD as-is instead of moving the human's branch.
noff="${sandbox}/work-noff"
git clone -q -b develop "${remote}" "${noff}"
printf 'five\n' >> "${seed}/file.txt"
git -C "${seed}" commit -qam fifth
git -C "${seed}" push -q
beforeNoFf="$(git -C "${noff}" rev-parse HEAD)"
eval "$("${skillDir}/lib/refresh_review_refs.py" \
    --repo "${noff}" --base develop --head HEAD --remote origin --no-ff)"
[ "$(git -C "${noff}" rev-parse HEAD)" = "${beforeNoFf}" ] || {
    echo "FAIL: --no-ff moved HEAD" >&2
    exit 1
}

echo "PASS: refresh-review-refs"
