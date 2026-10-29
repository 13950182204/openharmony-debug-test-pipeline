/**
 * GitLab + glab integration: token validation against the GitLab API and
 * glab CLI session sync (login -> read-back verification). The token is
 * passed to the glab child process over STDIN only — never as a command-line
 * argument, never logged, never written to any repository or MR artifact.
 *
 * Host shape matters: DSH Desktop runs this plugin on **Windows**, where glab
 * usually only exists inside WSL. On that shape the runner transparently
 * bridges through `wsl.exe -e bash -lc 'glab ...'` (token still travels over
 * stdin), so "保存并校验" ends with a working glab session instead of a
 * permanent "glab 未同步" badge.
 */

import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { which } from './which.ts'

/** Per-call option bag (config value; escape hatch for exotic installs). */
export interface GlabOptions {
  /**
   * Override for the glab runner.
   * - single token  => absolute path of the glab binary
   * - multiple words => shell-style prefix with the glab argv appended,
   *   e.g. `wsl.exe -d Ubuntu-22.04 -e bash -lc`
   */
  glabCommand?: string
}

/** Result of one glab process. */
interface GlabResult { code: number; stdout: string; stderr: string }

/** A resolved way to run glab on this host. */
interface GlabRunner {
  command: string
  /** Human-readable description shown in the settings page. */
  describe: string
  /** Build the full argv for one logical glab invocation. */
  argv: (glabArgs: string[]) => string[]
}

