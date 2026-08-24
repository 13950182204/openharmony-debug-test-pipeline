#!/usr/bin/env node
/**
 * Vendor the rc.2-consistent runtime SDK packages into this plugin's own
 * node_modules as symlinks. @deepseek-ai/* host packages are not published to
 * the public npm registry (the dsh runtime carries them), so a link: plugin
 * must not rely on npm resolution walking up to the runtime tree — Node
 * resolves ESM imports from the plugin's real path (e.g.
 * /home/cx/.../dsh-gitlab-credentials) and fails with MODULE_NOT_FOUND,
 * which takes the whole cordis profile down at boot.
 *
 * Symlinks here point INTO the running dsh runtime tree, so every transitive
 * @deepseek-ai dependency also resolves (the target packages already live
 * inside that tree). Run automatically via "postinstall" (pnpm/npm install);
 * safe to re-run: existing links are refreshed, nothing else is touched.
 */
import { existsSync, symlinkSync, mkdirSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

const PKG_DIR = dirname(dirname(fileURLToPath(import.meta.url)))
const RUNTIME_GLOB = join(homedir(), '.dsh', 'runtime')
const TARGETS = ['dsh-settings', 'dsh-tools', 'dsh-llm', 'dsh-host-webserver', 'dsh-system-prompt', 'schemastery']

function runtimeNodeModules() {
  if (!existsSync(RUNTIME_GLOB)) return undefined
  const entries = readdirSync(RUNTIME_GLOB)
    .filter(name => name.startsWith('dsh-'))
    .map(name => join(RUNTIME_GLOB, name))
    .filter(path => statSync(path).isDirectory())
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
  for (const fallback of ['dsh-011rc2', 'dsh-011rc1', 'dsh-011rc2']) {
    const direct = join(RUNTIME_GLOB, fallback, 'node_modules', '.pnpm', 'node_modules', '@deepseek-ai')
    if (existsSync(direct)) return direct
  }
  for (const entry of entries) {
    const candidate = join(entry, 'node_modules', '.pnpm', 'node_modules', '@deepseek-ai')
    if (existsSync(candidate)) return candidate
  }
  return undefined
}

const sdk = runtimeNodeModules()
if (sdk === undefined) {
  console.error('[dsh-gitlab-credentials] no dsh runtime found under ~/.dsh/runtime; vendored deps missing')
  process.exit(1)
}

const linkDir = join(PKG_DIR, 'node_modules', '@deepseek-ai')
mkdirSync(linkDir, { recursive: true })

let linked = 0
for (const name of TARGETS) {
  // The runtime hosts @deepseek-ai/schemastery; the plugin imports 'schemastery'
  // (bare name), so alias it into the plugin's node_modules root.
  const source = name === 'schemastery' ? join(sdk, 'schemastery') : join(sdk, name)
  if (!existsSync(source)) {
    console.warn(`[dsh-gitlab-credentials] runtime package missing: ${name}; continuing`)
    continue
  }
  const target = name === 'schemastery' ? join(PKG_DIR, 'node_modules', name) : join(linkDir, name)
  try {
    if (existsSync(target)) unlinkSync(target)
  } catch { /* not a link */ }
  symlinkSync(source, target, 'junction')
  linked++
}

if (linked === 0) {
  console.error('[dsh-gitlab-credentials] no runtime SDK packages linked; the plugin will not load')
  process.exit(1)
}
console.log(`[dsh-gitlab-credentials] vendored ${linked} runtime SDK packages from ${sdk}`)
