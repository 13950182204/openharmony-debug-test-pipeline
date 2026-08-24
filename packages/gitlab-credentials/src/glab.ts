/**
 * GitLab + glab integration: token validation against the GitLab API and
 * glab CLI session sync. The token is passed to the glab child process over
 * STDIN only — never as a command-line argument, never logged, never written
 * to any repository or MR artifact.
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { which } from './which.ts'

/**
 * Validate a token against GET /api/v4/user. Returns the username and scope
 * info; throws with a human-readable message on failure. The token never
 * appears in the error text.
 */
export async function validateToken(apiProtocol: 'http' | 'https', apiHost: string, token: string): Promise<{ user: string; scopes: string[] }> {
  const base = apiHost === '' ? '' : `${apiProtocol}://${apiHost}`
  const url = `${base}/api/v4/user`
  let response: Response
  try {
    response = await fetch(url, {
      headers: { 'PRIVATE-TOKEN': token, 'user-agent': 'dsh-gitlab-credentials' },
      signal: AbortSignal.timeout(10000),
    })
  } catch (error) {
    throw new Error(`cannot reach GitLab API at ${url} (${(error as Error).message})`)
  }
  if (!response.ok) {
    throw new Error(`GitLab API validation failed: HTTP ${response.status}`)
  }
  const body = (await response.json().catch(() => null)) as { username?: string } | null
  if (body === null || typeof body.username !== 'string') {
    throw new Error('GitLab API validation failed: unexpected response shape')
  }
  // Scopes are not exposed by /user; the API was reachable and authenticated.
  return { user: body.username, scopes: [] }
}

/** Locate the glab binary (PATH first, then the snap path). */
function resolveGlab(): string | undefined {
  const fromPath = which('glab')
  if (fromPath !== undefined) return fromPath
  for (const candidate of ['/snap/glab/current/glab', '/usr/local/bin/glab', '/usr/bin/glab']) {
    try {
      if (existsSync(candidate)) return candidate
    } catch { /* ignore */ }
  }
  return undefined
}

/** Run glab with args, feeding `input` to STDIN; resolves with stdout. */
function runGlab(args: string[], input?: string): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise(resolve => {
    const binary = resolveGlab()
    if (binary === undefined) {
      resolve({ code: 127, stdout: '', stderr: 'glab binary not found' })
      return
    }
    const child = spawn(binary, args, { stdio: ['pipe', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8') })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8') })
    child.on('close', code => resolve({ code: code ?? -1, stdout, stderr }))
    child.stdin.on('error', () => { /* EPIPE when glab exits early */ })
    if (input !== undefined) child.stdin.write(input)
    child.stdin.end()
  })
}

/**
 * Log the token into the glab CLI for one host. Token flows over STDIN to
 * `glab auth login --stdin`; the command arguments never carry it.
 */
export async function glabLogin(host: string, apiProtocol: 'http' | 'https', gitProtocol: 'ssh' | 'https', token: string): Promise<void> {
  const result = await runGlab(
    ['auth', 'login', '--hostname', host, '--api-host', host, '--api-protocol', apiProtocol, '--git-protocol', gitProtocol, '--stdin'],
    `${token}\n`,
  )
  if (result.code !== 0) {
    throw new Error(`glab auth login failed (exit ${result.code}): ${result.stderr.trim() || result.stdout.trim() || 'unknown error'}`)
  }
}

/** Log out one host from glab (best-effort; ignores missing sessions). */
export async function glabLogout(host: string): Promise<void> {
  await runGlab(['auth', 'logout', '--hostname', host])
}

/** Whether glab currently reports an authenticated session for the host. */
export async function glabAuthed(host: string): Promise<boolean> {
  const result = await runGlab(['auth', 'status', '--hostname', host])
  return result.code === 0
}
