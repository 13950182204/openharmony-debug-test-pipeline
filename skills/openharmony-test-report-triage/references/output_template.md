# Output Template

Use concise, source-backed output. Prefer one section per module or root cause.

```text
模块：
失败/Blocked：
失败用例：
测试内容：
测试资源/输入：
测试流程：
关键证据：
源码位置：
上游检查：
根因：
推荐改法：
涉及文件：
用例单编：
验证建议：
风险/备注：
```

When multiple cases share one root cause, group them together. Lead with the first failing case when blocked cases are cascade fallout.

When testcase source is changed, `用例单编` is required. Include the smallest compile target or command, whether it used the suite wrapper or root `--fast-rebuild`, pass/fail result, and generated package/artifact path if the build prints one. If the build cannot be run in the current environment, state the blocker and still provide the exact command to run.
