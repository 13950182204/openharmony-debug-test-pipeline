/**
 * 流水线状态：跨会话持久化闭环进度（JSON 文件）。
 * - 写入为原子写（临时文件 + rename），避免并发读半截文件；
 * - 文件损坏时备份为 .bak-<时间戳> 后重建空状态，不崩溃。
 */
export interface PipelineEvent {
    at: string;
    stage: string;
    note: string;
}
export interface PipelineState {
    schemaVersion: number;
    updatedAt: string;
    /** 当前阶段：report | triage | fix | mr | ci | ota | regression | done | null */
    stage: string | null;
    /** 阶段产物：报告路径、MR IID、构建号、设备 serial、OTA 结果等 */
    artifacts: Record<string, string | number | null>;
    events: PipelineEvent[];
    /** 每阶段结束的 token 用量快照（来自 DSH session_projcache） */
    tokenSnapshots?: TokenSnapshot[];
}
/** 一次 token 用量快照（与 scripts/pipeline_state.py 的 tokens 子命令对齐） */
export interface TokenSnapshot {
    at: string;
    stage: string;
    sessionId: string;
    uncachedInputTokens: number;
    outputTokens: number;
    cacheReadTokens: number;
    cacheWriteTokens: number;
}
export declare const SCHEMA_VERSION = 1;
export declare function emptyState(): PipelineState;
/** 展开 ~ 为用户主目录并解析为绝对路径 */
export declare function expandStatePath(path: string): string;
/** 读取状态文件；不存在返回空状态，损坏则备份后重建 */
export declare function loadState(path: string): PipelineState;
/** 原子写状态文件（先写临时文件再 rename） */
export declare function saveState(path: string, state: PipelineState): void;
/** 重置状态并落盘 */
export declare function resetState(path: string): PipelineState;
/** 追加一条流水线事件（不落盘，调用方负责 saveState） */
export declare function pushEvent(state: PipelineState, stage: string, note: string): void;
/** 渲染状态为人类可读文本（供 /pipeline status 与闭环汇报使用） */
export declare function renderState(state: PipelineState): string;
