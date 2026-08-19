import type { Context } from '@deepseek-ai/cordis'
import { Config } from './config.ts'
import { registerVendoredSkills } from './vendor.ts'
import { registerLoopSkill } from './loop-skill.ts'
import { registerPipelineCommands } from './commands.ts'

/**
 * OpenHarmony 修改-调试-测试闭环 DSH 插件。
 *
 * 运行时插件（bundle 聚合包形态，无 client 半身）：
 * - 注册五个 vendored 模块 skill（glab-mr-submit / karpathy-guidelines /
 *   openharmony-ci-orchestrator / openharmony-ota-upgrade /
 *   openharmony-test-report-triage），每个均可独立调用；
 * - 注册一个闭环编排 skill（openharmony-debug-loop），八阶段状态机串联各模块；
 * - 注册 /pipeline status|reset 命令，跨会话查询/重置流水线状态。
 */
export const name = 'openharmony-debug-test-pipeline'
export const inject = ['skills', 'commands']
export { Config }

export function apply(ctx: Context, config: Config = {}): void {
  const resolved = { stateFile: config.stateFile ?? '~/.dsh/pipeline-state.json' }
  registerVendoredSkills(ctx, resolved)
  registerLoopSkill(ctx, resolved)
  registerPipelineCommands(ctx, resolved.stateFile)
  ctx.logger.info('openharmony-debug-test-pipeline: OpenHarmony 调测闭环流水线插件加载完成')
}
