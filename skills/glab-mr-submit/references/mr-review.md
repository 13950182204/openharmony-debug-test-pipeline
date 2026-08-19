# MR Review Protocol

Use this reference for the six-dimensional review that follows a real MR creation or an explicit review of an existing MR.

## Contents

- [Authority and context](#authority-and-context)
- [Reviewer boundaries](#reviewer-boundaries)
- [Prompt and finding contract](#prompt-and-finding-contract)
- [Aggregation and repair gates](#aggregation-and-repair-gates)
- [Retry and re-review](#retry-and-re-review)
- [Final conversation report](#final-conversation-report)

## Authority and context

The GitLab MR is authoritative. Resolve the project path and MR IID, then query the current MR metadata and changes:

```bash
glab api --hostname 192.168.11.238 \
  "projects/<encoded-project-path>/merge_requests/<iid>"
glab api --hostname 192.168.11.238 \
  "projects/<encoded-project-path>/merge_requests/<iid>/changes"
```

Use `diff_refs.base_sha` as the base and the current MR `sha` as the head. Use the API `source_branch`, `target_branch`, and `/changes` paths in the prompt. Validate the exact diff locally with:

```bash
git diff --name-status <base_sha>...<head_sha>
git diff --check <base_sha>...<head_sha>
```

If the local checkout does not match the MR source branch, do not substitute `HEAD`. Use a clean review worktree at the MR head; for repair, use a clean worktree that can push the MR source branch.

## Reviewer boundaries

Each category gets exactly one independent read-only review Subagent. Categories are intentionally narrow:

| Category | Report | Do not report |
| --- | --- | --- |
| Security | Permission bypass, sensitive-data exposure, unsafe input/path handling, injection, resource exhaustion, and security regressions | Generic robustness or style without a security impact |
| Code quality | Incorrect API contracts, error handling, resource lifetime, compatibility, and risky local design | Pure naming or formatting preferences |
| Bug | Observable functional regressions, incorrect state transitions, boundary errors, and broken user/test behavior | Theoretical issues without a concrete trigger |
| Race condition | Async ordering, lifecycle callbacks, concurrent read-modify-write, stale events, and cleanup interleavings | Ordinary sequential logic errors |
| Test stability | Flaky timing, nondeterministic ordering, leaked state, incomplete cleanup, device/environment coupling, and repeat-run failures | Product behavior defects unless they directly destabilize tests |
| Maintainability | Duplication, hidden coupling, unclear ownership, brittle test data, and changes likely to create future defects or costly maintenance | Subjective style preferences and low-impact refactors |

The same root cause may be noticed by multiple categories. The parent Agent keeps one canonical finding and records the other categories as related tags.

## Prompt and finding contract

Every prompt must include:

- Repository absolute path.
- MR IID and URL when available.
- Source and target branch names.
- Exact `base_sha` and `head_sha`.
- `git diff --name-status base_sha...head_sha` output or equivalent exact path list.
- The assigned category and its boundary above.
- A read-only constraint: no file edits, commits, pushes, MR comments, or worktree cleanup.

Use this prompt shape, replacing bracketed values:

```text
Review MR ![iid] in [repo] for [category] only.
Authoritative diff: [base_sha]...[head_sha].
Source branch: [source_branch]. Target branch: [target_branch].
Inspect the exact diff and necessary base-code context. Do not modify files,
commit, push, comment on the MR, or include unrelated worktree changes.
Report only evidence-backed findings. For each finding provide:
severity (P0/P1/P2/P3), confidence (high/medium/low), absolute file path,
one-based line, short title, trigger, evidence, impact, and recommendation.
If no issue is found, return exactly: no findings.
```

Normalize each result to this logical shape before aggregation:

```text
category: security|code-quality|bug|race|test-stability|maintainability
severity: P0|P1|P2|P3
confidence: high|medium|low
path: /absolute/path/to/file
line: 123
title: concise problem statement
trigger: concrete triggering condition
evidence: changed code and relevant base behavior
impact: user, test, data, security, or maintenance impact
recommendation: minimal repair direction
```

## Aggregation and repair gates

Severity is based on impact, not on how large the diff is:

- `P0`: release-blocking security or data-integrity failure, or a broad crash/corruption path.
- `P1`: high-confidence functional failure, security bypass, persistent data loss, or a race that can invalidate the MR's primary behavior.
- `P2`: meaningful regression, resource leak, intermittent failure, or operational risk with a bounded trigger.
- `P3`: low-risk defect or maintainability concern that should be recorded but is not automatically repaired.

Auto-repair requires a high-confidence or reproducible finding, a concrete trigger, and a path already changed by the MR. P0-P2 findings qualify; P3 findings do not. Before editing, the parent Agent must inspect the exact diff and verify the finding rather than applying a suggestion blindly.

The repair must:

- Stay on the existing MR source branch and in its clean source worktree.
- Touch only the original MR paths.
- Preserve the original MR description and avoid MR comments.
- Use exact-path staging and a follow-up commit with the applicable OpenHarmony title/body format.
- Run `git diff --check` and relevant targeted tests before push.

If any repair needs a new path, a changed public contract, a broad refactor, or an unresolved product decision, stop automatic repair and report the scope expansion.

## Retry and re-review

Spawn all six reviewers before waiting. Wait for all six terminal states. Retry only failed categories once with a fallback model. A missing category after retry makes the review incomplete and blocks auto-repair.

After a repair push, refresh `base_sha`, `head_sha`, and changed paths from GitLab. Run all six categories again exactly once. Do not review only the changed hunk: review the complete latest MR diff against the latest authoritative base. A remaining confirmed P0-P2 finding or any incomplete category leaves the MR unresolved and ends the automatic loop.

## Final conversation report

Use this order:

1. Review scope: MR IID, source/target branches, base/head SHA, and changed-path count.
2. Findings, sorted by severity, with category, confidence, path:line, trigger, impact, and recommendation.
3. Clean categories explicitly marked `no findings`.
4. Repair commit and pushed source SHA, if a repair occurred.
5. Test commands/results and any unavailable validation.
6. Re-review status and unresolved risk.

Do not claim “review passed” when any category is incomplete, any confirmed P0-P2 finding remains, or required tests were not run and the gap is material.
