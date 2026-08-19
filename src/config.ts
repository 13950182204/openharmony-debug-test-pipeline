import z from 'schemastery'
import type { LadderTier } from './review-ladder.ts'

/**
 * 插件配置。默认值即可开箱即用；如需修改，可在 profile 的
 * cordis.patch.yml 中覆盖该行的 config，或在 web GUI 的
 * Settings → 插件配置 中调整。
 */
export interface Config {
  /** 流水线状态文件路径（跨会话持久化闭环进度） */
  stateFile?: string
  /**
   * 六维审查子代理模型档位表（可选）。索引 0 为最强档，末档为下限。
   * 缺省两档：high=deepseek-v4-pro、low=deepseek-v4-flash。
   * 注意：reasoningEffort 是 provider 全局配置，无法按子代理区分。
   */
  reviewLadder?: LadderTier[]
}

const ladderTierSchema = z.object({
  name: z.string(),
  provider: z.string(),
  model: z.string(),
  maxTokens: z.number(),
})

export const Config: z<Config> = z.object({
  stateFile: z.string().default('~/.dsh/pipeline-state.json'),
  reviewLadder: z.array(ladderTierSchema),
})
