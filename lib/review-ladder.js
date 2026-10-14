/** 默认档位表：索引越小越强（0 = 最强） */
export const DEFAULT_LADDER = [
    { name: 'high', provider: 'deepseek-official', model: 'deepseek-v4-pro' },
    { name: 'low', provider: 'deepseek-official', model: 'deepseek-v4-flash' },
];
/** 六维审查类别 */
export const REVIEW_CATEGORIES = [
    'security',
    'code-quality',
    'bug',
    'race',
    'test-stability',
    'maintainability',
];
/**
 * 每类别的降档步数（0 = 不降级，1 = 降一档）。
 * 简单任务（可维护性）允许用低档模型；安全/缺陷/竞态等复杂推理类别不降级。
 */
export const CATEGORY_DOWNGRADE_STEPS = {
    security: 0,
    'code-quality': 0,
    bug: 0,
    race: 0,
    'test-stability': 0,
    maintainability: 1,
};
/**
 * 探测父代理所在档位：精确匹配档位表 model；匹配不到时按模型名是否含 "pro"
 * 启发式归入最强档，其余（含未提供模型）归入最低档（保守）。
 * @returns 档位索引（0 = 最强）
 */
export function parentTierIndex(options, ladder = DEFAULT_LADDER) {
    if (ladder.length === 0)
        throw new Error('reviewLadder 不能为空');
    if (!options?.model)
        return ladder.length - 1;
    const exact = ladder.findIndex((tier) => tier.model === options.model);
    if (exact >= 0)
        return exact;
    return options.model.toLowerCase().includes('pro') ? 0 : ladder.length - 1;
}
/**
 * 按审查类别解析子代理档位索引。
 * @param category - 六维类别之一
 * @param parentIndex - 父代理档位索引
 * @param ladder - 档位表
 * @returns 子代理档位索引；父已在下限时不降级（下限保护）
 */
export function tierIndexForCategory(category, parentIndex, ladder = DEFAULT_LADDER) {
    const steps = CATEGORY_DOWNGRADE_STEPS[category] ?? 0;
    return Math.min(parentIndex + steps, ladder.length - 1);
}
/** 取某档位的 AgentOptions（provider/model/maxTokens；未配置字段省略以走继承） */
export function agentOptionsForTier(tier) {
    if (!tier)
        return undefined;
    return {
        ...(tier.provider ? { provider: tier.provider } : {}),
        ...(tier.model ? { model: tier.model } : {}),
        ...(tier.maxTokens ? { maxTokens: tier.maxTokens } : {}),
    };
}
/** 解析配置中的档位表（校验非空、档位名唯一），无效时回退默认表 */
export function resolveLadder(configured) {
    if (!configured || configured.length === 0)
        return DEFAULT_LADDER;
    const names = new Set(configured.map((tier) => tier.name));
    if (names.size !== configured.length)
        return DEFAULT_LADDER;
    return configured;
}
