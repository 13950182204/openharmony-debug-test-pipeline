import type { Context } from '@deepseek-ai/cordis'
import type { SkillRegistration } from '@deepseek-ai/dsh-skill'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Config } from './config.ts'

/**
 * 闭环编排 skill：把五个模块 skill 串成「修改-调试-测试」八阶段状态机。
 * 任意阶段可单独进入（模块化）；阶段产物通过流水线状态文件传递。
 */
export const LOOP_SKILL_NAME = 'openharmony-debug-loop'

function packageRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), '..')
}

export function buildLoopSkill(config: Config): SkillRegistration {
  const root = packageRoot()
  const stateFile = config.stateFile
  const scriptPath = join(root, 'scripts', 'pipeline_state.py')

  const content = `# OpenHarmony 调测闭环流水线

当用户要求「跑一遍完整闭环 / 修改-调试-测试闭环 / 从测试报告到回归验证」时使用本 skill。
目标：把一份测试报告或一个问题，走完 分诊 → 修复 → MR → CI → OTA → 回归 的完整闭环，并留下可审计的流水线记录。

## 模块化原则

- 每个阶段独立加载对应模块 skill（用 skill 工具按名加载），阶段之间只通过流水线状态文件传递产物；
- 用户只要求其中一步时，只执行对应阶段，不要展开整个闭环（例如「提交这个 MR」只走 mr 阶段）；
- 真实动作（触发 Jenkins、创建 MR、OTA 升级）必须经过用户明确授权；dry-run / 只读检查永远先行。

## 流水线状态

- 状态文件: ${stateFile}（可用 /pipeline status 命令查询）
- 状态脚本: ${scriptPath}
- 运行日志: ~/.dsh/pipeline-runs/<日期>-<报告名>.md（每个阶段结束后把该阶段细节追加写入：报告路径、失败表、证据文件与关键日志行、根因分析、修复决策；供后续优化插件复盘）
- 开始闭环前重置: python3 ${scriptPath} reset --file ${stateFile}
- 每阶段结束写状态: python3 ${scriptPath} set <stage> '{json 产物}' --file ${stateFile}
- 每阶段结束记 token 用量: python3 ${scriptPath} tokens <stage> --file ${stateFile}
- 阶段内关键事件: python3 ${scriptPath} note <stage> '<说明>' --file ${stateFile}

## 八阶段状态机

阶段顺序: report → triage → fix → mr → ci → ota → regression → done

1. **report（测试报告输入）**: 确认测试报告目录 / 问题描述，必要时询问缺失信息。
   记录产物 reportDir。

2. **triage（测试分诊）**: 加载 skill \`openharmony-test-report-triage\`，按其流程建立失败表、
   定位测试源码、提取 hilog 证据、分类根因（产品能力/HDF 配置/框架行为/断言容差/环境/挂起/崩溃）、
   核查上游（gitcode.com/openharmony）是否已有修复，给出修复优先级建议。
   记录产物 triageSummary（根因分类与涉及模块）。

3. **fix（按 Karpathy 规范修复）**: 加载 skill \`karpathy-guidelines\`，遵守：
   思考先行（先说假设与方案再动手）、简单优先（最小代码）、外科手术式改动（只改必须改的）、
   目标驱动（每处修复都有可验证的判据）。修复后先跑最小验证（编译 / 单测 / 对应测试模块）。
   记录产物 fixSummary 与涉及文件列表。

4. **mr（MR 提交与六维审查）**: 加载 skill \`glab-mr-submit\`，用其
   \`create_glab_mr.py\` 生成合规标题（[动作] [芯片] [XTS] 说明）、标签与标准记录，
   先 --dry-run 审查再执行；创建后按六维（安全/质量/缺陷/竞态/测试稳定性/可维护性）
   并行 subagent 审查，确认的 P0-P2 自动修复并在原 MR 上追加提交，再复审一次。
   记录产物 mrIid、mrUrl、sourceBranch。

5. **ci（Jenkins CI 触发）**: 加载 skill \`openharmony-ci-orchestrator\`，先
   --dry-run --verify-job 校验作业与参数，用户授权后真实触发；
   用 ci_orchestrator.py register 登记，构建完成由 webhook / 定时 reconcile
   交接一个 dsh headless 会话（交接命令已由插件定制为 dsh --profile headless）。
   记录产物 jenkinsJob、jenkinsBuildNumber、jenkinsUrl。

6. **ota（OTA 升级到设备）**: 构建产物可用后加载 skill \`openharmony-ota-upgrade\`，
   执行 ota_preflight.sh 包预检 → hdc list targets 确认唯一设备 → 设备预检
   （版本 / /data 容量 / write_updater）→ 传输并比对大小 → write_updater updater →
   reboot updater → 等待回连 → 验证 bootevent.boot.completed 与目标版本。
   记录产物 otaPackage、otaSerial、otaResult。

7. **regression（回归验证）**: 在升级后的设备上重新执行 triage 阶段失败对应的测试集
   （或由 CI 编排的 phase-3 回归 profile 驱动），把结果与失败表逐条对比。
   记录产物 regressionResult（通过数 / 失败数 / 残留项）。

8. **done / 回到 fix**: 回归通过 → 阶段 done，汇总闭环记录（MR 链接、构建号、
   OTA 摘要、回归结论）向用户报告；回归失败 → 回到 fix 阶段，按 Karpathy 规范继续修复，
   走 glab-mr-submit 的 same-MR 修复流程在原 MR 上追加提交，重新触发 ci → ota → regression。

## 纪律

- 状态文件是唯一事实来源：每个阶段结束必须写状态（set）+ 记 token（tokens），否则视为该阶段未完成；
- 每个阶段结束把流程细节追加进运行日志（~/.dsh/pipeline-runs/<日期>-<报告名>.md），包括证据文件路径与关键日志行；
- 每完成一个阶段向用户汇报进度与下一步，等用户确认后再继续；
- 设备操作前必须 hdc list targets 确认唯一目标，禁止对未确认设备操作；
- 不要把多个阶段塞进一次回复连续自嗨式执行——真实动作一律等待授权。
`

  return {
    name: LOOP_SKILL_NAME,
    description: 'OpenHarmony 修改-调试-测试闭环：分诊→修复→MR→CI→OTA→回归 八阶段状态机，任意阶段可单独进入',
    whenToUse: '用户要求跑完整闭环、修改-调试-测试流程、或从测试报告一路走到回归验证',
    content,
    source: 'bundled',
  }
}

export function registerLoopSkill(ctx: Context, config: Config): void {
  ctx.skills.register(buildLoopSkill(config))
  ctx.logger.info(`openharmony-debug-test-pipeline: 已注册编排 skill ${LOOP_SKILL_NAME}`)
}
