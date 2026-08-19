# Report Layouts

OpenHarmony ACTS/HATS/DCTS/XTS reports usually come from xdevice-like runners, but exact paths vary by suite and version. Discover files rather than hard-coding one layout.

## Common files

- `task_log.log`: total module/case summary and top-level runner messages.
- `result/*.xml`: JUnit-like testcase result files. This is the fastest source of failed testcase names and timeout messages.
- `<ModuleName>/module_run.log`: module command line, execution order, pass/fail/blocked fallout, timeout values.
- `<ModuleName>/hilog_*/hilog*.gz`: runtime logs. Search compressed files with `zgrep` or a script.
- `faultlog`, `cppcrash`, `appfreeze`, `SERVICE_BLOCK`: use when failures suggest crash, freeze, or service block.

## Search order

1. Parse XML results for failed/timeout testcase names.
2. Use module logs to identify the first failing case and blocked cascade.
3. Locate the testcase source with `rg`.
4. Extract hilog around the testcase start, callback logs, and failure timestamp.
5. Check crash/freeze logs only when symptoms justify it.

## Source lookup

Search under the path supplied by the user first. If the user gives a parent path, try:

- `test/xts/acts`
- `test/xts/hats`
- `test/xts/dcts`
- `test/xts`

Search keys, in order:

- exact testcase name
- class or suite name
- tail component of testcase name
- logged strings from hilog
- API name under test

