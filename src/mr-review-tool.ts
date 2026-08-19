import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import type { SubagentStartRequest } from '@deepseek-ai/dsh-subagent'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ContentBlock } from '@deepseek-ai/dsh-llm'
import {
  agentOptionsForTier,
  parentTierIndex,
  tierIndexForCategory,
  resolveLadder,
  REVIEW_CATEGORIES,
  type LadderTier,
  type ReviewCategory,
} from './review-ladder.ts'
import type { Config } from './config.ts'

/**
 * mr_review_six 工具：六维 MR 审查一键执行。
 *
 * 替代模型手工并行创建六个审查子代理：工具内部自动——
 * 1. 探测父代理模型档位；
 * 2. 按审查类别自动分级子代理模型（复杂类不降级、简单类降一档、下限保护）；
 * 3. 并行 spawn 六个只读审查子代理（prompt 内联类别边界与发现契约）；
 * 4. 失败类别用上一档模型重试一次（质量护栏）；
 * 5. 聚合结果返回。
 */

/** 每类别的边界描述（与 skills/glab-mr-submit/references/mr-review.md 对齐） */
const CATEGORY_BOUNDARIES: Record<ReviewCategory, { report: string; exclude: string }> = {
  security: {
    report: '权限绕过、敏感数据暴露、不安全的输入/路径处理、注入、资源耗尽与安全回归',
    exclude: '无安全影响的泛泛健壮性或风格问题',
  },
  'code-quality': {
    report: '错误的 API 契约、错误处理、资源生命周期、兼容性与有风险的本地设计',
    exclude: '纯命名或格式偏好',
  },
  bug: {
    report: '可观察的功能回归、错误状态迁移、边界错误与损坏的用户/测试行为',
    exclude: '无具体触发条件的理论问题',
  },
  race: {
    report: '异步顺序、生命周期回调、并发读-改-写、陈旧事件与清理交错',
    exclude: '普通顺序逻辑错误',
  },
  'test-stability': {
    report: '不稳定时序、非确定性顺序、状态泄漏、不完整清理、设备/环境耦合与重复运行失败',
    exclude: '除非直接破坏测试稳定，否则不报产品行为缺陷',
  },
  maintainability: {
    report: '重复、隐藏耦合、归属不清、脆弱的测试数据，以及可能造成未来缺陷或高维护成本的改动',
    exclude: '主观风格偏好与低影响重构',
  },
}

function buildReviewPrompt(args: MrReviewArgs, category: ReviewCategory): string {
  const boundary = CATEGORY_BOUNDARIES[category]
  const paths = args.changedPaths?.length
    ? args.changedPaths.join('\n')
    : '（未提供，请用 git diff --name-status <baseSha>...<headSha> 获取精确变更路径清单）'
  const lines = [
    `你是 OpenHarmony MR 六维审查中「${category}」维度的审查子代理。只读任务：`,
    `绝不修改任何文件、不提交、不推送、不发 MR 评论、不清理 worktree。`,
    ``,
    `审查对象：仓库 ${args.repoDir}，权威 diff 为 base_sha...head_sha（${args.baseSha}...${args.headSha}）。`,
    args.sourceBranch ? `源分支：${args.sourceBranch}。` : '',
    args.targetBranch ? `目标分支：${args.targetBranch}。` : '',
    args.mrIid ? `MR IID：${args.mrIid}。` : '',
    ``,
    `精确变更路径清单：`,
    paths,
    ``,
    `类别边界（${category} 维度）：`,
    `- 报告：${boundary.report}`,
    `- 不要报告：${boundary.exclude}`,
    ``,
    `审查整个 diff 及必要的基线上下文。每个发现必须给出：`,
    `severity (P0/P1/P2/P3)、confidence (high/medium/low)、绝对文件路径、`,
    `基于 1 的行号、简短标题、触发条件、证据、影响、最小修复建议。`,
    `只报告有证据支撑的发现；无发现时只输出一行：no findings`,
  ].filter(Boolean)
  return lines.join('\n')
}

function extractText(output: ContentBlock[]): string {
  return output
    .filter((block): block is Extract<ContentBlock, { type: 'text' }> => block.type === 'text')
    .map((block) => block.text)
    .join('\n')
}

export interface MrReviewArgs {
  repoDir: string
  baseSha: string
  headSha: string
  changedPaths?: string[]
  sourceBranch?: string
  targetBranch?: string
  mrIid?: number
  mrUrl?: string
  categories?: ReviewCategory[]
  retryOnFail?: boolean
}

