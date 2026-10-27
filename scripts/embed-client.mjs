#!/usr/bin/env node
/**
 * 把 packages/gitlab-credentials 的**浏览器半侧**内嵌成 pipeline 包的 client half。
 *
 * DSH 的 client-modules 只把「说明符恰为包名」的 Loader 行当作该包的浏览器半侧，
 * 并且要求 bundle 内注册的 id 与包名一致；凭据包的 client bundle 由 tsdown 生成、
 * 注册 id 写死为它自己的包名，所以这里复制一份并把 id 改成 pipeline 的包名。
 * 源码仍是 packages/gitlab-credentials（独立发布也用它），内嵌产物只是构建输出。
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const SOURCE = join(ROOT, 'packages', 'gitlab-credentials', 'lib', 'client.js')
const OUTPUT = join(ROOT, 'lib', 'client.js')
const PACKAGE_ID = 'openharmony-debug-test-pipeline'
const COMPONENT_ID = '@superjunier/dsh-gitlab-credentials'

if (!existsSync(SOURCE)) {
  console.error(`embed-client: 缺少凭据包的 client bundle：${SOURCE}`)
  console.error('  （先在该目录 pnpm build；仓库里提交了 lib/，通常是有的）')
  process.exit(1)
}
const source = readFileSync(SOURCE, 'utf8')
const needle = `id: "${COMPONENT_ID}"`
if (!source.includes(needle)) {
  console.error(`embed-client: ${SOURCE} 里找不到注册 id ${COMPONENT_ID}——凭据包的构建格式变了，需同步更新本脚本`)
  process.exit(1)
}
const output = source
  .replace(needle, `id: "${PACKAGE_ID}"`)
  .replaceAll('[dsh-gitlab-credentials]', '[openharmony-debug-test-pipeline]')
writeFileSync(OUTPUT, output)
console.log(`embed-client: 已生成 ${OUTPUT}（ModuleLoader id = ${PACKAGE_ID}）`)
