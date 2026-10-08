import { describe, expect, it, vi } from 'vitest'
import type { Config } from '../src/config.ts'
import { registerMrReviewTool } from '../src/mr-review-tool.ts'

/**
 * mr_review_six 的接线回归。
 *
 * 针对一次真实故障：工具把**运行名**（`mr-review-<类别>`）当作 `ctx.subagents.start()`
 * 的第一个参数，而该参数是 **provider 名**（`SubagentRuntime.expectProvider()` 按它查表），
 * 真实环境因此一律 `no subagent provider registered for "mr-review-security"` 失败。
 * 此前没有任何测试覆盖该调用，所以编译通过与其余单测全绿都发现不了。
 */

interface Call { provider: string; request: Record<string, unknown> }

/** 采集 subagents.start 入参的工具 harness，返回一个立刻完成的假 run。 */
function harness() {
  const calls: Call[] = []
  const register = vi.fn()
  const ctx = {
    subagents: {
      start: async (provider: string, request: Record<string, unknown>) => {
        calls.push({ provider, request })
        return {
          result: Promise.resolve({
            output: [{ type: 'text', text: 'no findings' }],
            stopReason: 'completed',
          }),
        }
      },
    },
    tools: { register },
    logger: { info: () => {}, warn: () => {} },
  } as any
  return { ctx, calls, register }
}

/** 注册工具并执行一次单类别审查，返回捕获到的 start 调用。 */
async function runOnce(config: Config, categories = ['security']): Promise<Call[]> {
  const { ctx, calls, register } = harness()
  registerMrReviewTool(ctx, config)
  const tool = register.mock.calls[0]?.[0]
  expect(tool, 'mr_review_six 未注册').toBeTruthy()
  const parent = { options: { provider: 'deepseek-official', model: 'deepseek-v4-flash' } }
  await tool.execute(
    { repoDir: '/repo', baseSha: 'a'.repeat(40), headSha: 'b'.repeat(40), categories },
    { agent: parent, signal: new AbortController().signal },
  )
  return calls
}

describe('mr_review_six 的 subagent provider 接线', () => {
  it('第一个参数是 provider 名（缺省 fork），不是运行名', async () => {
    const calls = await runOnce({})
    expect(calls).toHaveLength(1)
    expect(calls[0].provider).toBe('fork')
    // 关键回归：绝不能把运行标签当 provider 名
    expect(calls[0].provider).not.toMatch(/^mr-review-/)
  })

  it('config.subagentProvider 可覆盖 provider 名', async () => {
    const calls = await runOnce({ subagentProvider: 'spawn' })
    expect(calls[0].provider).toBe('spawn')
  })

  it('每个类别各起一个子代理，仍共用同一 provider 名', async () => {
    const calls = await runOnce({}, ['security', 'bug'])
    expect(calls).toHaveLength(2)
    expect(calls.map(call => call.provider)).toEqual(['fork', 'fork'])
  })

  it('请求里带上父代理与取消信号（子代理只读审查的前提）', async () => {
    const calls = await runOnce({})
    expect(calls[0].request.parent).toBeTruthy()
    expect(calls[0].request.signal).toBeInstanceOf(AbortSignal)
    expect(calls[0].request.prompt).toBeTruthy()
  })
})
