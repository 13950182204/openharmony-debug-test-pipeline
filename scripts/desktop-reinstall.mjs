#!/usr/bin/env node
/**
 * 在 **Windows 侧**把本插件（含内嵌 GitLab 凭据子组件）重装进 DSH Desktop 的
 * desktop profile。用法（在 Windows 上跑，Node 任意 20+）：
 *
 *   node <repo>/scripts/desktop-reinstall.mjs [--profile <名字>] [--dry-run]
 *
 * 为什么需要这个脚本，而不是直接在 profile 目录 `pnpm add`：
 *
 * 1. **PATH 必须显式补上 node 目录**。DSH Desktop 自带的 pnpm 在派生子进程时不会把
 *    node 放进子环境 PATH，两包安装/打包阶段可能因此报 `'node' 不是内部或外部命令`。
 *    只补 `…/dependencies/node/bin`，**绝不把 Node 根目录加进 PATH**——Node 根目录
 *    同时含 `bash.exe`/`wsl.exe`，一旦进入 PATH 会顶掉 WSL 的 bash 与 wsl.exe，
 *    而 Windows 侧 bash.exe 对 `\\\\wsl.localhost\\…` 路径无法工作，会连带打断会话里的 bash 工具。
 *
 * 2. **绝不要把宿主 SDK（`@deepseek-ai/dsh-*`）装进 profile**。DSH 从 0.2.1-rc.1 起
 *    在 profile 上装了模块解析拦截层：插件代码解析宿主 SDK 时，会回落到 **安装目录**
 *    （app.asar）里的那一份。反过来，只要 profile 的 node_modules 里出现同名包，
 *    解析就优先落到 profile 副本——核心插件（`tools` 等）会解析到另一个模块实例，
 *    服务 Symbol 不一致，启动直接失败（实测 0.2.1-rc.3：`tools` 服务起不来，
 *    17 个插件挂在 “Missing services: tools”）。
 *    本插件因此只把 `@deepseek-ai/dsh-tools` 声明为 **peerDependency**（构建用的
 *    精确版本在 devDependencies），安装时用 `--config.auto-install-peers=false`，
 *    由宿主提供运行时副本。脚本安装后强制审计，profile 里出现 dsh-tools 即失败。
 *
 * 3. 安装完成后 profile 的 `dsh.profile.bundles` 必须含这两个包名，否则不会加载。
 *    脚本会校正并打印最终清单。
 *
 * 4. 配置行（`wslPackageRoot` / `stateFile`）缺失时补回默认值；存在则不动。
 */

import { spawnSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/**
 * 两个分发入口。**版本从各自 package.json 现读**，不写死——写死版本会在
 * 仓库升版后让助手去找一个不存在的 tarball 文件名。
 *
 * `dir` 是打包目录；tarball 文件名沿用 pnpm pack 的规则（scope 用 `-` 连接）。
 */
const PLUGINS = [
  // 1.5.0 起 GitLab 凭据是本包的内嵌子组件（packages/gitlab-credentials/lib 随包分发），
  // 不再单独打包/安装第二个插件。
  { name: 'openharmony-debug-test-pipeline', dir: REPO },
]

/** 宿主提供、**绝不能**出现在 profile 依赖里的核心包（出现即覆盖宿主插件/服务）。 */
const FORBIDDEN_IN_PROFILE = [
  '@deepseek-ai/dsh-tools',
  '@deepseek-ai/dsh-agent',
  '@deepseek-ai/dsh-llm',
  '@deepseek-ai/dsh-session',
  '@deepseek-ai/dsh-skill',
  '@deepseek-ai/dsh-commands',
  '@deepseek-ai/dsh-subagent',
]

/** pnpm pack 对带 scope 的包名所用的 tarball 文件名。 */
function tgzName(name, version) {
  return `${name.replace(/^@/, '').replace(/\//g, '-')}-${version}.tgz`
}

/** 解析 DSH Desktop 自带的 runtime 目录（含 node 与 pnpm）。 */
function resolveRuntime() {
  const candidates = [
    join(process.env.LOCALAPPDATA ?? '', 'Programs', 'DeepSeek Harness', 'resources', 'runtime'),
    join('C:', 'Users', process.env.USERNAME ?? '', 'AppData', 'Local', 'Programs', 'DeepSeek Harness', 'resources', 'runtime'),
    join('D:', 'dsh', 'resources', 'runtime'),
  ]
  for (const base of candidates) {
    if (base === '' || base.startsWith(':')) continue
    const node = join(base, 'primary-runtime', 'dependencies', 'node', 'bin', 'node.exe')
    const pnpm = join(base, 'primary-runtime', 'dependencies', 'pnpm', 'bin', 'pnpm.cjs')
    if (existsSync(node) && existsSync(pnpm)) return { node, pnpm, nodeBin: dirname(node) }
  }
  return undefined
}

/** profile 里出现即判失败的宿主核心包。 */
function auditHostSdk(nodeModules) {
  const scope = join(nodeModules, '@deepseek-ai')
  const found = []
  if (!existsSync(scope)) return found
  for (const name of readdirSync(scope)) {
    if (FORBIDDEN_IN_PROFILE.includes(`@deepseek-ai/${name}`)) found.push(name)
  }
  return found
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
//    `~/.dsh/plugins` 被清理，助手仍是一条命令可恢复。
const entries = PLUGINS.map(({ name, dir }) => {
  const manifest = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
  return { name, dir, version: manifest.version, tgz: tgzName(name, manifest.version) }
})
for (const entry of entries) {
  const path = join(storeDir, entry.tgz)
  if (existsSync(path)) continue
  console.log(`tarball 缺失，重新打包：${entry.tgz}`)
  if (dryRun) continue
  const r = spawnSync(runtime.node, [runtime.pnpm, 'pack', '--pack-destination', storeDir], {
    cwd: entry.dir, stdio: 'inherit', timeout: 400000,
  })
  if (r.status !== 0 || !existsSync(path)) {
    console.error(`✗ 打包失败：${entry.name}（exit ${r.status}）`)
    process.exit(1)
  }
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
// 1.5.0 起凭据是内嵌子组件：清掉旧版单独安装留下的依赖与 bundles 选中项。
Reflect.deleteProperty(manifest.dependencies, '@superjunier/dsh-gitlab-credentials')
const credentialBundleIndex = bundles.indexOf('@superjunier/dsh-gitlab-credentials')
if (credentialBundleIndex >= 0) bundles.splice(credentialBundleIndex, 1)
if (dryRun) {
  console.log(`[dry-run] 清单将登记 ${entries.length} 个插件，bundles=${bundles.length}`)
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
    lines: [
      '  config:',
      '    wslPackageRoot: /home/cx/os/openharmony-debug-test-pipeline',
      '    stateFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json',
      '    stateFileModel: /home/cx/.dsh/pipeline-state.json',
      '    credentials:',
      '      storeFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/gitlab-credentials.json',
      '      announceToAgent: false',
    ],
  },
}
const patchText = existsSync(patchPath) ? readFileSync(patchPath, 'utf8') : ''
const missingConfig = Object.entries(PLUGIN_CONFIG).filter(([id]) => !patchText.includes(`id: ${id}`))
if (missingConfig.length > 0) {
  const block = missingConfig.map(([id, spec]) => ['- id: ' + id, '  name: "' + spec.name + '"', ...spec.lines].join('\n')).join('\n')
  if (dryRun) {
    console.log('[dry-run] 将补 ' + missingConfig.length + ' 条插件配置行：' + missingConfig.map(([id]) => id).join(', '))
  } else {
    copyFileSync(patchPath, patchPath + '.bak-desktop-reinstall')
    writeFileSync(patchPath, patchText.trimEnd() + '\n\n' + block + '\n', 'utf8')
    console.log('已补插件配置行（备份：' + patchPath + '.bak-desktop-reinstall）：' + missingConfig.map(([id]) => id).join(', '))
  }
} else {
  console.log('插件配置行已存在，未改动')
}

// 2) 安装：PATH 里只加 node 目录（见文件头注释的 bash.exe 陷阱），不自动装 peer。
const env = { ...process.env, PATH: `${runtime.nodeBin};${process.env.PATH ?? ''}` }
if (dryRun) {
  console.log('[dry-run] 跳过安装')
  process.exit(0)
}
const result = spawnSync(
  runtime.node,
  [runtime.pnpm, 'install', '--config.auto-install-peers=false'],
  { cwd: profileDir, stdio: 'inherit', env },
)
console.log('install exit =', result.status)
if (result.status !== 0) {
  console.error('✗ 安装失败：profile 的 package.json / pnpm-lock.yaml 已由 pnpm 自己维护；先修网络或依赖声明再重跑。')
  process.exit(1)
}

// 3) 自检：宿主核心包不得进入 profile（否则会覆盖 app.asar 里的同名插件）
const forbidden = auditHostSdk(nodeModules)
if (forbidden.length > 0) {
  console.error('✗ profile 里出现了宿主核心包（会覆盖运行时插件、导致 tools 服务起不来）：', forbidden.join(', '))
  console.error('  删掉它们并重新安装：pnpm remove ' + forbidden.map((name) => `@deepseek-ai/${name}`).join(' '))
  process.exit(1)
}

// 内嵌子组件必须随包落下：宿主半侧 + 本包 client half。
const embeddedLib = join(nodeModules, 'openharmony-debug-test-pipeline', 'packages', 'gitlab-credentials', 'lib')
for (const file of ['index.js', 'client.js']) {
  if (!existsSync(join(embeddedLib, file))) {
    console.error('✗ 内嵌凭据子组件未就位：', join(embeddedLib, file))
    process.exit(1)
  }
}
if (!existsSync(join(nodeModules, 'openharmony-debug-test-pipeline', 'lib', 'client.js'))) {
  console.error('✗ 本包 client half 未就位：', join(nodeModules, 'openharmony-debug-test-pipeline', 'lib', 'client.js'))
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

// 4) 组合校验：dump-config 不启动插件，但会把 bundles 组合成完整树；
//    能跑通说明 profile 清单与 patch 层没有结构错误。
const dshCmd = spawnSync('dsh', ['--profile', profile, '--dump-config'], { encoding: 'utf8', timeout: 180000 })
if (dshCmd.error !== undefined) {
  console.warn('⚠ 未找到 dsh CLI，跳过 --dump-config 校验（可手动执行：dsh --profile ' + profile + ' --dump-config）')
} else if (dshCmd.status !== 0) {
  console.error('✗ dsh --dump-config 失败：\n' + (dshCmd.stderr || dshCmd.stdout))
  process.exit(1)
} else {
  console.log('✓ dsh --dump-config 通过（bundles/patch 结构可用）')
}

console.log('完成。**重启 DSH Desktop** 后生效；启动后在 设置 → 插件 里确认 oh-debug-pipeline 已加载。')
