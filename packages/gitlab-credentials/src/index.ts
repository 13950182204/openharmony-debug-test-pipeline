/**
 * dsh-gitlab-credentials — host half. Stores per-host GitLab tokens (0600,
 * never replayed into conversations), validates them against the GitLab API,
 * syncs them into the glab CLI, exposes read-only status to agents, and keeps
 * MR preferences (assignee / target branch / labels / milestone) for the
 * glab-mr-submit scripts. The browser half renders the settings-page section.
 *
 * No dsh source changes: everything rides official NPM SDK packages and the
 * cordis bundle-patch loading path (see README.md).
 */

import type { Context } from '@deepseek-ai/cordis'
import { installSettingsSection, settingsNamespace } from '@deepseek-ai/dsh-settings'
import z from 'schemastery'
import type {} from '@deepseek-ai/dsh-host-webserver'
import type {} from '@deepseek-ai/dsh-system-prompt'
import type {} from '@deepseek-ai/dsh-tools'
import { CredentialStore } from './store.ts'
import { makeRoutes } from './routes.ts'
import { gitlabCredStatusTool } from './tools.ts'
import { mountOnce } from './mount-once.ts'
import { PLUGIN_ID, SETTINGS_NS } from './protocol.ts'

/** Stable cordis plugin name. */
export const name = PLUGIN_ID

/** Services required before the surfaces can mount. */
export const inject = ['webServer', 'tools', 'systemPrompt']

/** Settings namespace of the plugin (web settings surface edits live here). */
export const SETTINGS_NAMESPACE = settingsNamespace(SETTINGS_NS)

/** Plugin config. */
export interface Config {
  /** When true (default), a system-prompt section announces the plugin to every agent. */
  announceToAgent?: boolean
  /** Master switch for the plugin (routes, tools, prompt section). */
  enabled?: boolean
}

export const Config: z<Config> = z.object({
  announceToAgent: z.boolean().default(true),
  enabled: z.boolean().default(true),
})

const DEFAULT_ANNOUNCE = true

/** Order of the announcement section within the tool-guidance band. */
const SECTION_ORDER = 155

/** Model-facing announcement: plugin presence, capabilities, and limits. */
export const CREDENTIALS_GUIDANCE = '本机已安装 dsh-gitlab-credentials 插件（GitLab 凭据管理）：设置页「GitLab 凭据」栏管理每个 GitLab 主机的访问令牌（保存时校验后同步到 glab CLI，供 MR 流程使用）；同一栏可配置 MR 偏好（指派人/目标分支/标签/里程碑）。能力：gitlab_cred_status 工具报告各主机令牌状态（用户/指纹/glab 同步情况，绝不回显令牌）；令牌仅存于 ~/.dsh/gitlab-credentials.json（0600），写入与 glab 同步由用户在 GUI 完成，agent 不掌握令牌明文。限制：令牌需在 GUI 保存后 agent 方可使用；失效时提示用户在设置中重新保存。用户提到「GitLab 令牌 / 凭据 / MR 认证 / 授权提交」时即指本插件，请据此协作。'

/** Stable once-guard per process (bundle re-apply is tolerated, double-mount is not). */
export const apply = mountOnce(PLUGIN_ID, applyImpl)

function applyImpl(ctx: Context, config?: Config): void {
  let current: () => Config = () => config ?? {}
  const resolve = (): Config => ({
    announceToAgent: current().announceToAgent ?? DEFAULT_ANNOUNCE,
    enabled: current().enabled ?? true,
  })

  const store = new CredentialStore()

  const { routes } = makeRoutes({ store })
  let disposeRoutes: (() => void) | undefined
  let disposeTools: (() => void) | undefined
  let disposeSection: (() => void) | undefined

  /** Register (or drop) every surface to match the current source. */
  const sync = (): void => {
    if (disposeSection !== undefined) { disposeSection(); disposeSection = undefined }
    if (disposeRoutes !== undefined) { disposeRoutes(); disposeRoutes = undefined }
    if (disposeTools !== undefined) { disposeTools(); disposeTools = undefined }
    const value = resolve()
    if (!value.enabled) return
    if (value.announceToAgent) {
      disposeSection = ctx.systemPrompt.section({
        name: `plugin:${PLUGIN_ID}`,
        order: SECTION_ORDER,
        text: CREDENTIALS_GUIDANCE,
      })
    }
    disposeRoutes = ctx.effect(
      () => {
        const disposers = routes.map(route => ctx.webServer.register(route))
        return () => { for (const dispose of disposers) dispose() }
      },
      `${PLUGIN_ID}: routes`,
    )
    disposeTools = ctx.effect(
      () => {
        const disposer = ctx.tools.register(gitlabCredStatusTool(store))
        return () => disposer()
      },
      `${PLUGIN_ID}: tools`,
    )
  }

  installSettingsSection(ctx, SETTINGS_NAMESPACE, Config, config ?? {}, {
    setSource: (source) => {
      current = source
      sync()
    },
    onChange: sync,
  })

  // Initial registration from the composition entry (deployments with no
  // settings service never fire the hooks above).
  sync()
}
