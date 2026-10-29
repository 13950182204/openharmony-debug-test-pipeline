import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  loadDemoState,
  registerDemoCommand,
  renderDemoState,
  resetDemoState,
} from '../src/demo-command.ts'

describe('renderDemoState', () => {
  it('未开始时给出开始命令', () => {
    const text = renderDemoState(null)
    expect(text).toContain('（尚未开始）')
    expect(text).toContain('democtl.py start --case mr260 --lane a')
  })

  it('渲染进度、产物来源图例与最近事件', () => {
    const text = renderDemoState({
      case: 'mr260',
      lane: 'a',
      stage: 'ci',
      updatedAt: '2026-10-29T08:00:00Z',
      stages: {
        report: { status: 'done', provenance: 'live', notes: ['648 例/4 失败'] },
        fix: { status: 'done', provenance: 'recorded', degraded: true },
        ci: { status: 'running', provenance: 'local-staged' },
      },
      events: [{ stage: 'ci', note: '本地出包契约替代' }],
    })
    expect(text).toContain('case=mr260 lane=a 进度=2/8')
    expect(text).toContain('当前阶段: ci')
    expect(text).toContain('report     ● done  648 例/4 失败')
    expect(text).toContain('fix        ○ done !')
    expect(text).toContain('ci         ◐ running')
    expect(text).toContain('图例: ● live  ◐ local-staged  ○ recorded')
    expect(text).toContain('[ci] 本地出包契约替代')
  })

  it('整体 degraded 带警示', () => {
    expect(renderDemoState({ degraded: true, stages: {} })).toContain('⚠ DEGRADED')
  })
})

describe('演示状态文件读写（只碰 demo 状态，不碰真实流水线状态）', () => {
  let dir = ''
  let file = ''

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'demo-command-'))
    file = join(dir, 'state.json')
  })

  it('缺失返回 null，损坏返回 null', () => {
    expect(loadDemoState(file)).toBeNull()
    writeFileSync(file, '{not json', 'utf8')
    expect(loadDemoState(file)).toBeNull()
    rmSync(dir, { recursive: true, force: true })
  })

  it('reset 只删演示状态文件', () => {
    writeFileSync(file, JSON.stringify({ case: 'mr260' }), 'utf8')
    const text = resetDemoState(file)
    expect(text).toContain('已清空')
    expect(loadDemoState(file)).toBeNull()
    rmSync(dir, { recursive: true, force: true })
  })
})

describe('registerDemoCommand', () => {
  it('注册 /demo 并分发 status/reset/未知子命令', () => {
    const dir = mkdtempSync(join(tmpdir(), 'demo-command-'))
    const file = join(dir, 'state.json')
    writeFileSync(file, JSON.stringify({ case: 'mr260', lane: 'a', stages: {} }), 'utf8')

    const register = vi.fn()
    registerDemoCommand({
      commands: { register },
      logger: { info: vi.fn() },
    } as any, file)

    expect(register).toHaveBeenCalledTimes(1)
    const def = register.mock.calls[0]![0]

    const status = def.handler({ rawInput: '' })
    expect(status.kind).toBe('success')
    expect(status.text).toContain('case=mr260')

    const unknown = def.handler({ rawInput: 'bogus' })
    expect(unknown.kind).toBe('error')

    const reset = def.handler({ rawInput: 'reset' })
    expect(reset.kind).toBe('success')
    expect(() => readFileSync(file, 'utf8')).toThrow()

    rmSync(dir, { recursive: true, force: true })
  })
})