async function runReviewer(
  ctx: Context,
  parent: Agent,
  signal: AbortSignal,
  args: MrReviewArgs,
  category: ReviewCategory,
  tier: LadderTier | undefined,
  ladder: LadderTier[],
): Promise<{ ok: boolean; text: string; tierName: string }> {
  const run = await ctx.subagents.start(`mr-review-${category}`, {
    prompt: [{ type: 'text', text: buildReviewPrompt(args, category) }],
    parent,
    signal,
    agentOptions: agentOptionsForTier(tier),
  })
  const result = await run.result
  const text = extractText(result.output)
  if (result.stopReason !== 'completed') {
    return { ok: false, text: `子代理未正常完成（stopReason=${result.stopReason}）`, tierName: tier?.name ?? '继承' }
  }
  return { ok: true, text, tierName: tier?.name ?? '继承' }
}

export function registerMrReviewTool(ctx: Context, config: Config): void {
  const ladder = resolveLadder(config.reviewLadder)

  ctx.tools.register(defineTool({
    name: 'mr_review_six',
    description:
      '对既有 MR 或本地 diff 执行六维并行审查（security/code-quality/bug/race/test-stability/maintainability）。'
      + '内部自动按审查类别分级子代理模型（复杂类不降级，maintainability 降一档，下限为档位表最低档；'
      + '父代理已是最低档时全部子代理同档），并行创建只读审查子代理，失败类别自动升档重试一次，聚合结果返回。'
      + '审查后如需修复，由主 agent 按 glab-mr-submit skill 的修复流程处理。',
    parameters: {
      repoDir: { type: 'string', required: true, description: '仓库绝对路径（如 /home/cx/os/worktrees/<slug>）' },
      baseSha: { type: 'string', required: true, description: '权威 diff 的 base_sha（MR diff_refs.base_sha 或本地 git merge-base）' },
      headSha: { type: 'string', required: true, description: '权威 diff 的 head_sha（MR 当前 sha 或本地 HEAD）' },
      changedPaths: { type: 'array', items: { type: 'string' }, description: '可选：精确变更路径清单；缺省由子代理自行 git diff 获取' },
      sourceBranch: { type: 'string', description: '可选：MR 源分支名' },
      targetBranch: { type: 'string', description: '可选：MR 目标分支名' },
      mrIid: { type: 'number', description: '可选：MR IID（上下文）' },
      mrUrl: { type: 'string', description: '可选：MR URL（上下文）' },
      categories: {
        type: 'array',
        items: { type: 'string', enum: [...REVIEW_CATEGORIES] },
        description: '可选：审查维度子集，缺省为全部六维',
      },
      retryOnFail: { type: 'boolean', description: '可选：失败类别是否用上一档模型重试一次，缺省 true' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string', required: true },
        },
      },
      render: (_args, value) => {
        const text = typeof value === 'object' && value !== null && 'text' in value ? String(value.text) : String(value)
        return [{ type: 'text', text }]
      },
    },
    async execute(rawArgs: unknown, exec) {
      const args = rawArgs as MrReviewArgs
      const parent = exec.agent
      if (!parent) {
        throw new Error('mr_review_six 只能在 agent 会话内调用（缺少父代理上下文）')
      }
      const categories: ReviewCategory[] =
        args.categories?.length ? args.categories : [...REVIEW_CATEGORIES]
      const parentIndex = parentTierIndex(parent.options, ladder)
      const parentTier = ladder[parentIndex]
      const lines: string[] = [
        `六维审查：${categories.join(' / ')}`,
        `父代理档位: ${parentTier.name} (${parentTier.model})`,
        '',
      ]
      const results = await Promise.all(categories.map(async (category) => {
        let tierIndex = tierIndexForCategory(category, parentIndex, ladder)
        let tier = ladder[tierIndex]
        let outcome = await runReviewer(ctx, parent, exec.signal, args, category, tier, ladder)
        // 质量护栏：失败且允许重试且未到最强档 → 升一档（索引减 1）重试一次
        if (!outcome.ok && args.retryOnFail !== false && tierIndex > 0) {
          tierIndex -= 1
          tier = ladder[tierIndex]
          outcome = await runReviewer(ctx, parent, exec.signal, args, category, tier, ladder)
        }
        return { category, outcome, tierName: tier?.name ?? '继承' }
      }))
      for (const { category, outcome } of results) {
        const head = `[${category}] ${outcome.ok ? '完成' : '不完整'}（模型档位: ${outcome.tierName}）`
        lines.push(head)
        const body = outcome.text.trim()
        if (outcome.ok && (body === 'no findings' || body === 'no findings。')) {
          lines.push('  no findings')
        } else {
          lines.push(body.split('\n').map((l) => `  ${l}`).join('\n'))
        }
        lines.push('')
      }
      const incomplete = results.filter((r) => !r.outcome.ok)
      if (incomplete.length > 0) {
        lines.push(`不完整维度: ${incomplete.map((r) => r.category).join(', ')}（已按质量护栏升档重试；按 glab-mr-submit skill 规则，审查不完整时不得自动修复，需人工跟进）`)
      }
      return { text: lines.join('\n') }
    },
  }))
  ctx.logger.info('openharmony-debug-test-pipeline: 已注册 mr_review_six 工具（子代理模型自动分级）')
}
