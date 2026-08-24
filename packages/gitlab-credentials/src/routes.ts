/**
 * The /api/dsh-gitlab-credentials route family: credential status, save
 * (validate -> store -> glab login), delete (store + glab logout), and MR
 * preference read/write. Every route carries the loopback-only trust fence —
 * these endpoints accept credentials for the GitLab API, so LAN-exposed dsh
 * web deployments must not serve them. Tokens never appear in responses.
 */

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { WebRoute } from '@deepseek-ai/dsh-host-webserver'
import { isLoopbackRequest } from './loopback.ts'
import { API_PREFIX, type CredentialRecord, type MrPreferences } from './protocol.ts'
import { glabAuthed, glabLogin, glabLogout, validateToken } from './glab.ts'
import type { CredentialStore } from './store.ts'

/** Cap on JSON request bodies (credentials + preferences are small). */
const MAX_JSON_BODY_BYTES = 256 * 1024

/** One JSON response. */
function writeJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'referrer-policy': 'no-referrer' })
  res.end(payload)
}

/** Read a JSON request body (undefined when too large or unparseable). */
async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown> | undefined> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = chunk as Buffer
    size += buffer.length
    if (size > MAX_JSON_BODY_BYTES) return undefined
    chunks.push(buffer)
  }
  try {
    const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : undefined
  } catch {
    return undefined
  }
}

/** Normalize the apiHost: empty means the host itself (glab semantics). */
function apiHostValue(value: unknown): string {
  return typeof value === 'string' ? value.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '') : ''
}

/** Sanitize arbitrary text into a hostname-shaped id (no spaces/slashes). */
function hostValue(value: unknown): string {
  const host = typeof value === 'string' ? value.trim() : ''
  if (host === '' || host.includes(' ') || host.includes('/') || host.includes(':')) return ''
  return host
}

export interface CredentialRoutesDeps {
  store: CredentialStore
}

export function makeRoutes(deps: CredentialRoutesDeps): { routes: WebRoute[] } {
  const { store } = deps

  /** Guard helper: fence + method + JSON body. */
  const guard = (req: IncomingMessage, res: ServerResponse, method: string): boolean => {
    if (!isLoopbackRequest(req)) {
      writeJson(res, 403, { error: 'forbidden: loopback-only' })
      return false
    }
    if (req.method !== method) {
      writeJson(res, 405, { error: `method not allowed: ${req.method}` })
      return false
    }
    return true
  }

  const routes: WebRoute[] = [
    {
      kind: 'exact',
      path: `${API_PREFIX}/status`,
      handler: async (req, res) => {
        if (!guard(req, res, 'GET')) return
        const summaries = await Promise.all(store.summaries().map(async summary => ({
          ...summary,
          glabAuthed: await glabAuthed(summary.host).catch(() => false),
        })))
        writeJson(res, 200, { hosts: summaries, mrPreferences: store.mrPreferences() })
      },
    },
    {
      kind: 'exact',
      path: `${API_PREFIX}/save`,
      handler: async (req, res) => {
        if (!guard(req, res, 'POST')) return
        const body = await readJsonBody(req)
        if (body === undefined) {
          writeJson(res, 400, { error: 'invalid JSON body' })
          return
        }
        const host = hostValue(body.host)
        const token = typeof body.token === 'string' ? body.token.trim() : ''
        const apiProtocol = body.apiProtocol === 'https' ? 'https' : 'http'
        const gitProtocol = body.gitProtocol === 'https' ? 'https' : 'ssh'
        if (host === '') {
          writeJson(res, 400, { error: 'host must be a non-empty hostname without spaces, slashes or colons' })
          return
        }
        if (token === '') {
          writeJson(res, 400, { error: 'token must not be empty' })
          return
        }
        const apiHost = apiHostValue(body.apiHost) || host
        try {
          const { user } = await validateToken(apiProtocol, apiHost, token)
          const record: CredentialRecord = {
            host,
            apiProtocol,
            apiHost,
            gitProtocol,
            token,
            user,
            lastChecked: new Date().toISOString(),
            lastError: '',
          }
          store.upsert(record)
          // Sync into glab (best-effort: glab may be absent; the store remains authoritative).
          let glabError = ''
          try {
            await glabLogin(host, apiProtocol, gitProtocol, token)
          } catch (error) {
            glabError = (error as Error).message
          }
          writeJson(res, 200, {
            host,
            user,
            glabError,
            glabAuthed: glabError === '' ? await glabAuthed(host).catch(() => false) : false,
          })
        } catch (error) {
          const message = (error as Error).message.replace(/[A-Za-z0-9_-]{20,}/g, '<redacted>')
          writeJson(res, 400, { error: message })
        }
      },
    },
    {
      kind: 'exact',
      path: `${API_PREFIX}/delete`,
      handler: async (req, res) => {
        if (!guard(req, res, 'POST')) return
        const body = await readJsonBody(req)
        if (body === undefined) {
          writeJson(res, 400, { error: 'invalid JSON body' })
          return
        }
        const host = hostValue(body.host)
        if (host === '') {
          writeJson(res, 400, { error: 'host is required' })
          return
        }
        const removed = store.remove(host)
        await glabLogout(host).catch(() => undefined)
        writeJson(res, 200, { removed })
      },
    },
    {
      kind: 'exact',
      path: `${API_PREFIX}/mr-preferences`,
      handler: async (req, res) => {
        if (!guard(req, res, 'POST')) return
        const body = await readJsonBody(req)
        if (body === undefined || typeof body.preferences !== 'object' || body.preferences === null) {
          writeJson(res, 400, { error: 'preferences object is required' })
          return
        }
        const prefs = body.preferences as unknown as Partial<MrPreferences>
        const saved = store.setMrPreferences({
          assignee: typeof prefs.assignee === 'string' ? prefs.assignee : '',
          targetBranch: typeof prefs.targetBranch === 'string' ? prefs.targetBranch : '',
          labels: typeof prefs.labels === 'string' ? prefs.labels : '',
          milestone: typeof prefs.milestone === 'string' ? prefs.milestone : '',
          removeSourceBranch: prefs.removeSourceBranch !== false,
        })
        writeJson(res, 200, { mrPreferences: saved })
      },
    },
  ]

  return { routes }
}
