/**
 * Browser-half API client for the /api/dsh-gitlab-credentials route family.
 * Tokens are typed into the form and sent once per save over the loopback
 * fetch; nothing is cached in the browser beyond the in-memory form state.
 */

import type { HostStatus, MrPreferences } from '../protocol.ts'

/** Shape of the status response. */
export interface StatusResponse {
  hosts: HostStatus[]
  mrPreferences: MrPreferences
}

export interface SaveResponse {
  host: string
  user: string
  glabError: string
  glabAuthed: boolean
}

/** POST JSON helper with error extraction. */
async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => null) as (T & { error?: string }) | null
  if (!response.ok) {
    throw new Error(payload?.error ?? `request failed: HTTP ${response.status}`)
  }
  return payload as T
}

export class GitlabApi {
  /** Fetch credential status + MR preferences. */
  async status(): Promise<StatusResponse> {
    const response = await fetch('/api/dsh-gitlab-credentials/status')
    const payload = await response.json().catch(() => null) as (StatusResponse & { error?: string }) | null
    if (!response.ok) {
      throw new Error(payload?.error ?? `status request failed: HTTP ${response.status}`)
    }
    return payload as StatusResponse
  }

  /** Validate + store a token and sync it into glab. */
  save(input: {
    host: string
    token: string
    apiProtocol: 'http' | 'https'
    apiHost: string
    gitProtocol: 'ssh' | 'https'
  }): Promise<SaveResponse> {
    return postJson<SaveResponse>('/api/dsh-gitlab-credentials/save', input)
  }

  /** Delete one host (store + glab logout). */
  async remove(host: string): Promise<boolean> {
    const result = await postJson<{ removed: boolean }>('/api/dsh-gitlab-credentials/delete', { host })
    return result.removed
  }

  /** Save MR preferences. */
  saveMrPreferences(preferences: MrPreferences): Promise<{ mrPreferences: MrPreferences }> {
    return postJson('/api/dsh-gitlab-credentials/mr-preferences', { preferences })
  }
}
