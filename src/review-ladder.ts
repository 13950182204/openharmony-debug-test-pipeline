import type { AgentOptions } from '@deepseek-ai/dsh-agent'

/**
 * 六维审查子代理模型等级自动调节器。
 *
 * 原则（与用户确认的口径）：
 * - 档位只按模型区分（provider/model/maxTokens），reasoningEffort 是 provider
 *   全局配置，不能按子代理区分；
 * - 默认两档：high = deepseek-v4-pro，low = deepseek-v4-flash（下限）；
 * - 父代理已是下限档时全部子代理不降级（下限保护）；
 * - 简单任务（maintainability）降一档，复杂/中等任务不降级，保证审查质量；
 * - 档位表可配置（Config.reviewLadder），默认内置两档。
 */

export interface LadderTier {
  /** 档位名（high / low，可扩展更多档位） */
  name: string
  provider?: string
  model: string
  maxTokens?: number
}

/** 默认档位表：索引越小越强（0 = 最强） */
export const DEFAULT_LADDER: LadderTier[] = [
  { name: 'high', provider: 'deepseek-official', model: 'deepseek-v4-pro' },
  { name: 'low', provider: 'deepseek-official', model: 'deepseek-v4-flash' },
]

/** 六维审查类别 */
export const REVIEW_CATEGORIES = [
  'security',
  'code-quality',
  'bug',
  'race',
  'test-stability',
  'maintainability',
] as const

export type ReviewCategory = (typeof REVIEW_CATEGORIES)[number]

/**
 * 每类别的降档步数（0 = 不降级，1 = 降一档）。
 * 简单任务（可维护性）允许用低档模型；安全/缺陷/竞态等复杂推理类别不降级。
 */
export const CATEGORY_DOWNGRADE_STEPS: Record<ReviewCategory, number> = {
  security: 0,
  'code-quality': 0,
  bug: 0,
  race: 0,
  'test-stability': 0,
  maintainability: 1,
}

/**
 * 探测父代理所在档位：精确匹配档位表 model；匹配不到时按模型名是否含 "pro"
 * 启发式归入最强档，其余（含未提供模型）归入最低档（保守）。
 * @returns 档位索引（0 = 最强）
 */
export function parentTierIndex(
  options: Pick<AgentOptions, 'provider' | 'model'> | undefined,
  ladder: LadderTier[] = DEFAULT_LADDER,
): number {
  if (ladder.length === 0) throw new Error('reviewLadder 不能为空')
  if (!options?.model) return ladder.length - 1
  const exact = ladder.findIndex((tier) => tier.model === options.model)
  if (exact >= 0) return exact
  return options.model.toLowerCase().includes('pro') ? 0 : ladder.length - 1
}

/**
 * 按审查类别解析子代理档位索引。
 * @param category - 六维类别之一
 * @param parentIndex - 父代理档位索引
 * @param ladder - 档位表
 * @returns 子代理档位索引；父已在下限时不降级（下限保护）
 */
export function tierIndexForCategory(
  category: ReviewCategory,
  parentIndex: number,
  ladder: LadderTier[] = DEFAULT_LADDER,
): number {
  const steps = CATEGORY_DOWNGRADE_STEPS[category] ?? 0
  return Math.min(parentIndex + steps, ladder.length - 1)
}

/** 取某档位的 AgentOptions（provider/model/maxTokens；未配置字段省略以走继承） */
export function agentOptionsForTier(tier: LadderTier | undefined): AgentOptions | undefined {
  if (!tier) return undefined
  return {
    ...(tier.provider ? { provider: tier.provider } : {}),
    ...(tier.model ? { model: tier.model } : {}),
    ...(tier.maxTokens ? { maxTokens: tier.maxTokens } : {}),
  }
}

/** 解析配置中的档位表（校验非空、档位名唯一），无效时回退默认表 */
export function resolveLadder(configured: LadderTier[] | undefined): LadderTier[] {
  if (!configured || configured.length === 0) return DEFAULT_LADDER
  const names = new Set(configured.map((tier) => tier.name))
  if (names.size !== configured.length) return DEFAULT_LADDER
  return configured
}
