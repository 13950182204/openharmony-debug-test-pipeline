import type { Context } from '@deepseek-ai/cordis';
import type { SkillRegistration } from '@deepseek-ai/dsh-skill';
import type { Config } from './config.ts';
/**
 * 闭环编排 skill：把五个模块 skill 串成「修改-调试-测试」八阶段状态机。
 * 任意阶段可单独进入（模块化）；阶段产物通过流水线状态文件传递。
 */
export declare const LOOP_SKILL_NAME = "openharmony-debug-loop";
export declare function buildLoopSkill(config: Config, root?: string): SkillRegistration;
export declare function registerLoopSkill(ctx: Context, config: Config): void;
