---
name: openharmony-test-report-triage
description: 调查 OpenHarmony ACTS、HATS、DCTS、XTS 或 xdevice 测试报告目录时使用。给定报告路径与可选的源码/产品路径，汇总失败、阻塞、超时用例，映射到测试源码与 hilog 证据，分类根因，并提出产品侧、框架侧或测试侧修复建议。
metadata:
  short-description: 分诊 OpenHarmony 测试报告
---

# OpenHarmony 测试报告分诊

当用户提供 OpenHarmony 测试报告目录，或询问 ACTS/HATS/DCTS/XTS 用例为何失败、如何修复时，使用本 skill。

## 需要推断或询问的输入

- 报告目录，例如 `.../zxts/YYYY-MM-DD-HH-MM-SS`。
- 测试源码根，通常是 `test/xts/acts`、`test/xts/hats`、`test/xts/dcts`，或上级 `test/xts`。
- 产品源码根，如果可能需要产品/框架配置改动。
- 产品名或板名，如果 HDF/组件特性因产品而异。

路径缺失时，先从当前工作区搜索。除非报告路径或源码根无法安全推断，否则避免追问。

## 硬性规则

1. **测试用例/测试包侧的问题，不得靠修改测试用例来通过，必须撰写豁免证据。**
   - 适用：分诊结论为「非产品、非框架缺陷」，包括用例逻辑/断言强度、测试夹具与资源、
     测试包（HAP/字节码 abc 版本/SDK 与分支工具链/签名）与测试环境（设备能力、显示、权限、
     镜像版本不匹配）导致的失败。
   - 禁止：修改用例源码或辅助代码、删除或放宽断言、跳过/禁用用例、改测试包常量迁就环境，
     都不得作为让测试变绿的手段。
   - 必须：转豁免路径，逐项产出豁免证据（失败 case 清单、失败原因、可复核的证据链、
     申请豁免理由、证据附件路径），草稿用 skill `openharmony-waiver-draft` 产出；
     缺测评编号或账号密码时按该 skill 停止并说明，不得自行编造。
   - 例外：仅当用户明确要求「修改测试用例」并接受其为本地 XTS workaround 时才可动用例源码，
     且在报告里标注为 workaround 而非产品修复。

## 分诊流程

1. 建立失败表。
   - 存在时读取 `task_log.log`。
   - 读取 `result/*.xml` 获取精确用例名、消息、失败数与超时消息。
   - 读取每个模块的 `module_run.log`，定位第一个失败用例，并判断后续阻塞用例是否是超时的连带结果。
   - 需要时可先用 `scripts/summarize_report.py` 做第一轮汇总。

2. 定位测试源码。
   - 用 `rg` 搜索用例名、类名或特征性的 API/断言文本。
   - 对 ACTS/HATS/DCTS，不要假定单一固定根目录。先在用户提供的源码根下搜索，再搜索可能的 `acts`、`hats`、`dcts` 子树。
   - 只读失败用例及其辅助回调周围最小有用的源码片段。

3. 提取运行期证据。
   - 在模块 hilog 文件（含 `.gz`）中搜索用例名、`OHOS_REPORT_STATUS`、断言文本、回调日志、错误码、超时标记、服务崩溃/卡死线索。
   - 检查 `log/<Module>/crash_log_<sn>/` 下的 `cppcrash-*`、`appfreeze-*`、SERVICE_BLOCK 文件：读 `Module name`、`Reason`、`LastFatalMessage` 与崩溃栈，并用 `Timestamp` 与 hilog 中用例的 start/timeout 行对齐，确认崩溃进程与失败用例的因果对应。
   - 需要时使用 `scripts/extract_case_hilog.py`。
   - 优先采用日志中的具体证据，而不是根据 XML 汇总猜测。

4. 分类根因。
   - 产品能力不匹配：产品缺少硬件或功能能力，却通过 HDF/配置/model/mock 暴露出来。
   - HDF 或产品配置问题：继承的公共配置启用了本产品应覆盖的驱动模型或功能。
   - 框架/API 行为问题：服务状态、回调、返回码或错误码不符合 API 契约或测试预期。
   - 测试断言容差问题：数值精度、时序或平台相关边界过于严格。
   - 测试环境/夹具问题：资源缺失、安装/推送失败、权限、显示/画面、网络、时间/日期或设备模式。
   - 测试包/工具链不匹配：HAP 内 `.abc` 字节码版本、SDK/分支工具链或签名与目标镜像不一致。
     典型日志：`Unable to open file '<hash>' with abc file version X.Y.Z.W. Maximum supported abc file version on
     the current system image is A.B.C.D`（栈落在 `panda_file::CheckFileVersion` / `JsAbilityStage::LoadModule`），
     进程 SIGABRT（appspawn `exit with signal:6`），依赖该 HAP 的用例集体 20s 超时。
     排查手法：解包 HAP 读 `PandaFile` 头（magic `PANDA\0\0\0` + u32 checksum + 4 字节 version，如 `24.0.0.0`），
     与本仓 `build/version.gni` 的 `sdk_version`、构建产物
     `out/*/gen/arkcompiler/runtime_core/libpandafile/include/file_format_version.h` 的 `version {…}`，
     以及报告 `result/*.xml` 的 `devices` 段/崩溃日志中的镜像版本逐一对齐；再对比同一套件在其它镜像版本报告中的表现。
   - 测试代码挂起：被拒绝的 promise、错误分支或回调路径未调用 `done()`，导致超时与阻塞连带。
   - 真实崩溃/卡死：存在 cppcrash、appfreeze、SERVICE_BLOCK 或进程重启证据。

