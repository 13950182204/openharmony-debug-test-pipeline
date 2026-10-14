import type { AgentOptions } from '@deepseek-ai/dsh-agent';
/**
 * 六维审查子代理模型等级自动调节器。
 *
 * 原则（与用户确认的口径）：
 * - 档位只按模型区分（provider/model/maxTokens），reasoningEffort 是 provider
 *   全局配置，不能按子代理区分；
 * - 默认两档：high = deepseek-v4-pro，low = deepseek-v4-flash（下限）；
 * - 父代理已是下限档时全部子代理不降级（下限保护）；
 * - 简单任务（maintainability）降一档，复杂/中等任务不降级，保证审查质量；
 * - 档位表可配置（Config.reviewLadder），默认内置两档。
 */
export interface LadderTier {
    /** 档位名（high / low，可扩展更多档位） */
    name: string;
    provider?: string;
    model: string;
    maxTokens?: number;
}
/** 默认档位表：索引越小越强（0 = 最强） */
export declare const DEFAULT_LADDER: LadderTier[];
/** 六维审查类别 */
export declare const REVIEW_CATEGORIES: readonly ["security", "code-quality", "bug", "race", "test-stability", "maintainability"];
export type ReviewCategory = (typeof REVIEW_CATEGORIES)[number];
/**
 * 每类别的降档步数（0 = 不降级，1 = 降一档）。
 * 简单任务（可维护性）允许用低档模型；安全/缺陷/竞态等复杂推理类别不降级。
 */
export declare const CATEGORY_DOWNGRADE_STEPS: Record<ReviewCategory, number>;
/**
 * 探测父代理所在档位：精确匹配档位表 model；匹配不到时按模型名是否含 "pro"
 * 启发式归入最强档，其余（含未提供模型）归入最低档（保守）。
 * @returns 档位索引（0 = 最强）
 */
export declare function parentTierIndex(options: Pick<AgentOptions, 'provider' | 'model'> | undefined, ladder?: LadderTier[]): number;
/**
 * 按审查类别解析子代理档位索引。
 * @param category - 六维类别之一
 * @param parentIndex - 父代理档位索引
 * @param ladder - 档位表
 * @returns 子代理档位索引；父已在下限时不降级（下限保护）
 */
export declare function tierIndexForCategory(category: ReviewCategory, parentIndex: number, ladder?: LadderTier[]): number;
/** 取某档位的 AgentOptions（provider/model/maxTokens；未配置字段省略以走继承） */
export declare function agentOptionsForTier(tier: LadderTier | undefined): AgentOptions | undefined;
/** 解析配置中的档位表（校验非空、档位名唯一），无效时回退默认表 */
export declare function resolveLadder(configured: LadderTier[] | undefined): LadderTier[];
