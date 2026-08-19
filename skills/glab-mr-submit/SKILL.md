---
name: glab-mr-submit
description: Create, review, repair, and update compliant GitLab merge requests with glab for local git changes, including structured OpenHarmony action/chip/XTS titles, automatic labels and non-blocking milestone matching, standardized XTS/DCTS/test-report Markdown records, automatic test-result screenshot uploads, v6.1.0.31 release worktree isolation, exact-file staging, six-dimensional post-creation Subagent review, same-MR repair updates, worktree safety checks, push, and post-creation MR verification. Use when the agent is asked to submit or prepare an MR, review an existing MR or current branch against a target branch, push a fix branch, attach test evidence, repair review findings, or enforce the company MR workflow.
---

# glab MR Submit

## Purpose

Use this skill to turn a confirmed local fix into a compliant GitLab MR. Prefer the bundled script for execution because it validates commit/MR format, branch naming, persistent GitLab authentication, worktree/index integrity, exact file staging, screenshot uploads, `glab` arguments, and the final remote MR contents. After every real MR is created and verified, continue with the post-create six-dimensional review loop below; never run that loop for `--dry-run`.

Before executing a real commit, push, or MR creation, state the files to be submitted and the generated commit title/body. Never use `git add .`. Do not log out of GitLab after submission.

## Title and Commit Format

Every MR title uses space-separated bracket fields in this order:

```text
[动作] [芯片] [XTS] <说明>
```

Use one action from `修改`, `优化`, `升级`, `新增`, `修复`, `同步`, `回退`, `重构`, `适配`, `迁移`, or `移除`. The chip field is optional and may be either `[RK3568]` or `[A333/A537]`, never both. Omit the chip field for a common change. Add `[XTS]` for XTS/HATS/ACTS/DCTS or `test/xts` changes. Do not use the legacy compact form `[修改][XTS]`.

Examples:

```text
[修改] [RK3568] 修改 RK3568 EDP 屏幕系统调光失效问题
[修改] [A333/A537] 同步全志开源鸿蒙v0.9版本GPU库与HWC
[修改] [XTS] 修改 validator PlayerVideo、CapiDrag 测试通过状态异常问题
[优化] [RK3568] 优化 DNAKE 侧启动时序
```

For a common change that spans both chips, keep the title chip-free and pass the additional chip labels with repeated `--label` options when needed.

Use this ordinary fix body format:

```text
[修改] 修改 <问题对象> <问题现象>问题

具体:
    修改<失败原因或报错现象> 的问题
```

Use the same title grammar for XTS/HATS/ACTS/DCTS/test-report related fixes:

```text
[修改] [XTS] 修改 <测试模块或用例名> <修改内容或失败现象>问题

具体:
    修改<测试失败原因、环境差异或报错现象> 的问题
```

Rules:

- Use the action/chip/XTS title grammar above; the bundled script rejects unknown actions, duplicate chip fields, compact bracket fields, and missing summaries.
- Use `[XTS]` whenever paths, title, or context mention XTS, HATS, ACTS, DCTS, test report triage, or test modules under `test/xts`.
- The script derives project labels from the title: a chip field adds its chip label, `[XTS]` adds `XTS`, and a chip-free title adds `通用框架层修改`. Use repeated `--label` options for additional labels such as a cross-chip scope or a project-specific label.
- The script validates every derived/explicit label against the project before creating the MR.
- Use the first commit line exactly as the MR title.
- Include `具体:` in the commit body. The text after `具体:` must explain the reason or observed failure, not only say that files changed.
- Use the commit body exactly as the MR description. Do not use `glab mr create --fill`.
- For test-related MRs and any MR using `--screenshot`, use the required Markdown record format in [references/mr-description-template.md](references/mr-description-template.md). Every required section is a level-2 heading ending in `:`.
- Put test commands in a fenced `bash` code block. Do not use four-space indentation for commands or evidence.
- Include a `## 测试结果截图:` section with one or more Markdown images. Prefer `--screenshot PATH` so the script uploads local evidence and inserts the returned `/uploads/...` link.
- Treat each HATS/ACTS/DCTS executable or test MODULE as an independent submission unit. Submit its direct test correction in a separate commit and MR; do not combine multiple module names, changed test files, or their pass evidence in one MR.
- When one unavoidable change belongs to a shared test harness rather than a single MODULE, submit a separate MR titled for the shared harness. Do not mix it with a direct module fix. Validate and record every affected MODULE independently in that MR.

### Labels and Milestones

`create_glab_mr.py` passes the resolved labels to `glab mr create --label`. An optional `--milestone` accepts an active milestone title, global ID, or IID. Without it, the script matches the generated source branch first and the title/body second against active milestone versions such as `V1.2.0`; `v1.2.x` may match a unique `V1.2.*` milestone. No match, ambiguity, or conflict leaves the milestone unset and does not block submission. Set `MILESTONE_REQUIRED=true` only for a project that explicitly wants missing matches to fail.

## Branch and MR Defaults

Load defaults from `assets/defaults.env`:

