import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { load as loadYaml } from 'js-yaml'
import type { Context } from '@deepseek-ai/cordis'
import type { SkillRegistration } from '@deepseek-ai/dsh-skill'

/**
 * vendored skill 注册器：把插件包内 skills/ 目录下的五个 OpenHarmony
 * 调测 skill 注册进 DSH 的 ctx.skills 注册表（source: 'bundled'）。
 *
 * DSH 定制：skill 正文中由定制阶段写入的 {{SKILLS_DIR}} 占位符在加载时
 * 被替换为插件包 skills/ 目录的真实绝对路径（vendored 后脚本按该目录
 * 解析），{{PIPELINE_SCRIPT}} 被替换为流水线状态脚本的绝对路径。
 */

/** 随插件 vendored 的模块 skill 清单（保持与仓库 skills/ 目录一致） */
export const VENDORED_SKILLS = [
  'glab-mr-submit',
  'karpathy-guidelines',
  'openharmony-ci-orchestrator',
  'openharmony-ota-upgrade',
  'openharmony-test-report-triage',
] as const

const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/

export interface ParsedSkill {
  name: string
  description: string
  whenToUse?: string
  content: string
}

/**
 * 解析 SKILL.md：提取 YAML frontmatter（name/description/whenToUse），
 * 正文去掉 frontmatter 块。格式与 DSH 官方 skill 文件系统 provider 兼容。
 */
export function parseSkillFrontmatter(raw: string): ParsedSkill | undefined {
  const match = FRONTMATTER_RE.exec(raw)
  if (!match) return undefined
  let meta: Record<string, unknown>
  try {
    const parsed = loadYaml(match[1])
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined
    meta = parsed as Record<string, unknown>
  } catch {
    return undefined
  }
  const { name, description, whenToUse } = meta
  if (typeof name !== 'string' || !name || typeof description !== 'string' || !description) {
    return undefined
  }
  const content = raw.slice(match[0].length).trim() + '\n'
  return {
    name,
    description,
    whenToUse: typeof whenToUse === 'string' && whenToUse ? whenToUse : undefined,
    content,
  }
}

/**
 * 替换 skill 正文中的占位符并追加资源说明尾注。
 * @param raw - SKILL.md 原始内容
 * @param skillsDir - 插件包 skills/ 目录绝对路径
 * @param scriptPath - 流水线状态脚本绝对路径
 */
export function buildSkillContent(raw: string, skillsDir: string, scriptPath: string): string {
  const content = raw
    .replaceAll('{{SKILLS_DIR}}', skillsDir)
    .replaceAll('{{PIPELINE_SCRIPT}}', scriptPath)
  return (
    content +
    `\n\n## 技能资源\n\n` +
    `- 技能目录: ${skillsDir}\n` +
    `- 脚本、参考文档等资源位于技能目录下的 scripts/、references/、assets/ 子目录（相对本文件引用均基于技能目录解析）。\n`
  )
}

/** 插件包根目录（lib/vendor.js → 包根） */
export function packageRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), '..')
}

/**
 * 注册全部 vendored skill。某个 skill 缺失或 frontmatter 非法时记警告并
 * 跳过，不阻断其余 skill 与插件整体加载。
 * @param root - 插件包根目录（测试可注入临时目录）
 */
export function registerVendoredSkills(
  ctx: Context,
  config: { stateFile: string },
  root: string = packageRoot(),
): void {
  const skillsDir = join(root, 'skills')
  const scriptPath = join(root, 'scripts', 'pipeline_state.py')
  for (const name of VENDORED_SKILLS) {
    const skillDir = join(skillsDir, name)
    const skillFile = join(skillDir, 'SKILL.md')
    if (!existsSync(skillFile)) {
      ctx.logger.warn(`openharmony-debug-test-pipeline: vendored skill 缺失，跳过: ${skillFile}`)
      continue
    }
    const parsed = parseSkillFrontmatter(readFileSync(skillFile, 'utf8'))
    if (!parsed) {
      ctx.logger.warn(`openharmony-debug-test-pipeline: SKILL.md frontmatter 非法，跳过: ${skillFile}`)
      continue
    }
    const registration: SkillRegistration = {
      name: parsed.name,
      description: parsed.description,
      whenToUse: parsed.whenToUse,
      content: buildSkillContent(parsed.content, skillsDir, scriptPath),
      path: skillFile,
      source: 'bundled',
      resourceBase: { kind: 'directory', path: skillDir },
    }
    ctx.skills.register(registration)
    ctx.logger.info(`openharmony-debug-test-pipeline: 已注册 skill ${parsed.name}`)
  }
}
