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
pnpm install && npx tsdown          # 只装构建期 devDependencies；随后构建
dsh plugin --profile web add link:$(pwd)

# 发布版（发布到 npm 后）
dsh plugin --profile web add @superjunier/dsh-gitlab-credentials@latest
```

安装后**重启 `dsh web`**：设置页出现「GitLab 凭据」栏目，Agent 提示词自动注入本插件说明。

## DSH Desktop 形态（宿主 Windows、执行 WSL）

DSH Desktop 会在 **Windows 侧**加载插件，而凭据的实际消费者是 **WSL 侧**的 `glab` CLI 与
`glab-mr-submit` 脚本。若桌面侧用缺省路径，它会写 `C:\Users\...\.dsh\gitlab-credentials.json`，
与 WSL 那份**分叉**。因此桌面侧必须显式指向同一文件：

```yaml
# <win-home>\.dsh\profiles\desktop\cordis.patch.yml
- id: gitlab-credentials
  name: "@superjunier/dsh-gitlab-credentials"
  config:
    storeFile: //wsl.localhost/<发行版>/home/<用户>/.dsh/gitlab-credentials.json
    announceToAgent: false   # 权威实例仍是 WSL 侧，避免两个实例都向模型自我宣告
```

```bash
# 桌面侧安装（不能用 link:/file: 指向 WSL 路径，pnpm 会改写 UNC 为不存在的 /wsl.localhost/...）
cd <repo>/packages/gitlab-credentials && npx tsdown
pnpm pack --pack-destination <win-temp>          # 在 Windows 侧打包
pnpm add --config.auto-install-peers=false <win-temp>\<tgz>  # cwd = <win-home>\.dsh\profiles\desktop
# 并把 "@superjunier/dsh-gitlab-credentials" 追加进该 profile 的 dsh.profile.bundles
```

已知行为差异（Windows 侧）：

- **保存可用**：令牌校验走 HTTP，与宿主无关；`glab` 同步会报 `glab binary not found`，
  但代码是 **best-effort**（`routes.ts`: "glab may be absent; the store remains authoritative"），
  令牌照常入库，界面只提示同步失败。
- **glab CLI 会话仍由 WSL 侧维护**：Windows 侧不做 glab 同步（`glab.ts` 只查 PATH 与 `/snap/glab/*`）。
- 依赖：`@deepseek-ai/*`（cordis/dsh-host-webserver/dsh-llm/dsh-settings/dsh-system-prompt/dsh-tools）
  是构建期 external、运行期由 DSH 宿主提供；`schemastery` 走普通 npm 依赖。**不要把宿主 SDK
  装进 profile**（见下节）。

## 宿主 SDK 的解析（不要 vendoring）

DSH 0.2.1 起在 profile 上装了模块解析拦截层：profile 内的插件解析 `@deepseek-ai/*` 时，
会回落到**安装目录**（`app.asar`）里的宿主副本；`link:` 安装的插件只要在
`peerDependencies` 里声明了包名，即便插件目录的 `node_modules` 里有同名包，也会优先
路由到宿主副本。因此本包：

- `@deepseek-ai/dsh-tools` 只声明为 **`peerDependencies`**（`^0.2.1-rc.1`）；
- 精确的 `0.2.1-rc.3` 放在 `devDependencies`，只用于 `tsc` / `tsdown`；
- 不再有 `postinstall`，不再往插件或 profile 里链接/安装宿主 SDK。

**为什么不能装进 profile**：profile 里一旦出现 `node_modules/@deepseek-ai/dsh-tools`，
解析就优先落到这个副本，连核心 `tools` 插件行本身都会加载它；两份模块实例的调度器
`Symbol` 不同，`tools` 服务无法激活，DSH 启动直接失败（`Missing services: tools`）。
**同版本号不等于同模块实例。**

**故障教训**：本地 `link:` 安装后，用独立端口（`dsh --profile web --port <p> --no-open`）
验证再纳入常驻 profile；并确认 `node_modules/@deepseek-ai/dsh-tools` 不存在于 profile 顶层。

## 安全模型

- Token 仅存本机 0600 文件；GUI 表单不持久化（除提交保存外无缓存）
- 所有 `/api/dsh-gitlab-credentials/*` 路由仅 loopback 访问（同源栅栏，与 dsh-ssh 相同）
- 校验错误与工具输出经 `<redacted>`/脱敏处理，令牌指纹仅显示前 4 + 后 4 位
- gitlab 同步始终使用 `--stdin` 传输 token；子进程参数与日志不含 token

## 开发

```bash
pnpm add -D typescript tsdown @types/node @types/react @types/react-dom
npx tsc --noEmit           # 类型检查（SDK 类型来自 devDependencies 的 rc.3）
npx tsdown                 # 双半构建：lib/index.js（host）+ lib/client.js（browser）
npx tsx test/store.smoke.ts  # store 冒烟测试（0600/原子写/脱敏/偏好）
```

## 已知限制

- GitLab PAT 非 JWT，无法本地解析过期时间；以「使用前校验」兜底（GUI 显示最近校验时间）
- 无 keyring 的主机（headless）凭据落在 0600 JSON；检测到 `secret-tool` 可用时可在后续版本接入系统密钥环
- MR 偏好中的 `removeSourceBranch` 仅作记录；脚本按流程规范始终开启删除源分支
- 域名含端口/斜杠的主机校验拒绝（保持 host 即 glab hostname 的语义）
