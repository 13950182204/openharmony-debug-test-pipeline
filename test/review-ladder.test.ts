import { describe, expect, it } from 'vitest'
import {
  CATEGORY_DOWNGRADE_STEPS,
  DEFAULT_LADDER,
  agentOptionsForTier,
  parentTierIndex,
  resolveLadder,
  tierIndexForCategory,
  type LadderTier,
} from '../src/review-ladder.ts'

describe('parentTierIndex（父代理档位探测）', () => {
  it('精确匹配档位表 model', () => {
    expect(parentTierIndex({ model: 'deepseek-v4-pro' })).toBe(0)
    expect(parentTierIndex({ model: 'deepseek-v4-flash' })).toBe(1)
  })

  it('未知模型按含 pro 启发式归档，其余归最低档', () => {
    expect(parentTierIndex({ model: 'deepseek-v4-pro-max' })).toBe(0)
    expect(parentTierIndex({ model: 'deepseek-v4-flash-high' })).toBe(1)
    expect(parentTierIndex({ model: 'some-other-model' })).toBe(1)
  })

  it('未提供模型时保守归最低档', () => {
    expect(parentTierIndex(undefined)).toBe(1)
    expect(parentTierIndex({})).toBe(1)
  })
})

describe('tierIndexForCategory（按类别自动分级）', () => {
  it('父档=最强档（pro）时：复杂类不降级，maintainability 降一档', () => {
    expect(tierIndexForCategory('security', 0)).toBe(0)
    expect(tierIndexForCategory('bug', 0)).toBe(0)
    expect(tierIndexForCategory('race', 0)).toBe(0)
    expect(tierIndexForCategory('code-quality', 0)).toBe(0)
    expect(tierIndexForCategory('test-stability', 0)).toBe(0)
    expect(tierIndexForCategory('maintainability', 0)).toBe(1)
  })

  it('父档=最低档（flash）时：全部不降级（下限保护）', () => {
    for (const category of Object.keys(CATEGORY_DOWNGRADE_STEPS) as (keyof typeof CATEGORY_DOWNGRADE_STEPS)[]) {
      expect(tierIndexForCategory(category, 1)).toBe(1)
    }
  })

  it('三档扩展：父中档时 maintainability 可降到最低档', () => {
    const ladder: LadderTier[] = [
      { name: 'high', model: 'model-x' },
      { name: 'mid', model: 'model-y' },
      { name: 'low', model: 'model-z' },
    ]
    expect(tierIndexForCategory('maintainability', 1, ladder)).toBe(2)
    expect(tierIndexForCategory('security', 1, ladder)).toBe(1)
  })
})

describe('resolveLadder / agentOptionsForTier', () => {
  it('缺省或空配置回退默认两档', () => {
    expect(resolveLadder(undefined)).toEqual(DEFAULT_LADDER)
    expect(resolveLadder([])).toEqual(DEFAULT_LADDER)
    expect(resolveLadder([{ name: 'a', model: 'm1' }, { name: 'a', model: 'm2' }])).toEqual(DEFAULT_LADDER)
  })

  it('合法自定义档位表生效', () => {
    const ladder: LadderTier[] = [
      { name: 'high', provider: 'p1', model: 'm1' },
      { name: 'low', provider: 'p1', model: 'm2', maxTokens: 4096 },
    ]
    expect(resolveLadder(ladder)).toEqual(ladder)
  })

  it('档位 AgentOptions 转换：未配置字段省略（走继承）', () => {
    expect(agentOptionsForTier(undefined)).toBeUndefined()
    expect(agentOptionsForTier({ name: 'low', model: 'deepseek-v4-flash' })).toEqual({ model: 'deepseek-v4-flash' })
    expect(agentOptionsForTier(DEFAULT_LADDER[0])).toEqual({ provider: 'deepseek-official', model: 'deepseek-v4-pro' })
  })
})
