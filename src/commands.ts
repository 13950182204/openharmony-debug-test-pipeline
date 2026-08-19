import type { Context } from '@deepseek-ai/cordis'
import type { CommandInvocation, CommandResult } from '@deepseek-ai/dsh-commands'
import { loadState, renderState, resetState } from './state.ts'

/**
 * /pipeline 命令：查询与重置闭环流水线状态。
 *   /pipeline status  - 显示当前阶段、产物与最近事件
 *   /pipeline reset   - 重置状态（需在输入中明确写 reset）
 */
export function registerPipelineCommands(ctx: Context, stateFile: string): void {
  ctx.commands.register({
    name: 'pipeline',
    description: 'OpenHarmony 调测闭环流水线：status 查询状态，reset 重置状态',
    input: { hint: 'status | reset' },
    handler: (invocation: CommandInvocation): CommandResult => {
      const args = invocation.rawInput.trim().split(/\s+/).filter(Boolean)
      const sub = args[0] ?? 'status'
      if (sub === 'status') {
        return { kind: 'success', text: renderState(loadState(stateFile)) }
      }
      if (sub === 'reset') {
        resetState(stateFile)
        return { kind: 'success', text: '流水线状态已重置。' }
      }
      return { kind: 'error', text: `未知子命令 ${sub}，支持: status | reset` }
    },
  })
  ctx.logger.info(`openharmony-debug-test-pipeline: 已注册 /pipeline 命令（状态文件: ${stateFile}）`)
}