- Remote: `origin`
- Base version: `v6.1.0.31`
- Target branch: `v6.1.0.31_release`
- Required worktree base ref: `origin/v6.1.0.31_release`
- Iteration version: `auto`
- Fallback iteration: `v1.1.x`
- MR assignee: `@cx`
- GitLab host: `192.168.11.238`
- GitLab API protocol: `http`
- Git protocol: `ssh`
- Milestone matching: `branch_then_message`, non-blocking by default

Branch format:

```text
<iteration-version>/v6.1.0.31_<problem-name>
```

When iteration is `auto`, infer the highest semantic version from remote branches matching `v*/v6.1.0.31_*` and use the next patch version, for example `v1.1.3` -> `v1.1.4`. If inference fails, use the fallback value in `defaults.env`.

Derive `<problem-name>` from the commit title by removing all structured action/chip/XTS fields, the leading action word when repeated in the summary, and trailing `问题`, then converting the remaining text into a safe branch suffix. The script performs this transformation.

## Release Worktree Rule

Submit from a fresh worktree based on `origin/v6.1.0.31_release`. Do not submit directly from long-lived product, modem, feature, or dirty integration branches.

Use this flow when the fix was developed in another worktree:

```bash
git diff -- path/one path/two > /tmp/fix.patch
git worktree add -b <temporary-release-branch> /home/cx/os/worktrees/<slug> origin/v6.1.0.31_release
cd /home/cx/os/worktrees/<slug>
git apply /tmp/fix.patch
```

The helper script verifies that `origin/v6.1.0.31_release` is an ancestor of the current `HEAD` before it creates the final source branch, commit, push, or MR. If this check fails, create a new release worktree and apply only the selected patch there.

For a large repository where a full checkout is impractical, an index-only worktree is allowed only after initializing its index and skip-worktree bits. The required sequence is:

```bash
git worktree add --no-checkout /home/cx/os/worktrees/<slug> origin/v6.1.0.31_release
git -C /home/cx/os/worktrees/<slug> read-tree HEAD
git -C /home/cx/os/worktrees/<slug> ls-files -z | git -C /home/cx/os/worktrees/<slug> update-index --skip-worktree -z --stdin
git -C /home/cx/os/worktrees/<slug> update-index --no-skip-worktree -- path/one path/two
git -C /home/cx/os/worktrees/<slug> checkout HEAD -- path/one path/two
```

Before running the submission script, `git status --porcelain` must contain only the selected files. The script refuses an empty index, unexpected deletions, or staged paths outside `--files`.

## Recommended Execution

The title-derived labels are automatic. `--label` below is optional for additional labels, and `--milestone` is an optional explicit override; omit it to use the non-blocking branch-then-message matcher.

1. Inspect `git status --short` and identify only files that belong to this MR.
2. Create a fresh release worktree from `origin/v6.1.0.31_release` and apply only the selected patch there.
3. Write the commit message to a temporary file, for example `/tmp/mr_message.txt`.
4. Dry-run the script first:

```bash
python3 {{SKILLS_DIR}}/glab-mr-submit/scripts/create_glab_mr.py \
  --repo /home/cx/os/worktrees/<slug> \
  --message-file /tmp/mr_message.txt \
  --files path/one path/two \
  --screenshot /tmp/test-result.png \
  --base-ref origin/v6.1.0.31_release \
  --target-branch v6.1.0.31_release \
  --assignee cx \
  --label 应用修复 \
  --milestone '开源鸿蒙V6.1 DNAKE V1.2.0 release版本' \
  --hostname 192.168.11.238 \
  --dry-run
```

5. Review generated branch, MR title, standardized MR description, screenshot upload plan, assignee, and exact staged file list. Dry-run never uploads, commits, pushes, or creates an MR.
6. Execute for real only after the user asked to submit the MR:

```bash
python3 {{SKILLS_DIR}}/glab-mr-submit/scripts/create_glab_mr.py \
  --repo /home/cx/os/worktrees/<slug> \
  --message-file /tmp/mr_message.txt \
  --files path/one path/two \
  --screenshot /tmp/test-result.png \
  --base-ref origin/v6.1.0.31_release \
  --target-branch v6.1.0.31_release \
  --assignee cx \
  --label 应用修复 \
  --milestone '开源鸿蒙V6.1 DNAKE V1.2.0 release版本' \
  --hostname 192.168.11.238
```

The script uploads each screenshot to `projects/<encoded-project-path>/uploads`, appends the returned Markdown image to the screenshot section, and uses the resulting body for both the commit and MR. After creation it verifies the title grammar, labels, milestone, target branch, description, screenshot links, and exact changed paths through the GitLab API. On successful real creation, preserve the repository path and source branch and continue with the post-create review loop.

## Post-create Six-Dimension Review

Read [references/mr-review.md](references/mr-review.md) before starting this phase. This phase runs for every real MR created by this skill and for an explicitly requested review of an existing MR. It does not run during dry-run and it does not publish comments or modify the MR description.

### Resolve the review context

