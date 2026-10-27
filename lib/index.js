var __rewriteRelativeImportExtension = (this && this.__rewriteRelativeImportExtension) || function (path, preserveJsx) {
    if (typeof path === "string" && /^\.\.?\//.test(path)) {
        return path.replace(/\.(tsx)$|((?:\.d)?)((?:\.[^./]+?)?)\.([cm]?)ts$/i, function (m, tsx, d, ext, cm) {
            return tsx ? preserveJsx ? ".jsx" : ".js" : d && (!ext || !cm) ? m : (d + ext + "." + cm.toLowerCase() + "js");
        });
    }
    return path;
};
import { Config } from "./config.js";
import { registerVendoredSkills } from "./vendor.js";
import { registerLoopSkill } from "./loop-skill.js";
import { registerPipelineCommands } from "./commands.js";
import { registerMrReviewTool } from "./mr-review-tool.js";
import { packageRoot } from "./vendor.js";
import { isWindowsHost, resolveModelPaths } from "./wsl-paths.js";
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
export const name = 'openharmony-debug-test-pipeline';
export const inject = ['skills', 'commands', 'tools', 'subagents'];
export { Config };
/**
 * 1.5.0 起 GitLab 凭据是本插件的**内嵌子组件**，不再是独立插件：
 * 源码仍在 packages/gitlab-credentials（与独立发布共用），构建产物随本包分发。
 * 用相对本模块的 URL 动态加载：`src/index.ts` 与 `lib/index.js` 都在包根下一层，
 * 同一条相对路径在开发与发布两种形态下都指向
 * `<包根>/packages/gitlab-credentials/lib/index.js`——因此只需要带这一份构建产物，
 * 不依赖 npm 发布，也不需要 pnpm 允许传递依赖走 git（pnpm 11 默认禁止）。
 */
async function loadEmbeddedCredentials() {
    const url = new URL('../packages/gitlab-credentials/lib/index.js', import.meta.url).href;
    return (await import(__rewriteRelativeImportExtension(url)));
}
export async function apply(ctx, config = {}) {
    const resolved = {
        stateFile: config.stateFile ?? '~/.dsh/pipeline-state.json',
        wslPackageRoot: config.wslPackageRoot,
        stateFileModel: config.stateFileModel,
    };
    registerVendoredSkills(ctx, resolved);
    registerLoopSkill(ctx, config);
    const modelPaths = resolveModelPaths(packageRoot(), resolved);
    registerPipelineCommands(ctx, modelPaths.stateFile);
    registerMrReviewTool(ctx, config);
    if (isWindowsHost() && config.wslPackageRoot === undefined) {
        ctx.logger.warn('openharmony-debug-test-pipeline: 检测到 Windows 宿主但未配置 config.wslPackageRoot——'
            + 'skill 正文里的插件路径将是 Windows 路径，模型在 WSL 里用不了；'
            + '请在 profile 的 cordis.patch.yml 里补 wslPackageRoot，以及 stateFile（宿主读，//wsl.localhost/...）'
            + '与 stateFileModel（模型执行，/home/cx/...）。');
    }
    ctx.logger.info(`openharmony-debug-test-pipeline: OpenHarmony 调测闭环流水线插件加载完成`
        + `（宿主=${process.platform}，状态文件=${modelPaths.stateFile}）`);
    // 内嵌子组件：GitLab 凭据的宿主半侧（凭据存储、/api/dsh-gitlab-credentials 路由、
    // gitlab_cred_status 工具、可选的系统提示段）挂到本插件同一棵树上，随本插件启停；
    // 浏览器半侧由本包 dsh.client（lib/client.js，设置页「GitLab 凭据」分节）承担。
    const credentials = await loadEmbeddedCredentials();
    ctx.plugin(credentials, config.credentials ?? {});
}
