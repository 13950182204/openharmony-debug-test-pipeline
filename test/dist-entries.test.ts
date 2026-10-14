import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * 分发入口的构建产物必须**入库**。
 *
 * 针对一次实测故障：用 GitHub 仓库地址安装插件后，装出来的包**没有 `lib/`**
 * （`lib/` 是 tsc 产物且被 `.gitignore` 忽略，而 git 安装不会构建），于是
 * `main: lib/index.js` 指向不存在的文件、插件静默加载失败。
 *
 * 而 `prepare` 方案在 pnpm 下三重不可行（实测）：
 *   ① `ERR_PNPM_GIT_DEP_PREPARE_NOT_ALLOWED`（git 宿主的包必须显式进 allowBuilds）；
 *   ② 放行后 pnpm 要跑 `npm install` 装构建依赖，安装环境里通常没有 npm
 *      （`ERR_PNPM_PREPARE_PACKAGE: npm-install`）；
 *   ③ allowBuilds 键带 commit 哈希，每次都要改。
 *
 * 因此把产物提交进仓库是唯一可行路径，本用例守住它不被再次忽略或漏提交。
 */

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/** 断言某个包的 `main`/`types` 指向真实存在的文件。 */
function assertEntryPoints(packageDir: string, label: string): void {
  const manifest = JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8'))
  const candidates = [manifest.main, manifest.types].filter((v): v is string => typeof v === 'string')
  expect(candidates.length, `${label}: package.json 未声明 main/types`).toBeGreaterThan(0)
  for (const rel of candidates) {
    const abs = join(packageDir, rel.replace(/^\.\//, ''))
    expect(existsSync(abs), `${label}: 入口文件缺失（产物未入库？）${rel}`).toBe(true)
  }
}

describe('分发入口产物入库', () => {
  it('主包 lib/ 入库且入口可解析', () => {
    assertEntryPoints(repoRoot, 'openharmony-debug-test-pipeline')
  })

  it('子包 lib/ 入库且入口可解析', () => {
    assertEntryPoints(join(repoRoot, 'packages', 'gitlab-credentials'), '@superjunier/dsh-gitlab-credentials')
  })

  it('子包 client 半边（浏览器面板）也在仓库里', () => {
    const client = join(repoRoot, 'packages', 'gitlab-credentials', 'lib', 'client.js')
    expect(existsSync(client), 'client.js 缺失：桌面 GUI 的「GitLab 凭据」页会消失').toBe(true)
  })
})
