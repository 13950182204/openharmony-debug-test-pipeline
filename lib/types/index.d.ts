import type { Context } from '@deepseek-ai/cordis';
import { Config } from './config.ts';
/**
 * OpenHarmony 修改-调试-测试闭环 DSH 插件。
 *
 * 运行时插件（bundle 聚合包形态，无 client 半身）：
 * - 注册五个 vendored 模块 skill（glab-mr-submit / karpathy-guidelines /
 *   openharmony-ci-orchestrator / openharmony-ota-upgrade /
 *   openharmony-test-report-triage），每个均可独立调用；
 * - 注册一个闭环编排 skill（openharmony-debug-loop），八阶段状态机串联各模块；
 * - 注册 /pipeline status|reset 命令，跨会话查询/重置流水线状态；
 * - 注册 mr_review_six 工具：六维 MR 审查一键执行，子代理模型按类别自动分级
 *   （复杂类不降级、maintainability 降一档、下限为档位表最低档）。
 *
 * 宿主可以是 Windows（DSH Desktop）或 WSL/Linux：插件本身只注册 skill/命令/工具，
 * 真正执行命令的是会话的 bash 工具，因此 skill 正文里的路径按「模型口径」渲染
 * （见 wsl-paths.ts），Windows 宿主下由 config.wslPackageRoot 指定。
 */
export declare const name = "openharmony-debug-test-pipeline";
export declare const inject: string[];
export { Config };
export declare function apply(ctx: Context, config?: Config): Promise<void>;
