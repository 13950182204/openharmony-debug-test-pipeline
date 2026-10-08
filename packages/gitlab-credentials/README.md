# dsh-gitlab-credentials

GitLab 凭据管理插件（dsh web GUI）：按主机保存访问令牌（0600 存储、GitLab API 校验、自动同步 glab CLI），并在「设置」页提供 **GitLab 凭据** 栏目（凭据管理 + MR 偏好面板），供 glab-mr-submit 流程自动认证与取默认值。令牌永不出现在对话或日志中。

## 能力

- **设置页单独一栏**：设置 → 「GitLab 凭据」标签页，含
  - 凭据管理：主机列表（用户 / 令牌指纹 / 最近校验 / glab 同步状态）+ 添加/更新表单（Host、API Host、API/Git 协议、Token 密码框）
  - **MR 偏好面板**：指派人、目标分支、标签（逗号分隔）、里程碑、合并后删除源分支
- **保存即校验**：`GET /api/v4/user` 验证令牌与用户名，随后子进程 `glab auth login --stdin` 同步 glab（token 只走 stdin，绝不进命令行/日志/仓库/MR）
- **删除即登出**：同时清理插件 store 与 glab 会话
- **Agent 只读工具** `gitlab_cred_status`：各主机状态（用户/指纹/上次校验/glab 同步），绝不回显令牌
- **script 集成**：`create_glab_mr.py` 认证失败时自动从插件 store 登录 glab；MR 偏好作为 CLI 未显式传参时的默认值（CLI 参数优先）

## 数据

- 凭据与偏好：`~/.dsh/gitlab-credentials.json`（0700 目录 / 0600 文件，原子写）
- 可覆盖：`config.storeFile` 指定其它路径（例如 DSH Desktop 形态指向 WSL 那份文件，见下）
- 与 glab 的关系：插件 store 为权威源；保存时同步进 glab 客户端配置（`~/.config/glab-cli/`），删除时登出

## 安装

```bash
# 从仓库目录（开发）
cd <repo>/packages/gitlab-credentials
pnpm install && npx tsdown          # postinstall 自动 vendoring runtime SDK；随后构建
dsh plugin --profile web add link:$(pwd)

# 发布版（发布到 npm 后）
dsh plugin --profile web add @linxin666/dsh-gitlab-credentials@latest
```

安装后**重启 `dsh web`**：设置页出现「GitLab 凭据」栏目，Agent 提示词自动注入本插件说明。

## DSH Desktop 形态（宿主 Windows、执行 WSL）

DSH Desktop 会在 **Windows 侧**加载插件，而凭据的实际消费者是 **WSL 侧**的 `glab` CLI 与
`glab-mr-submit` 脚本。若桌面侧用缺省路径，它会写 `C:\Users\...\.dsh\gitlab-credentials.json`，
与 WSL 那份**分叉**。因此桌面侧必须显式指向同一文件：

```yaml
# <win-home>\.dsh\profiles\desktop\cordis.patch.yml
- id: gitlab-credentials
  name: "@linxin666/dsh-gitlab-credentials"
  config:
    storeFile: //wsl.localhost/<发行版>/home/<用户>/.dsh/gitlab-credentials.json
    announceToAgent: false   # 权威实例仍是 WSL 侧，避免两个实例都向模型自我宣告
```

```bash
# 桌面侧安装（不能用 link:/file: 指向 WSL 路径，pnpm 会改写 UNC 为不存在的 /wsl.localhost/...）
cd <repo>/packages/gitlab-credentials && npx tsdown
pnpm pack --pack-destination <win-temp>          # 在 Windows 侧打包
pnpm add --config.auto-install-peers=true <win-temp>\<tgz>   # cwd = <win-home>\.dsh\profiles\desktop
# 并把 "@linxin666/dsh-gitlab-credentials" 追加进该 profile 的 dsh.profile.bundles
```

已知行为差异（Windows 侧）：

- **保存可用**：令牌校验走 HTTP，与宿主无关；`glab` 同步会报 `glab binary not found`，
  但代码是 **best-effort**（`routes.ts`: "glab may be absent; the store remains authoritative"），
  令牌照常入库，界面只提示同步失败。
- **glab CLI 会话仍由 WSL 侧维护**：Windows 侧不做 glab 同步（`glab.ts` 只查 PATH 与 `/snap/glab/*`）。
- 依赖：桌面 profile 需能解析 `@deepseek-ai/{cordis,dsh-host-webserver,dsh-llm,dsh-settings,dsh-system-prompt,dsh-tools}`
  与 `schemastery`（这些是构建时的 external 依赖，运行期由宿主提供）。

## Runtime 依赖 vendoring（重要）

`@deepseek-ai/*` host SDK（`dsh-settings` / `dsh-tools` 等）**没有发布到公开 npm registry**，由 DSH runtime 内置携带。插件作为 `link:` 本地包被 cordis 装载时，Node ESM 从插件的**真实路径**（如 `<repo>/packages/gitlab-credentials`）解析 import——缺少运行时依赖会以 `MODULE_NOT_FOUND` 直接拖垮整个 profile 启动（曾导致 `Failed to load plugins`，见 DSH 知识库故障记录）。

因此 `package.json` 的 **`postinstall`**（`scripts/link-runtime-deps.mjs`）会把运行时 SDK 以符号链接 vendoring 进插件自身 `node_modules`，指向当前 `~/.dsh/runtime/<ver>/node_modules/.pnpm/node_modules/@deepseek-ai`（目标包已在该树内，传递依赖随之全部可解析）。`pnpm install` 自动执行；删除插件目录后重新 link 即可。vendored 清单见 package.json 的 `"vendored"` 字段（自定义元数据，非依赖声明）。

**故障教训**：任何新增/升级本地 link 插件，都必须先确认其 host 产物 import 的 SDK 能从插件目录解析，再用独立端口（`dsh --profile web --port <p> --no-open`）验证后再加入常驻 profile。

## 安全模型

- Token 仅存本机 0600 文件；GUI 表单不持久化（除提交保存外无缓存）
- 所有 `/api/dsh-gitlab-credentials/*` 路由仅 loopback 访问（同源栅栏，与 dsh-ssh 相同）
- 校验错误与工具输出经 `<redacted>`/脱敏处理，令牌指纹仅显示前 4 + 后 4 位
- gitlab 同步始终使用 `--stdin` 传输 token；子进程参数与日志不含 token

## 开发

```bash
pnpm add -D typescript tsdown @types/node @types/react @types/react-dom
npx tsc --noEmit           # 类型检查（SDK 类型经 tsconfig paths 映射到运行时安装）
npx tsdown                 # 双半构建：lib/index.js（host）+ lib/client.js（browser）
npx tsx test/store.smoke.ts  # store 冒烟测试（0600/原子写/脱敏/偏好）
```

## 已知限制

- GitLab PAT 非 JWT，无法本地解析过期时间；以「使用前校验」兜底（GUI 显示最近校验时间）
- 无 keyring 的主机（headless）凭据落在 0600 JSON；检测到 `secret-tool` 可用时可在后续版本接入系统密钥环
- MR 偏好中的 `removeSourceBranch` 仅作记录；脚本按流程规范始终开启删除源分支
- 域名含端口/斜杠的主机校验拒绝（保持 host 即 glab hostname 的语义）
