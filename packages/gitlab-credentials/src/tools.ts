/**
 * Agent tools: a read-only status surface for the credential store plus the
 * MR preferences gate (also read by the glab-mr-submit scripts). Tokens never
 * leave the host — the tool reports user, fingerprint and glab sync state.
 */

import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import { glabAuthed } from './glab.ts'
import type { CredentialStore } from './store.ts'

/** One text content block (the only render shape these tools emit). */
function text(value: string): ContentBlock[] {
  return [{ type: 'text', text: value }]
}

/** The status tool: per-host GitLab credential state (no tokens, ever). */
export function gitlabCredStatusTool(store: CredentialStore) {
  return defineTool({
    name: 'gitlab_cred_status',
    description: 'Show GitLab credential state per configured host (user, token fingerprint, last check, glab sync). ' +
      'Triggers: GitLab token, GitLab 认证, 凭据, MR 提交失败需要认证, gitlab authentication. ' +
      'Tokens are never exposed; if a host is missing or not synced, ask the user to save it in 设置 > GitLab 凭据.',
    parameters: {},
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          hosts: {
            type: 'array',
            required: true,
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                host: { type: 'string', required: true },
                user: { type: 'string', required: true },
                hasToken: { type: 'boolean', required: true },
                fingerprint: { type: 'string', required: true },
                lastChecked: { type: 'string', required: true },
                glabAuthed: { type: 'boolean', required: true },
                lastError: { type: 'string' },
              },
            },
          },
          mrPreferences: {
            type: 'object',
            additionalProperties: false,
            properties: {
              assignee: { type: 'string', required: true },
              targetBranch: { type: 'string', required: true },
              labels: { type: 'string', required: true },
              milestone: { type: 'string', required: true },
              removeSourceBranch: { type: 'boolean', required: true },
            },
          },
        },
      },
      render: (_args, value: { hosts?: Array<{ host: string; user: string; hasToken: boolean; fingerprint: string; lastChecked: string; glabAuthed: boolean; lastError?: string }>; mrPreferences?: unknown }) => {
        const hosts = value.hosts ?? []
        const head = ['host | user | token | fingerprint | lastChecked | glab', '--- | --- | --- | --- | --- | ---']
        const rows = hosts.map(host => [
          host.host,
          host.user || '-',
          host.hasToken ? 'yes' : 'no',
          host.fingerprint || '-',
          host.lastChecked || '-',
          host.glabAuthed ? 'authed' : (host.hasToken ? 'not-synced' : 'none'),
        ].join(' | '))
        const body = rows.length === 0
          ? ['no GitLab credentials configured — ask the user to add one in 设置 > GitLab 凭据']
          : [...head, ...rows]
        const prefs = value.mrPreferences as { assignee?: string; targetBranch?: string; labels?: string; milestone?: string; removeSourceBranch?: boolean } | undefined
        if (prefs !== undefined) {
          body.push('', 'MR preferences:', `assignee=${prefs.assignee ?? ''} targetBranch=${prefs.targetBranch ?? ''} labels=${prefs.labels ?? ''} milestone=${prefs.milestone ?? ''} removeSourceBranch=${String(prefs.removeSourceBranch ?? '')}`)
        }
        return text(body.join('\n'))
      },
    },
    async execute() {
      const summaries = await Promise.all(store.summaries().map(async summary => ({
        ...summary,
        glabAuthed: await glabAuthed(summary.host).catch(() => false),
      })))
      return { hosts: summaries, mrPreferences: store.mrPreferences() }
    },
  })
}
