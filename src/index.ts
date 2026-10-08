import type { Context } from '@deepseek-ai/cordis'
import { Config } from './config.ts'
import { registerVendoredSkills } from './vendor.ts'
import { registerLoopSkill } from './loop-skill.ts'
import { registerPipelineCommands } from './commands.ts'
import { registerMrReviewTool } from './mr-review-tool.ts'
import { packageRoot } from './vendor.ts'
import { isWindowsHost, resolveModelPaths } from './wsl-paths.ts'

/**
 * OpenHarmony 修改-调试-测试闭环 DSH 插件。
 *
 * 运行时插件（bundle 聚合包形态，无 client 半身）：
 * - 注册五个 vendored 模块 skill（glab-mr-submit / karpathy-guidelines /
 *   openharmony-ci-orchestrator / openharmony-ota-upgrade /
 *   openharmony-test-report-triage），每个均可独立调用；
 * - 注册一个闭环编排 skill（openharmony-debug-loop），八阶段状态机串联各模块；
 * - 注册 /pipeline status|reset 命令，跨会话查询/重置流水线状态；
 * - 注册 mr_review_six 工具：六维 MR 审查一键执行，子代理模型按类别自动分级
 *   （复杂类不降级、maintainability 降一档、下限为档位表最低档）。
 *
 * 宿主可以是 Windows（DSH Desktop）或 WSL/Linux：插件本身只注册 skill/命令/工具，
 * 真正执行命令的是会话的 bash 工具，因此 skill 正文里的路径按「模型口径」渲染
 * （见 wsl-paths.ts），Windows 宿主下由 config.wslPackageRoot 指定。
 */
export const name = 'openharmony-debug-test-pipeline'
export const inject = ['skills', 'commands', 'tools', 'subagents']
export { Config }

export function apply(ctx: Context, config: Config = {}): void {
  const resolved = {
    stateFile: config.stateFile ?? '~/.dsh/pipeline-state.json',
    wslPackageRoot: config.wslPackageRoot,
    stateFileModel: config.stateFileModel,
  }
  registerVendoredSkills(ctx, resolved)
  registerLoopSkill(ctx, config)
  const modelPaths = resolveModelPaths(packageRoot(), resolved)
  registerPipelineCommands(ctx, modelPaths.stateFile)
  registerMrReviewTool(ctx, config)
  if (isWindowsHost() && config.wslPackageRoot === undefined) {
    ctx.logger.warn(
      'openharmony-debug-test-pipeline: 检测到 Windows 宿主但未配置 config.wslPackageRoot——'
      + 'skill 正文里的插件路径将是 Windows 路径，模型在 WSL 里用不了；'
      + '请在 profile 的 cordis.patch.yml 里补 wslPackageRoot，以及 stateFile（宿主读，//wsl.localhost/...）'
      + '与 stateFileModel（模型执行，/home/cx/...）。',
    )
  }
  ctx.logger.info(
    `openharmony-debug-test-pipeline: OpenHarmony 调测闭环流水线插件加载完成`
    + `（宿主=${process.platform}，状态文件=${modelPaths.stateFile}）`,
  )
}
