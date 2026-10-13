import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * link-runtime-dsh-tools.mjs 的守卫回归。
 *
 * 针对一次真实故障：1.1.9 把 `@deepseek-ai/dsh-tools` 从 devDependencies 挪到
 * dependencies 后，脚本仍只读 devDependencies → 读到 undefined → 版本守卫整体失效，
 * 无条件把依赖树链到活动运行时。实测后果是仓库里 0.1.2-rc.1 的 dsh-tools 与
 * 0.2.0-rc.2 的 dsh-session/dsh-agent 混用，`pnpm run typecheck` 报
 * `Property '[BRAND]' is missing`。
 *
 * 这里用源文本断言锁住两个关键事实（避免再退化）：
 * 1. 声明版本必须同时读 dependencies 与 devDependencies；
 * 2. 清理判据必须是"任何指向 ~/.dsh 的运行时链接"，而不能要求它等于当前 runtime 位置
 *    （旧写法在版本不一致时判据恒为 false，导致"跳过重链"却把错误世代留在原地）。
 */

const SOURCE = readFileSync(
  new URL('../scripts/link-runtime-dsh-tools.mjs', import.meta.url),
  'utf8',
)

describe('link-runtime-dsh-tools 守卫', () => {
  it('声明版本同时读 dependencies 与 devDependencies', () => {
    expect(SOURCE).toContain("manifest.dependencies?.['@deepseek-ai/dsh-tools']")
    expect(SOURCE).toContain("?? manifest.devDependencies?.['@deepseek-ai/dsh-tools']")
  })

  it('清理判据是"任何指向 dshHome 的链接"，不再要求等于当前 runtime 位置', () => {
    // 识别函数存在且只依赖 dshHome 前缀
    expect(SOURCE).toMatch(/function isRuntimeLink\(\)[\s\S]*?realpathSync\(target\)\.startsWith\(dshHome\)/)
    // 旧写法（把 realpath 与 source 比较当清理前提）不得存在
    expect(SOURCE).not.toMatch(/resolvedTarget !== realpathSync\(source\)/)
  })

  it('版本不一致时优先清理运行时链接并交回 pnpm', () => {
    expect(SOURCE).toContain('const versionMismatch = declared !== undefined && declared !== runtime.version')
    expect(SOURCE).toContain('if (versionMismatch || (declared === undefined && isRuntimeLink()))')
  })

  it('Windows 宿主下整体跳过（Symbol 一致性要求只对 Linux/WSL 侧成立）', () => {
    expect(SOURCE).toContain("if (process.platform === 'win32')")
  })
})
