#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Applies the repository protections listed in SECURITY.md's maintainer notes with the GitHub CLI.
# Safe to run again and again: each step is reported as OK or SKIP (with GitHub's reason), and an
# existing ruleset is updated, not duplicated. Several settings only exist for a public repository
# (or on a paid plan), so run it once now and once more right after making the repo public.
#
#   gh auth login                      # an account with admin rights on the repository
#   dev/github_hardening.sh            # the repository of the current folder
#   dev/github_hardening.sh owner/repo
set -u
REPO="${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
echo "Repository: $REPO"

step() {                       # step "what" command...   (prints OK, or SKIP with the reason)
  local what="$1"; shift
  local out
  if out=$("$@" 2>&1); then printf '  OK    %s\n' "$what"
  else printf '  SKIP  %s\n          %s\n' "$what" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-200)"; fi
}

ruleset() {                    # ruleset "name" 'json'   (create, or update the one with that name)
  local name="$1" body="$2" id
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name==\"$name\") | .id" 2>/dev/null | head -1)
  case "$id" in ''|*[!0-9]*) id="";; esac        # an error message is not an id
  if [ -n "$id" ]; then step "ruleset '$name' (update)" gh api -X PUT "repos/$REPO/rulesets/$id" --input - <<<"$body"
  else step "ruleset '$name' (create)" gh api -X POST "repos/$REPO/rulesets" --input - <<<"$body"; fi
}

echo "Security features"
step "Dependabot alerts"                 gh api -X PUT "repos/$REPO/vulnerability-alerts"
step "Dependabot security updates"       gh api -X PUT "repos/$REPO/automated-security-fixes"
step "private vulnerability reporting"   gh api -X PUT "repos/$REPO/private-vulnerability-reporting"
step "secret scanning + push protection" gh api -X PATCH "repos/$REPO" --input - <<<'{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}'
step "CodeQL for Python (default setup)" gh api -X PATCH "repos/$REPO/code-scanning/default-setup" -f state=configured -f query_suite=default -f 'languages[]=python'

echo "Rulesets"
# main: no deletion, no force-push, changes go through pull requests (0 approvals, so a solo
# maintainer can merge their own; repository admins may still push directly: bypass_mode "always").
# Change "always" to "pull_request" to make even admins use pull requests.
ruleset "protect main" '{
  "name": "protect main", "target": "branch", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "rules": [
    {"type": "deletion"}, {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {"required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false, "require_code_owner_review": false,
      "require_last_push_approval": false, "required_review_thread_resolution": false}}],
  "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]}'
# release tags: nobody (admins included) can move or delete a v* tag; new ones can still be created.
# To fix a tag on purpose, disable this ruleset first, then enable it again.
ruleset "protect release tags" '{
  "name": "protect release tags", "target": "tag", "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
  "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "update"}],
  "bypass_actors": []}'

echo "Releases"
step "immutable releases (applies to releases published after this)" gh api -X PUT "repos/$REPO/immutable-releases"

echo "Actions"
step "workflow token read-only, no approving pull requests" gh api -X PUT "repos/$REPO/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
step "approval for workflows from all outside contributors" gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" -f approval_policy=all_external_contributors

echo "Features nobody needs here (less spam and impersonation)"
step "wiki, projects, discussions off" gh repo edit "$REPO" --enable-wiki=false --enable-projects=false --enable-discussions=false

echo
echo "State now"
gh api "repos/$REPO" --jq '"  visibility=\(.visibility) wiki=\(.has_wiki) projects=\(.has_projects) discussions=\(.has_discussions) issues=\(.has_issues)"'
gh api "repos/$REPO/rulesets" --jq '.[] | "  ruleset: \(.name) (\(.target), \(.enforcement))"' 2>/dev/null || echo "  rulesets: not available yet (needs a public repository on a free plan)"
exit 0
