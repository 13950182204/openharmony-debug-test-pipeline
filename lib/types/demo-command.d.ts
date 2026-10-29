import type { Context } from '@deepseek-ai/cordis';
/** 演示状态里的单个阶段记录（与 scripts/demo/democtl.py 对齐） */
export interface DemoStageRecord {
    status?: string;
    provenance?: string;
    startedAt?: string;
    finishedAt?: string;
    notes?: string[];
    evidence?: string[];
    degraded?: boolean;
}
/** 演示模式状态文件（`~/.dsh/demo/state.json`） */
export interface DemoState {
    schemaVersion?: number;
    case?: string;
    lane?: string;
    stage?: string;
    degraded?: boolean;
    startedAt?: string;
    updatedAt?: string;
    stages?: Record<string, DemoStageRecord>;
    artifacts?: Record<string, unknown>;
    events?: Array<{
        at?: string;
        stage?: string;
        note?: string;
    }>;
}
/** 读取演示状态；缺失或损坏返回 null（演示可能还没开始） */
export declare function loadDemoState(path: string): DemoState | null;
/** 渲染演示状态为人类可读文本（供 /demo status 使用） */
export declare function renderDemoState(state: DemoState | null): string;
/** 清空演示状态；`runs/` 证据目录保留 */
export declare function resetDemoState(path: string): string;
/**
 * /demo 命令：查询与重置演示模式状态。
 *   /demo status - 显示阶段、产物来源与最近事件
 *   /demo reset  - 清空演示状态（需在输入中明确写 reset）
 */
export declare function registerDemoCommand(ctx: Context, demoStateFile: string): void;
