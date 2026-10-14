import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
export const SCHEMA_VERSION = 1;
export function emptyState() {
    return {
        schemaVersion: SCHEMA_VERSION,
        updatedAt: '',
        stage: null,
        artifacts: {},
        events: [],
    };
}
/** 展开 ~ 为用户主目录并解析为绝对路径 */
export function expandStatePath(path) {
    // 注意不能用 resolve(homedir(), path.slice(1))：slice 后以 / 开头会被 resolve 当作绝对路径丢弃 home
    if (path.startsWith('~'))
        return join(homedir(), path.slice(1));
    return resolve(path);
}
/** 读取状态文件；不存在返回空状态，损坏则备份后重建 */
export function loadState(path) {
    const file = expandStatePath(path);
    if (!existsSync(file))
        return emptyState();
    let raw;
    try {
        raw = readFileSync(file, 'utf8');
    }
    catch {
        return emptyState();
    }
    let parsed;
    try {
        parsed = JSON.parse(raw);
    }
    catch {
        backupCorrupt(file);
        return emptyState();
    }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        backupCorrupt(file);
        return emptyState();
    }
    const value = parsed;
    if (value.schemaVersion !== SCHEMA_VERSION) {
        backupCorrupt(file);
        return emptyState();
    }
    return {
        ...emptyState(),
        ...value,
        artifacts: value.artifacts && typeof value.artifacts === 'object' ? value.artifacts : {},
        events: Array.isArray(value.events) ? value.events : [],
    };
}
function backupCorrupt(file) {
    try {
        renameSync(file, `${file}.bak-${Date.now()}`);
    }
    catch {
        // 备份失败不阻断加载
    }
}
/** 原子写状态文件（先写临时文件再 rename） */
export function saveState(path, state) {
    const file = expandStatePath(path);
    mkdirSync(dirname(file), { recursive: true });
    const tmp = `${file}.tmp`;
    writeFileSync(tmp, JSON.stringify({ ...state, updatedAt: new Date().toISOString() }, null, 2) + '\n', 'utf8');
    renameSync(tmp, file);
}
/** 重置状态并落盘 */
export function resetState(path) {
    const state = emptyState();
    saveState(path, state);
    return state;
}
/** 追加一条流水线事件（不落盘，调用方负责 saveState） */
export function pushEvent(state, stage, note) {
    state.events.push({ at: new Date().toISOString(), stage, note });
}
/** 渲染状态为人类可读文本（供 /pipeline status 与闭环汇报使用） */
export function renderState(state) {
    const lines = [];
    lines.push(`流水线阶段: ${state.stage ?? '（未开始）'}`);
    const artifactKeys = Object.keys(state.artifacts);
    if (artifactKeys.length === 0) {
        lines.push('产物: （无）');
    }
    else {
        lines.push('产物:');
        for (const key of artifactKeys) {
            lines.push(`  ${key}: ${state.artifacts[key] ?? ''}`);
        }
    }
    const recent = state.events.slice(-8);
    if (recent.length === 0) {
        lines.push('事件: （无）');
    }
    else {
        lines.push('最近事件:');
        for (const event of recent) {
            lines.push(`  [${event.stage}] ${event.note}`);
        }
    }
    const snapshots = state.tokenSnapshots ?? [];
    if (snapshots.length > 0) {
        lines.push('token 快照（最近 3 次）:');
        for (const snap of snapshots.slice(-3)) {
            lines.push(`  [${snap.stage}] in=${snap.uncachedInputTokens} out=${snap.outputTokens} cacheRead=${snap.cacheReadTokens}`);
        }
    }
    return lines.join('\n');
}
