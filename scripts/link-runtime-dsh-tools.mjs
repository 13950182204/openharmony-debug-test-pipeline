#!/usr/bin/env node
/**
 * A linked DSH bundle resolves ESM imports from its real source path.  The
 * scheduler uses a Symbol exported by dsh-tools, so a profile-local copy and
 * the active runtime copy are not interchangeable even at the same version.
 *
 * 因此本脚本**不再**拿 `devDependencies` 的版本号当期望值（那会在升级 DSH
 * 后把 `pnpm install` 直接打断），而是去解析**活动运行时**实际携带的
 * dsh-tools：版本一致就沿用，不一致就自动重链；解析不到时降级为警告，
 * 不阻断安装（真正的加载失败仍会在插件启动时暴露）。
 *
 * Windows 宿主（DSH Desktop 在 Windows 侧加载插件、会话命令转发进 WSL）
 * 不需要这层链接，见下方 `process.platform === 'win32'` 分支。
 */

import { existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, symlinkSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

const NAME = '[openharmony-debug-test-pipeline]'
const packageDir = dirname(dirname(fileURLToPath(import.meta.url)))
const target = join(packageDir, 'node_modules', '@deepseek-ai', 'dsh-tools')
const dshHome = join(homedir(), '.dsh')

/**
 * Windows 宿主（DSH Desktop 在 Windows 侧加载插件、会话命令转发进 WSL）上**不做**链接：
 * `~/.dsh/runtime` 那套只在 Linux/WSL 侧存在；而链接的目的——与活动运行时共用
 * `dsh-tools`（`TOOL_RUNTIME_SCHEDULER` 的进程内 Symbol 同一性）——对 Windows 宿主
 * 不适用，因为命令执行不经过这里的 Node。Windows 侧直接用 pnpm 解析的依赖即可。
 */
if (process.platform === 'win32') {
  console.log(`${NAME} Windows 宿主：跳过 dsh-tools 运行时链接（该 Symbol 一致性要求只对 Linux/WSL 侧运行时成立）。`)
  process.exit(0)
}

/**
 * 读取 `<dir>/@deepseek-ai/dsh-tools` 的清单；目录或 package.json 不存在返回 undefined。
 * @param {string} dir - 候选 `node_modules` 目录
 */
function readManifest(dir) {
  const manifest = join(dir, '@deepseek-ai', 'dsh-tools', 'package.json')
  if (!existsSync(manifest)) return undefined
  try {
    const parsed = JSON.parse(readFileSync(manifest, 'utf8'))
    return typeof parsed.version === 'string' ? { version: parsed.version, dir: join(dir, '@deepseek-ai', 'dsh-tools') } : undefined
  } catch {
    return undefined
  }
}

/**
 * 解析活动运行时的 dsh-tools，按优先级返回第一个可用的候选：
 * 1. profile 共享树 `~/.dsh/profiles/node_modules`——`dsh` 启动时按当前 runtime 维护；
 * 2. `~/.local/bin/dsh` wrapper 推导出的 `~/.dsh/runtime/<id>/node_modules`；
 * 3. `~/.dsh/runtime/*` 下确实携带 dsh-tools、且修改时间最新的 runtime。
 */
function resolveRuntimeDshTools() {
  const candidates = []
  candidates.push(join(dshHome, 'profiles', 'node_modules'))

  const wrapper = join(homedir(), '.local', 'bin', 'dsh')
  if (existsSync(wrapper)) {
    try {
      const text = readFileSync(wrapper, 'utf8')
      for (const match of text.matchAll(/(\/[^\s"']*\/\.dsh\/runtime\/[^/\s"']+)\/node_modules/g)) {
        candidates.push(join(match[1], 'node_modules'))
      }
    } catch {
      // 读不到 wrapper 就退到候选 3。
    }
  }

  const runtimesDir = join(dshHome, 'runtime')
  if (existsSync(runtimesDir)) {
    try {
      const entries = readdirSync(runtimesDir, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => join(runtimesDir, entry.name, 'node_modules'))
        .filter((dir) => existsSync(join(dir, '@deepseek-ai', 'dsh-tools', 'package.json')))
        .sort((a, b) => statSync(join(b, '@deepseek-ai', 'dsh-tools', 'package.json')).mtimeMs - statSync(join(a, '@deepseek-ai', 'dsh-tools', 'package.json')).mtimeMs)
      candidates.push(...entries)
    } catch {
      // 目录列举失败时忽略该候选。
    }
  }

  for (const dir of candidates) {
    const manifest = readManifest(dir)
    if (manifest !== undefined) return manifest
  }
  return undefined
}

const runtime = resolveRuntimeDshTools()
if (runtime === undefined) {
  console.warn(`${NAME} 未能解析活动运行时的 dsh-tools（已在 ~/.dsh/profiles 与 ~/.dsh/runtime 下查找）；跳过链接。`)
  console.warn(`${NAME} 若插件启动报 MODULE_NOT_FOUND，先用目标 DSH 跑一次 \`dsh --profile web --dump-config\` 生成共享树，再执行 \`pnpm run link-runtime-deps\`。`)
  process.exit(0)
}

const source = runtime.dir
const declared = JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8')).devDependencies?.['@deepseek-ai/dsh-tools']

/**
 * 链接只在「声明版本 == 活动运行时版本」时执行。
 *
 * 两个版本不同意味着：类型检查基线是声明的那一版，而活动运行时是另一版。
 * 此时绝不改写依赖树——`dsh-tools` 声明了自己的 peer（`dsh-llm`/`dsh-agent`/
 * `dsh-session` 等），把它链到一个不同版本的位置会让这些 peer 解析到两个
 * 世代（本轮实测：0.2.0 的 `defineTool` 参数被 0.1.2 的 `Agent`/`ContentBlock`
 * 顶掉，报 `[BRAND] is missing`）。运行期的 Symbol 一致性由「运行时升级后
 * 版本对齐、自动重链」来保证。
 */
if (declared !== undefined && declared !== runtime.version) {
  console.log(`${NAME} 声明 ${declared} ≠ 活动运行时 ${runtime.version}：跳过 dsh-tools 重链，依赖树保持 pnpm 解析结果（类型检查基线一致）。`)
  console.log(`${NAME} 运行时升到 ${declared} 后重跑 \`pnpm run link-runtime-deps\` 即可自动对齐。`)
  process.exit(0)
}

/** 目标当前是否已指向同一份真实文件。 */
function linkIsCurrent() {
  try {
    lstatSync(target)
  } catch {
    return false // 无 lstat 条目。
  }
  try {
    return realpathSync(target) === realpathSync(source)
  } catch {
    return false // 悬空符号链接：有 lstat 条目但没有 realpath。
  }
}

if (linkIsCurrent()) {
  console.log(`${NAME} dsh-tools ${runtime.version} 链接已是最新（活动运行时）`)
  process.exit(0)
}

mkdirSync(dirname(target), { recursive: true })
try {
  lstatSync(target)
  rmSync(target, { recursive: true, force: true })
} catch {
  // 没有旧条目，直接建链接。
}
symlinkSync(source, target, 'junction')

console.log(`${NAME} 已链接活动运行时的 dsh-tools ${runtime.version} ← ${resolve(source)}`)
