import type { Context } from '@deepseek-ai/cordis';
import { type ReviewCategory } from './review-ladder.ts';
import type { Config } from './config.ts';
export interface MrReviewArgs {
    repoDir: string;
    baseSha: string;
    headSha: string;
    changedPaths?: string[];
    sourceBranch?: string;
    targetBranch?: string;
    mrIid?: number;
    mrUrl?: string;
    categories?: ReviewCategory[];
    retryOnFail?: boolean;
}
export declare function registerMrReviewTool(ctx: Context, config: Config): void;
