#!/usr/bin/env node
/**
 * 在 **Windows 侧**把本插件与 `@superjunier/dsh-gitlab-credentials` 重装进 DSH Desktop 的
 * desktop profile。用法（在 Windows 上跑，Node 任意 20+）：
 *
 *   node <repo>/scripts/desktop-reinstall.mjs [--profile <名字>] [--dry-run]
 *
 * 为什么需要这个脚本，而不是直接在 profile 目录 `pnpm add`：
 *
 * 1. **PATH 必须显式补上 node 目录**。DSH Desktop 自带的 pnpm 在派生子进程时不会把
 *    node 放进子环境 PATH，于是两包 `postinstall` 里的 `node ...` 一律报
 *    `'node' 不是内部或外部命令`（实测）。只补 `…/dependencies/node/bin`，
 *    **绝不把 Node 根目录加进 PATH**——Node 根目录同时含 `bash.exe`/`wsl.exe`，
 *    一旦进入 PATH 会顶掉 WSL 的 bash 与 wsl.exe，而 Windows 侧 bash.exe 对
 *    `\\wsl.localhost\…` 路径无法工作，会连带打断会话里的 bash 工具。
 *
 * 2. **依赖树必须保持"每个核心包只有一个版本"**。本插件的 `@deepseek-ai/dsh-tools`
 *    是精确钉版本（与 runtime 的 asar 副本同版本）；一旦 profile 里出现第二个版本的
 *    核心包，就会出现"同版本号但不同模块 URL"的隐患
 *    （参见知识库《DSH-rc1工具调度器与Web profile重复模块故障》）。
 *    脚本在安装后强制做一次副本矩阵自检，多副本即失败退出。
 *
 * 3. 安装完成后 profile 的 `dsh.profile.bundles` 必须含这两个包名，否则不会加载。
 *    脚本会校正并打印最终清单。
 */

import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, writeFileSync, copyFileSync, rmSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/**
 * 两个分发入口。**版本从各自 package.json 现读**，不写死——写死版本会在
 * 仓库升版后让助手去找一个不存在的 tarball 文件名（本脚本第一版就是这样，
 * 在 1.1.13 上直接失败）。
 *
 * `dir` 是打包目录；tarball 文件名沿用 pnpm pack 的规则（scope 用 `-` 连接）。
 */
const PLUGINS = [
  { name: 'openharmony-debug-test-pipeline', dir: REPO },
  { name: '@superjunier/dsh-gitlab-credentials', dir: join(REPO, 'packages', 'gitlab-credentials') },
]

/** pnpm pack 对带 scope 的包名所用的 tarball 文件名。 */
function tgzName(name, version) {
  return `${name.replace(/^@/, '').replace(/\//g, '-')}-${version}.tgz`
}

/**
 * `dsh-wsl-workspace` 在启动期直接 import 的宿主 SDK 包。
 *
 * DSH Desktop 把宿主打在 `app.asar` 里，而插件装在 profile 下——Node 沿文件系统往上
 * 找裸包名**永远走不进归档**，于是这些 import 全部失败、WSL 变体预设生成不出来
 * （症状：`Unknown agent preset: wsl-standard`；见该插件 CHANGELOG 与
 * 《DSH-rc1工具调度器与Web profile重复模块故障》同类的"模块解析锚点"问题）。
 *
 * 修法是把它们以**与 runtime asar 相同的版本**装进 profile：不引入第二个版本，
 * 只是把归档里的那一份在 profile 侧变得可解析。
 */
const HOST_SDK = [
  '@deepseek-ai/dsh-fs',
  '@deepseek-ai/dsh-fs-local',
  '@deepseek-ai/dsh-shell',
  '@deepseek-ai/dsh-tool-fs-search',
]
const HOST_SDK_VERSION = '0.2.0-rc.2'

/** 解析 DSH Desktop 自带的 runtime 目录（含 node 与 pnpm）。 */
function resolveRuntime() {
  const candidates = [
    join(process.env.LOCALAPPDATA ?? '', 'Programs', 'DeepSeek Harness', 'resources', 'runtime'),
    join('C:', 'Users', process.env.USERNAME ?? '', 'AppData', 'Local', 'Programs', 'DeepSeek Harness', 'resources', 'runtime'),
  ]
  for (const base of candidates) {
    if (base === '' || base.startsWith(':')) continue
    const node = join(base, 'primary-runtime', 'dependencies', 'node', 'bin', 'node.exe')
    const pnpm = join(base, 'primary-runtime', 'dependencies', 'pnpm', 'bin', 'pnpm.cjs')
    if (existsSync(node) && existsSync(pnpm)) return { node, pnpm, nodeBin: dirname(node) }
  }
  return undefined
}

