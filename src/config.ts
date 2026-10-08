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
   * 「宿主 Windows、执行 WSL」时的插件包 WSL 路径（DSH Desktop 直驱 agent loop 的场景）。
   * 不配置则按插件包在宿主上的真实路径渲染 skill 正文——即 Linux/WSL 原生安装下的正确行为。
   */
  wslPackageRoot?: string
  /**
   * **模型**执行用的状态文件路径；缺省与 `stateFile` 相同。
   * Windows 宿主上 `stateFile` 是 `//wsl.localhost/...`（给宿主 Node 读），而模型在 WSL 里
   * 需要 `/home/cx/...`——两者指向同一文件、形态不同，所以单独给。
   */
  stateFileModel?: string
  /**
   * 六维审查用的 subagent provider 名（`ctx.subagents.start()` 的第一个参数）。
   * DSH 内置：`fork`（dsh-subagent-fork-in-process 默认）、`spawn`。缺省 `fork`。
   * 注意这是 **provider 名**，不是运行标签——传错会 `no subagent provider registered`。
   */
  subagentProvider?: string
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
  wslPackageRoot: z.string(),
  subagentProvider: z.string().default('fork'),
  stateFileModel: z.string(),
  reviewLadder: z.array(ladderTierSchema),
})
