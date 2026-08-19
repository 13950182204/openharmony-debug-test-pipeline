# MR Description Template

Use this template for XTS, HATS, ACTS, DCTS, test-report-related merge requests, and any MR with manual test evidence or screenshots. The first line remains the commit/MR title; the body below is used unchanged for both the commit and the MR description.

~~~markdown
## 具体:

说明本次修改解决的具体失败现象、错误信息或行为问题。

## 问题分析:

说明复现条件、根因、时序/环境差异，以及为什么原实现会失败。

## 修改内容:

- 修改点 1
- 修改点 2

## 测试环境:

- DUT: `设备 ID` (`IP:port`)
- 对端或 DCTS 盒子: `设备 ID` (`IP:port`)
- 测试 HAP: `/path/to/test.hap`
- HAP SHA256: `sha256`

## 测试命令:

```bash
python3 -m xdevice run ...
```

## 测试结果:

- `TestModule`: `N/N` 通过，失败 `0`，错误 `0`，unavailable `0`
- 报告: `/path/to/summary_report.html`

## 测试结果截图:

<!-- 使用 --screenshot PATH 自动上传；不要把本机路径直接当作图片链接。 -->
~~~

## Formatting Rules

- Use exactly the level-2 headings and order shown above. Keep the trailing `:` so GitLab renders the headings consistently.
- Keep commands inside a fenced `bash` block. Use inline backticks for IDs, paths, package names, and error symbols.
- Use Markdown bullets for environment and result records instead of four-space indentation.
- Put the uploaded image Markdown directly under `## 测试结果截图:`. The upload path must be a GitLab `/uploads/...` link, not a local `/tmp` or Windows path.
- Keep analysis factual: include the observed failure, the root cause, the changed behavior, and the test evidence.

For ordinary non-test MRs without manual evidence, use the structured title format `[动作] [芯片] [XTS] <说明>` (the chip and XTS fields are optional under the rules in `SKILL.md`) and keep the `具体:` body requirement. Once commands, results, or screenshots are recorded, use this complete Markdown format.
