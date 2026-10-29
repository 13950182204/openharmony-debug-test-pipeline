import { existsSync, readFileSync, rmSync } from 'node:fs'
import type { Context } from '@deepseek-ai/cordis'
import type { CommandInvocation, CommandResult } from '@deepseek-ai/dsh-commands'
import { expandStatePath } from './state.ts'

/** 演示状态里的单个阶段记录（与 scripts/demo/democtl.py 对齐） */
export interface DemoStageRecord {
  status?: string
  provenance?: string
  startedAt?: string
  finishedAt?: string
  notes?: string[]
  evidence?: string[]
  degraded?: boolean
}

/** 演示模式状态文件（`~/.dsh/demo/state.json`） */
export interface DemoState {
  schemaVersion?: number
  case?: string
  lane?: string
  stage?: string
  degraded?: boolean
  startedAt?: string
  updatedAt?: string
  stages?: Record<string, DemoStageRecord>
  artifacts?: Record<string, unknown>
  events?: Array<{ at?: string; stage?: string; note?: string }>
}

/** 八阶段展示顺序（与 democtl.STAGES 一致） */
const DEMO_STAGES = [
  'report', 'triage', 'fix', 'mr', 'ci', 'ota', 'regression', 'done',
] as const

const PROVENANCE_MARK: Record<string, string> = {
  live: '●',
  'local-staged': '◐',
  recorded: '○',
}

/** 读取演示状态；缺失或损坏返回 null（演示可能还没开始） */
export function loadDemoState(path: string): DemoState | null {
  const file = expandStatePath(path)
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as DemoState
  } catch {
    return null
  }
}

/** 渲染演示状态为人类可读文本（供 /demo status 使用） */
export function renderDemoState(state: DemoState | null): string {
  if (state === null) {
    return [
      '演示模式: （尚未开始）',
      '开始: python3 scripts/demo/democtl.py start --case mr260 --lane a',
    ].join('\n')
  }
  const lines: string[] = []
  const caseId = state.case ?? '?'
  const lane = state.lane ?? 'a'
  const done = DEMO_STAGES.filter((stage) => state.stages?.[stage]?.status === 'done').length
  const degraded = state.degraded === true ? '  ⚠ DEGRADED' : ''
  lines.push(`演示模式: case=${caseId} lane=${lane} 进度=${done}/8${degraded}`)
  lines.push(`当前阶段: ${state.stage ?? '（未开始）'}   更新: ${state.updatedAt ?? '-'}`)
  for (const stage of DEMO_STAGES) {
    const record = state.stages?.[stage]
    if (record === undefined) {
      lines.push(`  ${stage.padEnd(10)} ·   （未开始）`)
      continue
    }
    const mark = PROVENANCE_MARK[record.provenance ?? ''] ?? '?'
    const status = record.status ?? 'running'
    const flag = record.degraded === true ? ' !' : ''
    const note = (record.notes ?? []).slice(-1)[0] ?? ''
    lines.push(`  ${stage.padEnd(10)} ${mark} ${status}${flag}${note ? `  ${note}` : ''}`)
  }
  lines.push('图例: ● live  ◐ local-staged  ○ recorded')
  const events = (state.events ?? []).slice(-5)
  if (events.length > 0) {
    lines.push('最近事件:')
    for (const event of events) {
      lines.push(`  [${event.stage ?? '-'}] ${event.note ?? ''}`)
    }
  }
  return lines.join('\n')
}

/** 清空演示状态；`runs/` 证据目录保留 */
export function resetDemoState(path: string): string {
  const file = expandStatePath(path)
  if (!existsSync(file)) return '演示状态本来就不存在。'
  rmSync(file)
  return '演示状态已清空（runs/ 证据目录保留）。'
}

/**
 * /demo 命令：查询与重置演示模式状态。
 *   /demo status - 显示阶段、产物来源与最近事件
 *   /demo reset  - 清空演示状态（需在输入中明确写 reset）
 */
export function registerDemoCommand(ctx: Context, demoStateFile: string): void {
  ctx.commands.register({
    name: 'demo',
    description: '演示模式：status 查看 8 阶段进度，reset 清空演示状态',
    input: { hint: 'status | reset' },
    handler: (invocation: CommandInvocation): CommandResult => {
      const args = invocation.rawInput.trim().split(/\s+/).filter(Boolean)
      const sub = args[0] ?? 'status'
      if (sub === 'status') {
        return { kind: 'success', text: renderDemoState(loadDemoState(demoStateFile)) }
      }
      if (sub === 'reset') {
        return { kind: 'success', text: resetDemoState(demoStateFile) }
      }
      return { kind: 'error', text: `未知子命令 ${sub}，支持: status | reset` }
    },
  })
  ctx.logger.info(`openharmony-debug-test-pipeline: 已注册 /demo 命令（演示状态: ${demoStateFile}）`)
}
