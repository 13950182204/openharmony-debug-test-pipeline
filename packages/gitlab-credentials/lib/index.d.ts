import z from "schemastery";
import { Context } from "@deepseek-ai/cordis";
//#region src/index.d.ts
/** Stable cordis plugin name. */
declare const name = "gitlab-credentials";
/** Services required before the surfaces can mount. */
declare const inject: string[];
/** Settings namespace of the plugin (web settings surface edits live here). */
declare const SETTINGS_NAMESPACE = "dsh-gitlab-credentials";
/** Plugin config. */
interface Config {
  /** When true (default), a system-prompt section announces the plugin to every agent. */
  announceToAgent?: boolean;
  /** Master switch for the plugin (routes, tools, prompt section). */
  enabled?: boolean;
  /**
   * 凭据文件路径；缺省 `~/.dsh/gitlab-credentials.json`。
   *
   * 「宿主 Windows、执行 WSL」（DSH Desktop）形态下必须显式给成 WSL 视角路径的
   * Windows 可写形态（`//wsl.localhost/<发行版>/home/<用户>/.dsh/gitlab-credentials.json`），
   * 否则桌面侧会写 `C:\Users\...\.dsh\...`，与 WSL 侧的 `glab` / MR 流程读的那份**分叉**。
   * 该值在插件启动时读取一次，改动后需重启宿主。
   */
  storeFile?: string;
  /**
   * glab 执行方式覆盖（一般不需要配）。Windows 宿主默认自动探测 WSL 里的 glab；
   * 装在非常规位置或要指定发行版时可写成：
   * - 单个词 = glab 可执行文件路径（如 `/snap/bin/glab`）
   * - 多个词 = 前缀，glab 的参数接在其后（如 `wsl.exe -d Ubuntu-22.04 -e bash -lc`）
   */
  glabCommand?: string;
}
declare const Config: z<Config>;
/** Model-facing announcement: plugin presence, capabilities, and limits. */
declare const CREDENTIALS_GUIDANCE = "本机已安装 dsh-gitlab-credentials 插件（GitLab 凭据管理）：设置页「GitLab 凭据」栏管理每个 GitLab 主机的访问令牌（保存时校验后同步到 glab CLI，供 MR 流程使用）；同一栏可配置 MR 偏好（指派人/目标分支/标签/里程碑）。能力：gitlab_cred_status 工具报告各主机令牌状态（用户/指纹/glab 同步情况，绝不回显令牌）；令牌仅存于 ~/.dsh/gitlab-credentials.json（0600），写入与 glab 同步由用户在 GUI 完成，agent 不掌握令牌明文。限制：令牌需在 GUI 保存后 agent 方可使用；失效时提示用户在设置中重新保存。用户提到「GitLab 令牌 / 凭据 / MR 认证 / 授权提交」时即指本插件，请据此协作。";
/** Stable once-guard per process (bundle re-apply is tolerated, double-mount is not). */
declare const apply: typeof applyImpl;
declare function applyImpl(ctx: Context, config?: Config): void;
//#endregion
export { CREDENTIALS_GUIDANCE, Config, SETTINGS_NAMESPACE, apply, inject, name };