/** 每个 `@deepseek-ai/*` 包在 profile 树里的版本与嵌套副本（自检用）。 */
function auditCoreCopies(nodeModules) {
  const scope = join(nodeModules, '@deepseek-ai')
  if (!existsSync(scope)) return { versions: {}, nested: [] }
  const versions = {}
  const nested = []
  for (const name of readdirSync(scope)) {
    const manifest = join(scope, name, 'package.json')
    if (!existsSync(manifest)) continue
    versions[name] = JSON.parse(readFileSync(manifest, 'utf8')).version
    const inner = join(scope, name, 'node_modules', '@deepseek-ai')
    if (!existsSync(inner)) continue
    for (const dep of readdirSync(inner)) nested.push(`${name} -> ${dep}`)
  }
  return { versions, nested }
}

const argv = process.argv.slice(2)
const dryRun = argv.includes('--dry-run')
const profileIdx = argv.indexOf('--profile')
const profile = profileIdx >= 0 ? argv[profileIdx + 1] : 'desktop'
const dshHome = join(homedir(), '.dsh')
const profileDir = join(dshHome, 'profiles', profile)
const storeDir = join(dshHome, 'plugins')
const nodeModules = join(profileDir, 'node_modules')

if (!existsSync(join(profileDir, 'package.json'))) {
  console.error(`找不到 profile：${profileDir}`)
  process.exit(1)
}

const runtime = resolveRuntime()
if (runtime === undefined) {
  console.error('找不到 DSH Desktop 自带的 runtime（期望 …/resources/runtime/primary-runtime/dependencies）。')
  process.exit(1)
}

console.log(`profile   : ${profileDir}`)
console.log(`runtime   : ${runtime.node}`)
console.log(`plugins   : ${storeDir}`)

// 0) 解析每个入口的包名/版本，tarball 缺失就**就地重打包**——这样即使
//    `~/.dsh/plugins` 被清理（本机发生过多次），助手仍是一条命令可恢复。
const entries = PLUGINS.map(({ name, dir }) => {
  const manifest = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
  return { name, dir, version: manifest.version, tgz: tgzName(name, manifest.version), packed: false }
})
for (const entry of entries) {
  const path = join(storeDir, entry.tgz)
  if (existsSync(path)) continue
  console.log(`tarball 缺失，重新打包：${entry.tgz}`)
  if (dryRun) { entry.packed = true; continue }
  const r = spawnSync(runtime.node, [runtime.pnpm, 'pack', '--pack-destination', storeDir], {
    cwd: entry.dir, stdio: 'inherit', timeout: 400000,
  })
  if (r.status !== 0 || !existsSync(path)) {
    console.error(`✗ 打包失败：${entry.name}（exit ${r.status}）`)
    process.exit(1)
  }
  entry.packed = true
}
console.log('插件版本：', entries.map((e) => `${e.name}@${e.version}`).join(' | '))

// 1) 清单：登记依赖并确保进 bundles（少了 bundles 插件不会加载）
const manifestPath = join(profileDir, 'package.json')
const backupPath = `${manifestPath}.bak-desktop-reinstall`
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
if (!dryRun) copyFileSync(manifestPath, backupPath)
manifest.dependencies ??= {}
for (const { name, tgz } of entries) {
  manifest.dependencies[name] = `file:${join(storeDir, tgz).replace(/\\/g, '/')}`
}
manifest.dsh ??= {}
manifest.dsh.profile ??= {}
const bundles = (manifest.dsh.profile.bundles ??= [])
for (const { name } of entries) if (!bundles.includes(name)) bundles.push(name)
if (dryRun) {
  console.log(`[dry-run] 清单将登记 ${entries.length} 个插件 + ${HOST_SDK.length} 个宿主 SDK 包，bundles=${bundles.length}`)
} else {
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  console.log(`清单已更新（备份：${backupPath}），bundles=${bundles.length}`)
}

