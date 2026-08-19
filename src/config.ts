import z from 'schemastery'

/**
 * 插件配置。默认值即可开箱即用；如需修改，可在 profile 的
 * cordis.patch.yml 中覆盖该行的 config，或在 web GUI 的
 * Settings → 插件配置 中调整。
 */
export interface Config {
  /** 流水线状态文件路径（跨会话持久化闭环进度） */
  stateFile?: string
}

export const Config: z<Config> = z.object({
  stateFile: z.string().default('~/.dsh/pipeline-state.json'),
})
