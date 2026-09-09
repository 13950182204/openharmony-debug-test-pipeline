import { readFileSync, existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Context } from '@deepseek-ai/cordis'
import type { SkillRegistration } from '@deepseek-ai/dsh-skill'

/**
 * 本插件的 vendored skill:GitLab Issue 驱动看板流水线编排。
 * 与父插件同构:skills/<name>/SKILL.md,frontmatter 提取 name/description/whenToUse,
 * 正文中的 {{ISSUE_SCRIPT}} 与 {{PARENT_PLUGIN_DIR}} 占位符在加载时替换为真实路径。
 */

export const ISSUE_SKILL_NAME = 'openharmony-issue-pipeline'

/** 插件包根目录(lib/vendor.js → 包根) */
export function packageRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), '..')
}

export interface ParsedSkill {
  name: string
  description: string
  whenToUse?: string
  content: string
}

const FRONTMATTER_RE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/
const SCALAR_RE = /^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$/

/**
 * 解析 SKILL.md frontmatter。仅支持单行标量(name/description/whenToUse;
 * 双引号或裸值),本插件 SKILL.md 均由我们编写,无需完整 YAML。
 */
export function parseSkillFrontmatter(raw: string): ParsedSkill | undefined {
  const match = FRONTMATTER_RE.exec(raw)
  if (!match) return undefined
  const meta: Record<string, unknown> = {}
  for (const line of match[1].split(/\r?\n/)) {
    const lineMatch = SCALAR_RE.exec(line.trim())
    if (!lineMatch) continue
    const value = lineMatch[2].trim()
    const unquoted = /^"(.*)"$/.exec(value)
    meta[lineMatch[1]] = unquoted ? unquoted[1] : value
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

/** 替换正文占位符并追加资源说明尾注。 */
export function buildSkillContent(
  raw: string,
  skillsDir: string,
  scriptPath: string,
  parentPluginDir: string,
): string {
  const content = raw
    .replaceAll('{{ISSUE_SCRIPT}}', scriptPath)
    .replaceAll('{{PARENT_PLUGIN_DIR}}', parentPluginDir)
  return (
    content +
    `\n\n## 技能资源\n\n` +
    `- 技能目录: ${skillsDir}\n` +
    `- 流水线脚本: ${scriptPath}\n` +
    `- 父插件目录: ${parentPluginDir}(其 skills/ 与 scripts/pipeline_state.py 可被本 skill 各阶段引用)\n`
  )
}

/**
 * 注册 vendored skill。缺失或 frontmatter 非法时记警告并跳过,不阻断插件加载。
 */
export function registerVendoredSkills(
  ctx: Context,
  config: { parentPluginDir: string },
  root: string = packageRoot(),
): void {
  const skillsDir = join(root, 'skills')
  const scriptPath = join(root, 'scripts', 'issue_pipeline.py')
  const skillDir = join(skillsDir, ISSUE_SKILL_NAME)
  const skillFile = join(skillDir, 'SKILL.md')
  if (!existsSync(skillFile)) {
    ctx.logger.warn(`openharmony-issue-pipeline: vendored skill 缺失，跳过: ${skillFile}`)
    return
  }
  const parsed = parseSkillFrontmatter(readFileSync(skillFile, 'utf8'))
  if (!parsed) {
    ctx.logger.warn(`openharmony-issue-pipeline: SKILL.md frontmatter 非法，跳过: ${skillFile}`)
    return
  }
  const registration: SkillRegistration = {
    name: parsed.name,
    description: parsed.description,
    whenToUse: parsed.whenToUse,
    content: buildSkillContent(parsed.content, skillsDir, scriptPath, config.parentPluginDir),
    path: skillFile,
    source: 'bundled',
    resourceBase: { kind: 'directory', path: skillDir },
  }
  ctx.skills.register(registration)
  ctx.logger.info(`openharmony-issue-pipeline: 已注册 skill ${parsed.name}`)
}