5. 定稿或修改前核查上游修复。
   - 对 ACTS，检查 `https://gitcode.com/openharmony/xts_acts`。
   - 对 HATS，检查 `https://gitcode.com/openharmony/xts_hats`。
   - 对 DCTS，检查 `https://gitcode.com/openharmony/xts_dcts`。
   - 对产品、框架、服务、驱动或公共源码修复，把本地源码路径映射到对应的 `https://gitcode.com/openharmony/<repo>` 仓库，在做出或定稿本地修改前，检查同一发布分支上是否有类似补丁。
   - 优先用户指定的目标分支；未指定时同时检查 `OpenHarmony-6.1-Release` 与 `OpenHarmony-6.1-LTS`。
   - 提出纯本地修复前，对比精确的用例源码与附近辅助代码。
   - 若上游已修改用例、辅助代码、框架、产品或服务源码，报告上游分支、commit 哈希、文件路径与最小回移 diff。

6. 按优先级提出修复。
   - 先给结论：本问题是**产品/框架侧**还是**测试侧**。
   - 产品错了时，优先持久的产品或框架修复。
   - 硬件能力缺失时，优先产品功能/HDF 覆盖。
   - 测试侧（用例/测试包/测试环境）问题按硬性规则 1 处理：**不修改用例源码、不放宽断言、不跳过用例**，
     改为产出豁免证据并给出证据链；若可通过**不改用例**的环境修复绕开（例如换用与镜像匹配的测试包、
     对齐 SDK/工具链版本、恢复匹配的镜像后重跑），先报告该选项供用户决策，再决定是否豁免。
   - 只要改了任何用例源码，先单独编译该用例包或最小的所属测试模块，再视为本地验证通过。
   - 对 OpenHarmony 标准系统 ACTS/HATS/DCTS 用例包，首次构建或目标发现优先使用套件包装脚本；`out/<product>/build.ninja` 已存在时，重复的 ETS-only 重建用根目录 `build.sh --fast-rebuild --build-target <suite-path>:<target>`。
   - 重建外部运行器产物后，把 ACTS 产物同步到 `/home/cx/os/acts/new_acts_testcase/`、HATS 产物同步到 `/home/cx/os/hats/new_hats_testcase/` 作为用户的导出副本；本地重跑时也更新运行器的 `testcases/` 目录。
   - 报告精确的单模块编译命令、是否走完整套件包装或 fast-rebuild 路径、是否通过，以及可用时的输出包/产物路径。

7. 仅当存在产品/框架侧修复（或用户明确接受的本地 workaround）时，才按本地 MR 惯例提交代码修复；
   判定为测试侧的问题不提交用例改动，而是产出豁免证据（硬性规则 1）。
   - 除非用户明确要求合并 MR，否则每个测试模块或用例工程单独一个分支、commit 与 MR。
   - 这些 ACTS/XTS 修复 MR 不要使用通用 agent 分支前缀，使用本地惯例，例如 `v1.1.x/v6.1.0.31_<TestModule>_测试项Failed`。
   - 创建 MR 时显式指派给陈鑫：`--assignee cx`。
   - 创建 MR 时显式开启合并后删除源分支：`--remove-source-branch=true`。
   - MR 目标分支与用户当前基础分支保持一致，例如目标活跃时用 `v1.0.15/v6.1.0.31_modem`。
   - 若 `glab-mr-submit` 可用，使用它并传入等价的指派人与目标分支、删除源分支选项；不可用时用 `glab mr create` 显式选项，不要依赖 GitLab 默认值。
   - 创建每个 MR 前，验证分支 diff 只包含对应用例工程的文件。

8. 按以下结构汇报每个问题：
   - 模块
   - 失败/阻塞数量
   - 失败用例
   - 用例在测什么
   - 测试资源/输入数据，如 URL、文件路径、设备能力、权限或系统设置
   - 测试流程：setup、API 调用、等待的回调/事件、断言、清理
   - 证据：XML/模块日志/hilog/源码引用
   - 上游核查结果，含可用时的分支/commit/路径
   - 根因
   - 产品侧 / 测试侧判定（测试侧需给出豁免证据方案与覆盖用例数）
   - 推荐修复
   - 需要修改的文件
   - 用例编译命令与结果（改了用例源码时必填）
   - 验证或重跑建议
   - 残余风险

## 参考资料

- 已知模式与示例：阅读 `references/failure_patterns.md`。
- HDF/产品功能覆盖模式：阅读 `references/hdf_feature_overrides.md`。
- 报告布局与搜索策略：阅读 `references/report_layouts.md`。
- 上游仓库核查：阅读 `references/upstream_checks.md`。
- 用例单模块构建与 fast-rebuild 命令：阅读 `references/fast_rebuild.md`。
- 输出格式：阅读 `references/output_template.md`。