/** Quote one argv element for a POSIX shell (shell-prefixed runners only). */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`
}

/** Shell-style split of a configured command line (no quoting: keep it simple). */
function splitWords(commandLine: string): string[] {
  return commandLine.trim().split(/\s+/).filter(word => word !== '')
}

/** POSIX install locations worth trying when PATH lookup fails. */
const POSIX_FALLBACKS = ['/snap/glab/current/glab', '/usr/local/bin/glab', '/usr/bin/glab', '/opt/homebrew/bin/glab']

/**
 * On a Windows host glab normally lives inside WSL. Probe the default distro
 * once; when found, run every glab invocation through a login shell there.
 */
function windowsWslRunner(): GlabRunner | undefined {
  if (process.platform !== 'win32') return undefined
  let probe
  try {
    probe = spawnSync('wsl.exe', ['-e', 'bash', '-lc', 'command -v glab'], {
      encoding: 'utf8', timeout: 10000, windowsHide: true,
    })
  } catch {
    return undefined
  }
  if (probe.status !== 0 || (probe.stdout ?? '').trim() === '') return undefined
  return {
    command: 'wsl.exe',
    describe: 'wsl.exe -e bash -lc glab',
    argv: glabArgs => ['-e', 'bash', '-lc', `glab ${glabArgs.map(shellQuote).join(' ')}`],
  }
}

/** Resolve the runner for this host + options; undefined when glab is unavailable. */
function resolveRunner(options: GlabOptions): GlabRunner | undefined {
  const override = options.glabCommand?.trim() ?? ''
  if (override !== '') {
    const words = splitWords(override)
    if (words.length > 1) {
      const [command, ...prefix] = words
      return {
        command,
        describe: override,
        argv: glabArgs => [...prefix, `glab ${glabArgs.map(shellQuote).join(' ')}`],
      }
    }
    return { command: words[0], describe: words[0], argv: glabArgs => glabArgs }
  }
  const fromPath = which('glab')
  if (fromPath !== undefined) return { command: fromPath, describe: fromPath, argv: glabArgs => glabArgs }
  for (const candidate of POSIX_FALLBACKS) {
    try {
      if (existsSync(candidate)) return { command: candidate, describe: candidate, argv: glabArgs => glabArgs }
    } catch { /* ignore */ }
  }
  return windowsWslRunner()
}

/** Resolved runners are cached per option value (the WSL probe is not free). */
const runnerCache = new Map<string, GlabRunner | undefined>()

function runnerFor(options: GlabOptions): GlabRunner | undefined {
  const key = options.glabCommand?.trim() ?? ''
  if (!runnerCache.has(key)) runnerCache.set(key, resolveRunner(options))
  return runnerCache.get(key)
}

/** Description of the runner for the settings page, or '' when none resolved. */
export function describeGlab(options: GlabOptions = {}): string {
  return runnerFor(options)?.describe ?? ''
}

/**
 * Validate a token against GET /api/v4/user. Returns the username; throws with
 * a human-readable message on failure. The token never appears in the error text.
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

/** Run glab with args, feeding `input` to STDIN; resolves with stdout. */
function runGlab(glabArgs: string[], input?: string, options: GlabOptions = {}): Promise<GlabResult> {
  return new Promise(resolve => {
    const runner = runnerFor(options)
    if (runner === undefined) {
      resolve({
        code: 127,
        stdout: '',
        stderr: 'glab not found: missing on PATH and no WSL session to bridge to (set credentials.glabCommand to override)',
      })
      return
    }
    let child
    try {
      child = spawn(runner.command, runner.argv(glabArgs), { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true })
    } catch (error) {
      resolve({ code: 127, stdout: '', stderr: (error as Error).message })
      return
    }
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8') })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8') })
    child.on('error', (error: Error) => resolve({ code: 127, stdout, stderr: error.message }))
    child.on('close', code => resolve({ code: code ?? -1, stdout, stderr }))
    child.stdin.on('error', () => { /* EPIPE when glab exits early */ })
    if (input !== undefined) child.stdin.write(input)
    child.stdin.end()
  })
}

/** One-line error text from a glab run (never contains the token). */
function errorText(result: GlabResult): string {
  return result.stderr.trim().split('\n').pop() || result.stdout.trim() || `exit ${result.code}`
}

/**
 * Log the token into the glab CLI for one host. Token flows over STDIN to
 * `glab auth login --stdin`; the command arguments never carry it.
 */
export async function glabLogin(host: string, apiProtocol: 'http' | 'https', gitProtocol: 'ssh' | 'https', token: string, options: GlabOptions = {}): Promise<void> {
  const result = await runGlab(
    ['auth', 'login', '--hostname', host, '--api-host', host, '--api-protocol', apiProtocol, '--git-protocol', gitProtocol, '--stdin'],
    `${token}\n`,
    options,
  )
  if (result.code !== 0) {
    throw new Error(`glab auth login failed (${errorText(result)})`)
  }
}

/** Log out one host from glab (best-effort; ignores missing sessions). */
export async function glabLogout(host: string, options: GlabOptions = {}): Promise<void> {
  await runGlab(['auth', 'logout', '--hostname', host], undefined, options)
}

/** Whether glab currently reports an authenticated session for the host. */
export async function glabAuthed(host: string, options: GlabOptions = {}): Promise<boolean> {
  const result = await runGlab(['auth', 'status', '--hostname', host], undefined, options)
  return result.code === 0
}

/** Whether glab itself is usable here (binary + working bridge). */
export async function glabProbe(options: GlabOptions = {}): Promise<{ available: boolean; describe: string; error: string }> {
  const result = await runGlab(['--version'], undefined, options)
  return {
    available: result.code === 0,
    describe: describeGlab(options),
    error: result.code === 0 ? '' : errorText(result),
  }
}

/**
 * Read the account glab actually acts as for one host and compare it with the
 * token owner we validated. This is the "同步后验证" step: a successful
 * `auth login` exit code alone does not prove the CLI picked the new token up.
 */
export async function glabVerify(host: string, expectedUser: string, options: GlabOptions = {}): Promise<{ verified: boolean; user: string; error: string }> {
  const result = await runGlab(['api', '--hostname', host, 'user'], undefined, options)
  if (result.code !== 0) {
    return { verified: false, user: '', error: `glab 回读失败：${errorText(result)}` }
  }
  let body: { username?: string } | null = null
  try {
    body = JSON.parse(result.stdout) as { username?: string }
  } catch {
    body = null
  }
  const user = typeof body?.username === 'string' ? body.username : ''
  if (user === '') return { verified: false, user, error: 'glab 回读失败：无法解析当前登录用户' }
  if (expectedUser !== '' && user !== expectedUser) {
    return { verified: false, user, error: `glab 当前登录用户为 ${user}，与令牌用户 ${expectedUser} 不一致` }
  }
  return { verified: true, user, error: '' }
}