1. Parse the created MR URL and IID from the verified script output. For an existing MR, use the supplied URL or IID.
2. Query the MR through the GitLab API and use its current `diff_refs.base_sha`, `diff_refs.head_sha`/`sha`, `source_branch`, and `target_branch` as the review authority. Do not assume the current checkout or a similarly named local branch is the MR source.
3. Query `/changes` to capture the exact original changed paths. Refresh the MR metadata and changed paths at the start of the repair pass and the re-review pass.
4. Review only `base_sha...head_sha`, with the repository absolute path, MR identity, branch names, and exact diff file list included in every Subagent prompt. Keep all Subagents read-only: no edits, commits, pushes, MR comments, or unrelated worktree changes.

### Run and aggregate the reviewers

1. Spawn six independent read-only review Subagents in parallel, one for each category: security, code quality, bugs, race conditions, test stability, and maintainability. Use the common context and category-specific prompts in the reference document.
2. Wait for all six terminal results. If an agent errors, times out, or hits model capacity, retry that category once with a fallback model. If the retry also fails, mark the review incomplete and do not auto-fix or claim a clean review.
3. Require every finding to include category, severity, confidence, absolute file path, line, trigger, evidence, impact, and recommendation. Require an explicit `no findings` result for clean categories.
4. Deduplicate findings with the same root cause. Keep the highest justified severity, preserve cross-category tags, and downgrade speculative findings instead of treating them as confirmed defects.
5. Report findings first in severity order, then clean categories, test status, incomplete dimensions, and residual risk. Keep the report in the conversation only.

### Repair and update the existing MR

1. Automatically repair only confirmed, reproducible P0-P2 findings. Do not automatically implement P3 findings or subjective style suggestions.
2. The main Agent performs the edits in the existing source worktree and branch; review Subagents never edit. Before editing, re-check that the worktree is clean and that every proposed file is in the original MR path set.
3. If a repair needs a new file or any path outside the original MR, stop and report the scope expansion instead of silently enlarging the MR.
4. Run `git diff --check` and the narrowest relevant build/test commands after repair. Stage exact paths with `git add -- <files>`, create a follow-up commit using the structured title format, and push the existing source branch. Never create a second MR or use `git add .`.
5. Preserve the original MR description and add no automatic MR comment. A follow-up commit may describe the confirmed review findings and test evidence.

### Re-review once

After a repair push, refresh the MR metadata and run all six reviewers again against the latest MR diff. This is one complete re-review, not an unbounded loop. If the re-review is incomplete or still reports confirmed P0-P2 findings, leave the MR unchanged after the latest push and report the unresolved items and required manual follow-up. If it is clean, report the updated MR SHA, repair commit, tests, and six clean dimensions.

## Persistent Authentication

Configure the internal GitLab host once on the local machine. Prefer the system keyring; fall back to glab's local configuration only when the keyring is unavailable:

```bash
glab auth login \
  --hostname 192.168.11.238 \
  --api-host 192.168.11.238 \
  --api-protocol http \
  --git-protocol ssh \
  --use-keyring
```

The token must be entered through glab's prompt or standard input and must never be written to this skill, a repository, a commit message, an MR description, or command output. The script runs `glab auth status --hostname 192.168.11.238` before a submission and stops with an actionable error if authentication is missing. It does not revoke or log out the persistent credential.

## Standard Record

Read [references/mr-description-template.md](references/mr-description-template.md) before writing a test-related message or any MR that includes screenshots. The standard order is:

~~~markdown
## 具体:

## 问题分析:

## 修改内容:

## 测试环境:

## 测试命令:

```bash
command
```

## 测试结果:

## 测试结果截图:
~~~

Ordinary changes without test evidence still require the structured title grammar and `具体:`, but do not require the standard record. Once a manual test, command, or `--screenshot` is included, use the full Markdown record so GitLab renders each section consistently.

## Safety Checks

Stop and ask the user before execution if:

- The target branch cannot be found locally or on the remote.
- The current worktree is not based on `origin/v6.1.0.31_release`.
- GitLab authentication for `192.168.11.238` is missing or invalid.
- The Git index is empty, incomplete, or contains unexpected worktree changes.
- Any requested file does not exist or has no changes.
- The working tree contains unrelated changes and the user has not clearly selected files.
- The commit message fails validation.
- The title does not use a supported `[动作] [芯片] [XTS] <说明>` form, or a test-related title omits `[XTS]`.
- A derived or explicit label is missing from the target GitLab project, or an explicit chip/XTS label conflicts with the title fields.
- A test-related or screenshot-bearing message has missing required headings, a non-fenced command, or no screenshot link/path.
- The staged paths or post-create MR changes do not exactly match the requested files.
- The generated remote branch already exists and a unique suffix cannot be generated.
- The six-dimensional review is incomplete after one retry for any category.
- An automatic repair would modify a path outside the original MR change set.
- A review finding is speculative, P3-only, or cannot be reproduced from the exact MR diff.
- A post-repair test or one-time re-review cannot be completed; report the MR as unresolved instead of claiming success.

The script automatically appends `_2`, `_3`, etc. when a generated branch exists locally or remotely.
