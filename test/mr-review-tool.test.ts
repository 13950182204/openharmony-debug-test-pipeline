import { describe, expect, it, vi } from 'vitest'
import type { Config } from '../src/config.ts'
import { registerMrReviewTool } from '../src/mr-review-tool.ts'

/**
 * mr_review_six 的接线回归。
 *
 * 针对一次真实故障：工具把**运行名**（`mr-review-<类别>`）当作子代理调用的 provider 参数，
 * 而该参数是 **provider 名**（`SubagentRuntime` 按它查表），真实环境因此一律
 * `no subagent provider registered for "mr-review-security"` 失败。
 * 此前没有任何测试覆盖该调用，所以编译通过与其余单测全绿都发现不了。
 *
 * DSH 0.2.1-rc.1 起 API 由 `start(name, request)` 改为 `startActivation(spec)`：
 * provider / label / request / signal / delivery 五个顶层字段，request 内不含 label 与 signal。
 */

interface Call {
  provider: string
  label: string
  request: Record<string, unknown>
  signal: unknown
  delivery: string
}

/** 采集 subagents.startActivation 入参的工具 harness，返回立刻完成的假 activation。 */
function harness() {
  const calls: Call[] = []
  const register = vi.fn()
  const ctx = {
    subagents: {
      startActivation: async (spec: Record<string, any>) => {
        calls.push({
          provider: spec.provider,
          label: spec.label,
          request: spec.request,
          signal: spec.signal,
          delivery: spec.delivery,
        })
        return {
          childId: 'child-1',
          result: Promise.resolve({
            output: [{ type: 'text', text: 'no findings' }],
            stopReason: 'completed',
          }),
          dispose: async () => {},
        }
      },
    },
    tools: { register },
    logger: { info: () => {}, warn: () => {} },
  } as any
  return { ctx, calls, register }
}

/** 注册工具并执行一次单类别审查，返回捕获到的 startActivation 调用。 */
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

  it('请求里带上父代理与提示，signal 与 label 走 spec 顶层（0.2.1 的 Omit 约束）', async () => {
    const calls = await runOnce({})
    expect(calls[0].request.parent).toBeTruthy()
    expect(calls[0].request.prompt).toBeTruthy()
    // 0.2.1-rc.1：request 的类型是 Omit<SubagentStartRequest, 'label' | 'signal'>，
    // 这两项若塞进 request 会被类型拒绝（运行时也可能被忽略）。
    expect(calls[0].request.signal).toBeUndefined()
    expect(calls[0].request.label).toBeUndefined()
    expect(calls[0].signal).toBeInstanceOf(AbortSignal)
    expect(calls[0].label).toBe('mr-review:security')
    // 只把结果返回给调用方，不额外通知模型
    expect(calls[0].delivery).toBe('caller')
  })
})