// 1b) 配置行：profile 重置会连 `cordis.patch.yml` 里的插件配置一起丢掉，而这两行是
//     "skill 正文路径可用"的前提（宿主 Windows / 执行 WSL）。缺失就补回默认值，
//     存在则不动（尊重用户改过的值）。
const patchPath = join(profileDir, 'cordis.patch.yml')
const PLUGIN_CONFIG = {
  'oh-debug-pipeline': {
    name: 'openharmony-debug-test-pipeline',
    config: {
      wslPackageRoot: '/home/cx/os/openharmony-debug-test-pipeline',
      stateFile: '//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json',
      stateFileModel: '/home/cx/.dsh/pipeline-state.json',
    },
  },
  'gitlab-credentials': {
    name: '@superjunier/dsh-gitlab-credentials',
    config: {
      storeFile: '//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/gitlab-credentials.json',
      announceToAgent: false,
    },
  },
}
const patchText = existsSync(patchPath) ? readFileSync(patchPath, 'utf8') : ''
const missingConfig = Object.entries(PLUGIN_CONFIG).filter(([id]) => !patchText.includes(`id: ${id}`))
if (missingConfig.length > 0) {
  const block = missingConfig.map(([id, spec]) => {
    const lines = [`- id: ${id}`, `  name: "${spec.name}"`, '  config:']
    for (const [k, v] of Object.entries(spec.config)) lines.push(`    ${k}: ${v}`)
    return lines.join('\n')
  }).join('\n')
  if (dryRun) {
    console.log(`[dry-run] 将补 ${missingConfig.length} 条插件配置行：${missingConfig.map(([id]) => id).join(', ')}`)
  } else {
    if (!dryRun) copyFileSync(patchPath, `${patchPath}.bak-desktop-reinstall`)
    writeFileSync(patchPath, `${patchText.trimEnd()}\n\n${block}\n`, 'utf8')
    console.log(`已补插件配置行（备份：${patchPath}.bak-desktop-reinstall）：${missingConfig.map(([id]) => id).join(', ')}`)
  }
} else {
  console.log('插件配置行已存在，未改动')
}

// 2) 安装：PATH 里只加 node 目录（见文件头注释的 bash.exe 陷阱）
const env = { ...process.env, PATH: `${runtime.nodeBin};${process.env.PATH ?? ''}` }
if (dryRun) {
  console.log('[dry-run] 跳过安装')
  process.exit(0)
}
const result = spawnSync(
  runtime.node,
  [
    runtime.pnpm,
    'add',
    '--config.auto-install-peers=true',
    ...HOST_SDK.map((name) => `${name}@${HOST_SDK_VERSION}`),
  ],
  { cwd: profileDir, stdio: 'inherit', env },
)
console.log('install exit =', result.status)

// 2b) 校验宿主 SDK 包确实可解析（这是 WSL 变体预设能生成的前提）
const pluginEntry = join(nodeModules, 'dsh-wsl-workspace', 'lib', 'index.js')
if (existsSync(pluginEntry)) {
  const probeScript = join(profileDir, '.resolve-probe.mjs')
  const pkgs = [...HOST_SDK, '@deepseek-ai/schemastery', 'js-yaml']
  writeFileSync(
    probeScript,
    `import { createRequire } from 'node:module'\n`
    + `const req = createRequire(${JSON.stringify(pluginEntry)})\n`
    + `const bad = []\n`
    + `for (const p of ${JSON.stringify(pkgs)}) { try { req.resolve(p + '/package.json') } catch { bad.push(p) } }\n`
    + `console.log(bad.length === 0 ? 'RESOLVE_OK' : 'RESOLVE_FAIL ' + bad.join(','))\n`,
    'utf8',
  )
  const probe = spawnSync(runtime.node, [probeScript], { encoding: 'utf8' })
  console.log('依赖可解析性:', (probe.stdout ?? '').trim() || '(无输出)')
}

// 3) 自检
const audit = auditCoreCopies(nodeModules)
const coreVersions = Object.values(audit.versions)
const distinct = new Set(coreVersions)
console.log(`核心包 ${Object.keys(audit.versions).length} 个，版本种类 ${distinct.size}：`, [...distinct].join(', '))
if (audit.nested.length > 0) {
  console.error('✗ 存在嵌套核心包副本：', audit.nested.join('; '))
  process.exit(1)
}
const missingPlugins = entries.filter((e) => !existsSync(join(nodeModules, e.name, 'package.json')))
if (missingPlugins.length > 0) {
  console.error('✗ 插件未就位：', missingPlugins.map((p) => p.name).join(', '))
  process.exit(1)
}
for (const { name, version } of entries) {
  const installed = JSON.parse(readFileSync(join(nodeModules, name, 'package.json'), 'utf8')).version
  const flag = installed === version ? '✓' : '✗ 版本不符'
  console.log(`${flag} ${name}@${installed}（仓库 ${version}）`)
  if (installed !== version) process.exitCode = 1
}
console.log('完成。重启 DSH Desktop 后生效。')
