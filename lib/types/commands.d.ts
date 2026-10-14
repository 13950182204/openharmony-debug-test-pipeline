import type { Context } from '@deepseek-ai/cordis';
/**
 * /pipeline 命令：查询与重置闭环流水线状态。
 *   /pipeline status  - 显示当前阶段、产物与最近事件
 *   /pipeline reset   - 重置状态（需在输入中明确写 reset）
 */
export declare function registerPipelineCommands(ctx: Context, stateFile: string): void;
