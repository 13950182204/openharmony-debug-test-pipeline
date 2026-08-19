import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'

/**
 * 流水线状态：跨会话持久化闭环进度（JSON 文件）。
 * - 写入为原子写（临时文件 + rename），避免并发读半截文件；
 * - 文件损坏时备份为 .bak-<时间戳> 后重建空状态，不崩溃。
 */

export interface PipelineEvent {
  at: string
  stage: string
  note: string
}

export interface PipelineState {
  schemaVersion: number
  updatedAt: string
  /** 当前阶段：report | triage | fix | mr | ci | ota | regression | done | null */
  stage: string | null
  /** 阶段产物：报告路径、MR IID、构建号、设备 serial、OTA 结果等 */
  artifacts: Record<string, string | number | null>
  events: PipelineEvent[]
  /** 每阶段结束的 token 用量快照（来自 DSH session_projcache） */
  tokenSnapshots?: TokenSnapshot[]
}

/** 一次 token 用量快照（与 scripts/pipeline_state.py 的 tokens 子命令对齐） */
export interface TokenSnapshot {
  at: string
  stage: string
  sessionId: string
  uncachedInputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
}

export const SCHEMA_VERSION = 1

export function emptyState(): PipelineState {
  return {
    schemaVersion: SCHEMA_VERSION,
    updatedAt: '',
    stage: null,
    artifacts: {},
    events: [],
  }
}

/** 展开 ~ 为用户主目录并解析为绝对路径 */
export function expandStatePath(path: string): string {
  // 注意不能用 resolve(homedir(), path.slice(1))：slice 后以 / 开头会被 resolve 当作绝对路径丢弃 home
  if (path.startsWith('~')) return join(homedir(), path.slice(1))
  return resolve(path)
}

/** 读取状态文件；不存在返回空状态，损坏则备份后重建 */
export function loadState(path: string): PipelineState {
  const file = expandStatePath(path)
  if (!existsSync(file)) return emptyState()
  let raw: string
  try {
    raw = readFileSync(file, 'utf8')
  } catch {
    return emptyState()
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    backupCorrupt(file)
    return emptyState()
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    backupCorrupt(file)
    return emptyState()
  }
  const value = parsed as Partial<PipelineState>
  if (value.schemaVersion !== SCHEMA_VERSION) {
    backupCorrupt(file)
    return emptyState()
  }
  return {
    ...emptyState(),
    ...value,
    artifacts: value.artifacts && typeof value.artifacts === 'object' ? value.artifacts : {},
    events: Array.isArray(value.events) ? value.events : [],
  }
}

function backupCorrupt(file: string): void {
  try {
    renameSync(file, `${file}.bak-${Date.now()}`)
  } catch {
    // 备份失败不阻断加载
  }
}

/** 原子写状态文件（先写临时文件再 rename） */
export function saveState(path: string, state: PipelineState): void {
  const file = expandStatePath(path)
  mkdirSync(dirname(file), { recursive: true })
  const tmp = `${file}.tmp`
  writeFileSync(tmp, JSON.stringify({ ...state, updatedAt: new Date().toISOString() }, null, 2) + '\n', 'utf8')
  renameSync(tmp, file)
}

/** 重置状态并落盘 */
export function resetState(path: string): PipelineState {
  const state = emptyState()
  saveState(path, state)
  return state
}

/** 追加一条流水线事件（不落盘，调用方负责 saveState） */
export function pushEvent(state: PipelineState, stage: string, note: string): void {
  state.events.push({ at: new Date().toISOString(), stage, note })
}

/** 渲染状态为人类可读文本（供 /pipeline status 与闭环汇报使用） */
export function renderState(state: PipelineState): string {
  const lines: string[] = []
  lines.push(`流水线阶段: ${state.stage ?? '（未开始）'}`)
  const artifactKeys = Object.keys(state.artifacts)
  if (artifactKeys.length === 0) {
    lines.push('产物: （无）')
  } else {
    lines.push('产物:')
    for (const key of artifactKeys) {
      lines.push(`  ${key}: ${state.artifacts[key] ?? ''}`)
    }
  }
  const recent = state.events.slice(-8)
  if (recent.length === 0) {
    lines.push('事件: （无）')
  } else {
    lines.push('最近事件:')
    for (const event of recent) {
      lines.push(`  [${event.stage}] ${event.note}`)
    }
  }
  const snapshots = state.tokenSnapshots ?? []
  if (snapshots.length > 0) {
    lines.push('token 快照（最近 3 次）:')
    for (const snap of snapshots.slice(-3)) {
      lines.push(`  [${snap.stage}] in=${snap.uncachedInputTokens} out=${snap.outputTokens} cacheRead=${snap.cacheReadTokens}`)
    }
  }
  return lines.join('\n')
}
