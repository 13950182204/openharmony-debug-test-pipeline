import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import type { Config } from './config.ts'
import { registerVendoredSkills, packageRoot } from './vendor.ts'
import { registerIssueCommands } from './commands.ts'

/**
 * openharmony-issue-pipeline:GitLab Issue 驱动看板流水线 DSH 插件。
 *
 * 运行时插件(bundle 聚合包形态,无 client 半身),父插件
 * openharmony-debug-test-pipeline 的子插件:
 * - 注册 vendored skill openharmony-issue-pipeline:定期抓取 GitLab 项目
 *   Assignee 相关待解决 issue → 按标签映射工作区/分支 → 按 Due date 分级
 *   模型档位(含图片强制视觉模型)→ 八阶段闭环(第一步分析 issue Description)
 *   → 每阶段同步任务看板卡片与 issue 评论;
 * - 注册 /issue status 命令,只读输出看板任务 vs issue 清单对照;
 * - 配套 scripts/issue_pipeline.py(bootstrap/sync/run/stage/close/status)。
 */
export const name = 'openharmony-issue-pipeline'
export const inject = ['skills', 'commands']
export type { Config }

export function apply(ctx: Context, config: Config = {}): void {
  const root = packageRoot()
  const parentPluginDir = config.parentPluginDir ?? resolve(dirname(root), '..')
  const scriptPath = resolve(root, 'scripts', 'issue_pipeline.py')
  registerVendoredSkills(ctx, { parentPluginDir }, root)
  registerIssueCommands(ctx, scriptPath)
  ctx.logger.info(
    `openharmony-issue-pipeline: GitLab Issue 驱动看板流水线插件加载完成（父插件目录: ${parentPluginDir}）`,
  )
}
