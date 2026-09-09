import { execFileSync } from 'node:child_process'
import type { Context } from '@deepseek-ai/cordis'
import type { CommandInvocation, CommandResult } from '@deepseek-ai/dsh-commands'

/**
 * /issue 命令:查询 GitLab issue 流水线状态(只读)。
 *   /issue status - 输出看板任务 vs issue 清单对照(调用 issue_pipeline.py status)
 */
export function registerIssueCommands(ctx: Context, scriptPath: string): void {
  ctx.commands.register({
    name: 'issue',
    description: 'GitLab Issue 流水线:status 查询看板与 issue 对照',
    input: { hint: 'status' },
    handler: (invocation: CommandInvocation): CommandResult => {
      const args = invocation.rawInput.trim().split(/\s+/).filter(Boolean)
      const sub = args[0] ?? 'status'
      if (sub === 'status') {
        try {
          const out = execFileSync('python3', [scriptPath, 'status'], {
            encoding: 'utf8',
            timeout: 30000,
            maxBuffer: 4 * 1024 * 1024,
          })
          return { kind: 'success', text: out.trim() }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          return { kind: 'error', text: `issue_pipeline.py status 失败: ${message}` }
        }
      }
      return { kind: 'error', text: `未知子命令 ${sub}，支持: status` }
    },
  })
  ctx.logger.info(`openharmony-issue-pipeline: 已注册 /issue 命令（脚本: ${scriptPath}）`)
}
