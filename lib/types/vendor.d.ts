import type { Context } from '@deepseek-ai/cordis';
import type { Config } from './config.ts';
/**
 * vendored skill 注册器：把插件包内 skills/ 目录下的五个 OpenHarmony
 * 调测 skill 注册进 DSH 的 ctx.skills 注册表（source: 'bundled'）。
 *
 * DSH 定制：skill 正文中由定制阶段写入的 {{SKILLS_DIR}} 占位符在加载时
 * 被替换为插件包 skills/ 目录的真实绝对路径（vendored 后脚本按该目录
 * 解析），{{PIPELINE_SCRIPT}} 被替换为流水线状态脚本的绝对路径。
 */
/** 随插件 vendored 的模块 skill 清单（保持与仓库 skills/ 目录一致） */
export declare const VENDORED_SKILLS: readonly ["glab-mr-submit", "karpathy-guidelines", "openharmony-ci-orchestrator", "openharmony-ota-upgrade", "openharmony-test-report-triage"];
export interface ParsedSkill {
    name: string;
    description: string;
    whenToUse?: string;
    content: string;
}
/**
 * 解析 SKILL.md：提取 YAML frontmatter（name/description/whenToUse），
 * 正文去掉 frontmatter 块。格式与 DSH 官方 skill 文件系统 provider 兼容。
 */
export declare function parseSkillFrontmatter(raw: string): ParsedSkill | undefined;
/**
 * 替换 skill 正文中的占位符并追加资源说明尾注。
 * @param raw - SKILL.md 原始内容
 * @param skillsDir - 插件包 skills/ 目录绝对路径
 * @param scriptPath - 流水线状态脚本绝对路径
 */
export declare function buildSkillContent(raw: string, skillsDir: string, scriptPath: string): string;
/** 插件包根目录（lib/vendor.js → 包根） */
export declare function packageRoot(): string;
/**
 * 注册全部 vendored skill。某个 skill 缺失或 frontmatter 非法时记警告并
 * 跳过，不阻断其余 skill 与插件整体加载。
 * @param root - 插件包根目录（测试可注入临时目录）
 */
export declare function registerVendoredSkills(ctx: Context, config: Pick<Config, 'stateFile' | 'wslPackageRoot' | 'stateFileModel'>, root?: string): void;
