---
name: openharmony-test-report-triage
description: Use when investigating OpenHarmony ACTS, HATS, DCTS, XTS, or xdevice test report directories. Given a report path and optional source/product paths, summarize failed, blocked, and timeout cases, map them to test source and hilog evidence, classify root causes, and propose product, framework, or test-side fixes.
metadata:
  short-description: Triage OpenHarmony test reports
---

# OpenHarmony Test Report Triage

Use this skill when the user provides an OpenHarmony test report directory or asks why ACTS/HATS/DCTS/XTS cases failed and how to fix them.

## Inputs to infer or ask for

- Report directory, for example `.../zxts/YYYY-MM-DD-HH-MM-SS`.
- Test source root, often `test/xts/acts`, `test/xts/hats`, `test/xts/dcts`, or a parent `test/xts`.
- Product source root, if product/framework config changes may be needed.
- Product name or board name, if HDF/component features may differ by product.

If paths are omitted, search from the current workspace first. Avoid asking unless the report path or source root cannot be inferred safely.

## Triage workflow

1. Build the failure table.
   - Read `task_log.log` when present.
   - Read `result/*.xml` for exact testcases, messages, failed counts, and timeout messages.
   - Read each module's `module_run.log` to identify the first failing case and whether later blocked cases are fallout from a timeout.
   - Use `scripts/summarize_report.py` for a first pass when useful.

2. Locate the test source.
   - Search with `rg` for the testcase name, class name, or distinctive API/assert text.
   - For ACTS/HATS/DCTS, do not assume one fixed root. Search under the user-provided source root, then likely `acts`, `hats`, and `dcts` subtrees.
   - Read the smallest useful source span around the failing case and helper callbacks.

3. Extract runtime evidence.
   - Search module hilog files, including `.gz`, for testcase name, `OHOS_REPORT_STATUS`, assertion text, callback logs, error codes, timeout markers, service crash/freeze hints.
   - Use `scripts/extract_case_hilog.py` when useful.
   - Prefer concrete evidence from logs over guesses from XML summaries.

4. Classify the root cause.
   - Product capability mismatch: the product lacks hardware or feature capability but exposes it through HDF/config/model/mock.
   - HDF or product config issue: inherited common config enables a driver model or feature that this product should override.
   - Framework/API behavior issue: service state, callback, return code, or error code does not match API contract or test expectation.
   - Test assertion tolerance issue: numeric precision, timing, or platform-dependent boundary is too strict.
   - Test harness/environment issue: missing resource, install/push failure, permission, display/surface, network, time/date, or device mode.
   - Test code hang: rejected promise, error branch, or callback path does not call `done()`, causing timeout and blocked fallout.
   - Real crash/freeze: cppcrash, appfreeze, SERVICE_BLOCK, or process restart evidence exists.

5. Check upstream fixes before finalizing or editing.
   - For ACTS, check `https://gitcode.com/openharmony/xts_acts`.
   - For HATS, check `https://gitcode.com/openharmony/xts_hats`.
   - For DCTS, check `https://gitcode.com/openharmony/xts_dcts`.
   - For product, framework, service, driver, or common source fixes, map the local source path to the corresponding `https://gitcode.com/openharmony/<repo>` repository and check the same release branch for similar patches before making or finalizing local edits.
   - Prefer the user's target branch when provided; otherwise check both `OpenHarmony-6.1-Release` and `OpenHarmony-6.1-LTS`.
   - Compare the exact testcase source and nearby helpers before proposing a local-only fix.
   - If upstream already changed the testcase, helper, framework, product, or service source, report the upstream branch, commit hash, file path, and minimal backport diff.

6. Propose fixes in priority order.
   - Prefer durable product or framework fixes when the product is wrong.
   - Prefer product feature/HDF overrides for absent hardware capabilities.
   - Use test-side changes only when the test is too strict, has a known race, or the user explicitly accepts a local XTS workaround.
   - If any testcase source is changed, compile that testcase package or the smallest owning test module separately before treating the fix as locally validated.
   - For OpenHarmony standard-system ACTS/HATS/DCTS testcase packages, prefer the suite wrapper for first-time build or target discovery, then use root `build.sh --fast-rebuild --build-target <suite-path>:<target>` for repeated ETS-only rebuilds when `out/<product>/build.ninja` already exists.
   - After rebuilding external-runner artifacts, sync ACTS outputs to `/home/cx/os/acts/new_acts_testcase/` and HATS outputs to `/home/cx/os/hats/new_hats_testcase/` as the user's export copy; also update the runner `testcases/` directory when rerunning locally.
   - Report the exact single-module compile command, whether it used the full suite wrapper or fast rebuild path, whether it passed, and the output package/artifact path when available.
   - Do not remove assertions or skip cases without explaining why that is a workaround, not a product fix.

7. Submit testcase fixes with the local MR convention when the user asks to submit.
   - Submit each test module or testcase project as a separate branch, commit, and MR unless the user explicitly asks for a combined MR.
   - Do not use a generic agent branch prefix for these ACTS/XTS repair MRs. Use the local convention, for example `v1.1.x/v6.1.0.31_<TestModule>_测试项Failed`.
   - When creating the MR, explicitly assign it to Chen Xin with `--assignee cx`.
   - When creating the MR, explicitly enable source branch deletion after merge with `--remove-source-branch=true`.
   - Keep the MR target branch aligned with the user's current base branch, for example `v1.0.15/v6.1.0.31_modem` when that is the active target.
   - If `glab-mr-submit` is available, use it with equivalent assignee, target branch, and source-branch-deletion options. If it is not available, use `glab mr create` with explicit options instead of relying on GitLab defaults.
   - Before creating each MR, verify the branch diff contains only the corresponding testcase project's files.

8. Report each issue with this structure:
   - Module
   - Failed/blocked count
   - Failing testcase(s)
   - What the testcase is testing
   - Test resource/input data, such as URL, file path, device capability, permission, or system setting
   - Test flow: setup, API calls, callbacks/events waited for, assertions, cleanup
   - Evidence: XML/module log/hilog/source references
   - Upstream check result, including branch/commit/path when available
   - Root cause
   - Recommended fix
   - Files to change
   - Testcase compile command and result, required when testcase source is changed
   - Validation or rerun recommendation
   - Residual risk

## References

- For known patterns and examples, read `references/failure_patterns.md`.
- For HDF/product feature override patterns, read `references/hdf_feature_overrides.md`.
- For report layouts and search strategy, read `references/report_layouts.md`.
- For upstream repository checks, read `references/upstream_checks.md`.
- For testcase single-module build and fast rebuild commands, read `references/fast_rebuild.md`.
- For output formatting, read `references/output_template.md`.
