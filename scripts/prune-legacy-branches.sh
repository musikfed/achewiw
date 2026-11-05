#!/usr/bin/env bash
# One-time, allowlisted remote branch cleanup AFTER the merged release reaches
# GitHub main. No rebase, no reset, no force-push, no local branch deletion.
set -Eeuo pipefail
ACTION="${1:-preview}"
CONFIRM="${2:-}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
die() { printf 'ACHEWIW branch-cleanup: %s\n' "$*" >&2; exit 1; }
[[ "${ACTION}" == preview || "${ACTION}" == apply ]] || die "Usage: bash scripts/prune-legacy-branches.sh [preview|apply DELETE_OLD_ACHEWIW_BRANCHES]"
[[ "$(git branch --show-current)" == main ]] || die "Switch to main first."
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || die "Uncommitted changes found; nothing deleted."
case "$(git remote get-url origin)" in
  https://github.com/musikfed/achewiw|https://github.com/musikfed/achewiw.git|git@github.com:musikfed/achewiw|git@github.com:musikfed/achewiw.git) ;;
  *) die "Unexpected Git origin; nothing deleted.";;
esac
if [[ "$ACTION" == apply && "$CONFIRM" != "DELETE_OLD_ACHEWIW_BRANCHES" ]]; then
  die "For deletion use: apply DELETE_OLD_ACHEWIW_BRANCHES"
fi
git fetch --no-tags origin
git merge-base --is-ancestor HEAD refs/remotes/origin/main ||
  die "Local HEAD does not belong to the current GitHub main."
branches=(
 feature/canon-storygraph
 feature/storyengine-mvp
 feature/publishing-pipeline-rfc
 feature/release-gate-technical-books
 feature/safe-library-reset-rebuild-20261104
 feature/server-edits
 feature/technical-epub-first
 feature/production-conveyor-rtx3060
)
for branch in "${branches[@]}"; do
  [[ "$branch" != main && "$branch" != archive/dual-model-20261105 ]] || die "Allowlist is unsafe."
  if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    echo "SKIP (already absent): $branch"
    continue
  fi
  if ! git merge-base --is-ancestor "refs/remotes/origin/$branch" "refs/remotes/origin/main"; then
    die "STOP: $branch has commits outside main history; preserve/merge first."
  fi
  if [[ "$ACTION" == preview ]]; then
    echo "CAN DELETE: $branch (all commits preserved in main ancestry)"
  else
    git push origin --delete "$branch"
    echo "DELETED: $branch"
  fi
done
printf '\nKeep: main + archive/dual-model-20261105\n'
