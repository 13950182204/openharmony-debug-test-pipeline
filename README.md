# openharmony-debug-test-pipeline

OpenHarmony「修改-调试-测试」闭环 DSH 插件：把测试报告一路走到回归验证，或者走到豁免资料交付。

> 完整流程、CI 契约、状态字段、豁免资料规范、异常速查见 **[docs/操作手册.md](docs/操作手册.md)**。
> 本 README 只讲三件事：**怎么装、怎么用、回归怎么跑**。

## 一、它是什么

一条带状态机的流水线：每完成一个阶段就把产物写进 `~/.dsh/pipeline-state.json`，
换会话、换机器都能接着上次继续；每个阶段对应一个可独立调用的 skill，也可以只跑其中一段。

```
report ─→ triage ─┬─→ fix ─→ mr ─→ ci ─→ ota ─→ regression ─→ done   （产品/框架侧问题）
                  └─→ waiver ─────────────────────────────────→ done   （测试侧问题，只交豁免资料）
```

- **两条腿**：根因在产品/框架侧就走 `fix` 腿（要 MR、CI、OTA、回归）；根因在测试侧就走 `waiver` 腿，
  资料齐备即收口，不必碰 MR 与设备。
- **阶段契约以脚本为准**：顺序、分支、每阶段必需产物、允许的回退、`done` 判据，
  都由 `python3 scripts/pipeline_state.py describe` 输出；README 与 skill 正文都从它派生。
  写错阶段顺序会被当场拒绝（例如还没登记报告就想跳到 `ota`）。

| 模块 skill | 干什么 | 独立调用示例 |
|---|---|---|
| `openharmony-test-report-triage` | 失败表、源码定位、hilog 证据、根因分类、上游核查 | 「分析这份测试报告」 |
| `karpathy-guidelines` | 修复行为准则：先假设、最小改动、外科手术式变更 | 「按 Karpathy 规范修这个问题」 |
| `openharmony-waiver-draft` | 豁免草稿（只保存，不提交） | 「按豁免规范写这批资料」 |
| `glab-mr-submit` | MR 创建 + 六维审查 + same-MR 修复 | 「提交这个 MR」 |
| `openharmony-ci-orchestrator` | 只读观察平台 CI、认领门禁构建产物、phase-3 交接 | 「认领这次 CI 的构建」 |
| `openharmony-ota-upgrade` | 预检、传输、updater、回连验证 | 「OTA 升级到设备」 |
| `openharmony-debug-loop` | 八阶段编排（本插件新增） | 「跑一遍完整闭环」 |
| `openharmony-demo-loop` | 演示模式，独立状态，不污染真实流水线 | 「演示模式，用 mr260 跑一遍」 |

## 二、安装

两种部署形态，同一份源码：WSL 侧原生 `dsh`（最省事），或 DSH Desktop（插件跑在 Windows、脚本转发进 WSL）。

**A. WSL 侧**

```bash
pnpm build
dsh plugin --profile web add link:/home/cx/os/openharmony-debug-test-pipeline
```

**B. DSH Desktop**：插件页 →「添加插件」→ 填
`https://github.com/13950182204/openharmony-debug-test-pipeline`。装完在 desktop profile 的
`cordis.patch.yml` 补这几行（不配则在 Windows 宿主上路径不可用）：

```yaml
- id: oh-debug-pipeline
  name: "openharmony-debug-test-pipeline"
  config:
    wslPackageRoot: /home/cx/os/openharmony-debug-test-pipeline
    stateFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json
    stateFileModel: /home/cx/.dsh/pipeline-state.json
    credentials:
      storeFile: //wsl.localhost/Ubuntu-22.04/home/cx/.dsh/gitlab-credentials.json
      announceToAgent: false
```

`stateFile` 是宿主读（UNC 形态），`stateFileModel` 是模型读（WSL 视角）——**两者必须指向同一个文件**。
内网/离线改用 `pnpm pack` 出的 tarball 安装（Windows 侧路径），不要用 `link:`/`file:` 指向 WSL 仓库。
装完**重启 DSH** 生效；GitLab 凭据在设置页「GitLab 凭据」填写。

## 三、使用方法

**起一轮闭环**：直接说「跑一遍完整闭环，报告在 `<报告目录>`」。编排 skill 会按状态机推进，
每个阶段结束先汇报、等授权——**创建 MR 与 OTA 是真实动作，必须你点头**。

**看状态 / 手动介入**：

```bash
/pipeline status                                   # 当前阶段、产物、最近事件
python3 scripts/pipeline_state.py describe         # 阶段契约（改流程前先看这个）
python3 scripts/pipeline_state.py note triage "..." # 记事件，不动阶段
python3 scripts/pipeline_state.py reset            # 清空重来
```

阶段推进由模型在会话里用 `set` 完成，产物形状有校验（例如 `failedCases` 必须是
`模块#用例` 清单而不是数量，写成数量会当场报错）。`--force` 能越过"越级/回退"检查，
但 **`done` 的判据越不过去**。

**换一份新报告**会自动把上一轮归档进 `runs[]`（保留最近 5 轮），当前轮从空状态开始——
两轮的 MR 号、OTA 结果不会串。

**两个实用的常见入口**：

- 只分诊：「分析这份报告，给出根因和该修还是该豁免」
- 只提 MR：「这批改动提交 MR」；只认构建：「认领这次 CI 的构建产物」

> `/pipeline` 只接 `status` 和 `reset`，别在后面接自然语言；真正的任务是普通对话。

## 四、回归流程（重点）

回归要回答一句话：**这次 OTA 之后，原来失败的那些用例还失败吗？**

**前置条件**

1. 本轮 `triage` 已登记 `failedModules`（xdevice 模块名，不是测试类名）与 `failedCases`；
   没登记时回归无从下手，只会得出 `INCONCLUSIVE`。
2. 平台 CI 已出包（门禁构建产物：`update.zip` / `updater_full.zip`），且**没有超过 7 天**——
   过期后只能重跑平台流水线，本插件不会替你触发构建。
3. 有一台和构建产物**同芯片**的在线设备。芯片按 job 后缀派生（`OpenHarmony-V6.1-MR-a333` → `a333`），
   A333 与 A537 同源、共用 `-MR-a333`，不需要为 A537 单独配 profile；新芯片才要加 profile 与设备池。
4. 有受信任的 phase-3 profile（负责下载/预检/OTA/回归），profile 里的设备序列号要与在线设备一致。

**等构建**：MR 提交后不用守着。建一个 **DSH 定时任务**：首次在 **+2.5 小时**检查一次，
没好就**每 30 分钟**再查，命令就是 `reconcile`；拿到构建成功后继续 OTA/回归，run 进终态后把该任务删掉。
插件自己不做定时（**不要写 systemd 定时器**，等待节奏由 agent 侧驱动）。

**跑**：`ci` 阶段认领构建 → `ota` 阶段升级设备 → `regression` 阶段在升级后的设备上重跑
`failedModules`（或由 profile 驱动 phase-3 回归）→ 与失败表逐条对比后 `set regression`。

**判据（写 `regressionResult`）**

| 结论 | 写法 | 能否 `done` |
|---|---|---|
| 全通过 | `{"status": "PASS"}` | 可以 |
| 失败数为 0 | `{"failed": 0, "total": 44}` | 可以 |
| 只能跑 smoke，未做功能回归 | `{"status": "INCONCLUSIVE", "smokeOnly": true}` | 可以，但要**明示降级**并在汇报里说清没验什么 |
| 回归失败 | `{"status": "FAIL", "failed": 2, "total": 44}` | 不可以，回 `fix` 继续修 |
| 缺登记 | — | 不可以 |

走过 MR 的轮次必须有通过的回归；`INCONCLUSIVE` 不写 `smokeOnly` 视为**没通过**——
不允许拿一个"没结论"宣布闭环。

**证据回写（有 XTS 用例的回归）**：在设备上用 xdevice 重跑失败的模块，再把报告页截图回写到同一条 MR 评论：

```bash
# 1) 截图（报告目录里的 summary_report.html；static/detail.html 可再截一张）
msedge --headless=new --disable-gpu --window-size=1600,2400 \
  --screenshot="D:\ohos\XTS6.1\<测试套>\summary-report.png" \
  "file:///D:/ohos/XTS6.1/<测试套>/summary_report.html"

# 2) 上传并更新同一条评论（可重复，同一张图不会重复）
python3 skills/openharmony-ci-orchestrator/scripts/attach_regression_evidence.py \
  --run-id <run-id> --image "D:\ohos\XTS6.1\<测试套>\summary-report.png"
```

截图只是证据，不参与通过判定；判定只认回归报告。

**回归失败之后**：阶段回 `fix`，在原 MR 上追加提交（same-MR 修复），再走一遍 `ci` → `ota` → `regression`；
不要新开一轮报告来绕过失败记录。

**两个高频坑**

- 设备不在 profile 的 `devices[]` 里 → 认领/OTA 前就失败；先对照 `hardware_compatibility` 与序列号。
- 门禁包过期或设备被别的流水线占用 → 先看 `~/.local/state/openharmony-ci-orchestrator/runs/` 的运行记录，
  不要重复触发构建。

## 五、豁免资料（waiver 腿）

测试侧问题的资料有固定模型：**一个模块的一个 Testsuite = 一份 `<套件>.txt` + 一份
`<套件>-豁免材料.zip` + 一条平台申请**，目录按 `exemption/<测试大项>/<模块>/` 摆，
同模块多套件必须分文件、分申请。**允许用简单 txt 说明问题**，够说清"哪条用例、为什么是测试侧、
证据在哪"即可；但命名、层级、zip 成对关系要合规。

交付前用只读校验器自检到 `0 问题`：

```bash
python3 scripts/verify_waiver_layout.py --exemption-dir <exemption目录> [--records <records.json>]
```

退出码 `0` 无问题 / `1` 有问题 / `2` 参数错误；历史目录可加 `--allow-legacy`。
资料齐备后 `set waiver '{"waiverRecords": [...]}'` → `set done`。
**本插件只产出与校验资料，不上传、不提交平台**——平台申请提交归 `ohos-xts-auto-reporter` 插件。

## 六、边界与安全

- 真实动作（建 MR、OTA）必须授权；**从不触发 Jenkins 构建**，门禁由平台 CI 驱动，本插件只读观察 + 认领。
- 设备操作前先 `hdc list targets` 确认唯一目标。
- 状态文件损坏或版本不受支持时**保留原文件并报错**，不静默重建。
- Jenkins / GitLab 凭据只从 `~/.config/openharmony-ci-orchestrator/jenkins.env`（0600）或进程环境读取，
  不写入状态文件或 MR 文本。
- 已知边界：功能回归 profile 未配置时回归只能出 `INCONCLUSIVE`（需明示 `smokeOnly`）；
  XTS-only 变更没有设备回测；门禁包 7 天后不可取；无告警通道，靠巡检发现失败。

## 七、版本与兼容

运行时要求 DSH `>=0.2.1-rc.1 <0.3.0`（`engines.dsh`），构建基线 `0.2.1-rc.5`。
已登记兼容的宿主版本见 `package.json` 的 `dsh.compatibility.dshReleases`（0.2.1-rc.1…rc.5、0.2.2-rc.1）。

从 1.7.x 升级：`/pipeline reset` 后重新登记报告即可，旧状态文件仍可读。
从 1.4.x 升级：profile 里旧的独立 GitLab 凭据插件依赖要删掉（自 1.5.0 起是本插件内嵌子组件）。

## 八、开发与自检

```bash
pnpm typecheck && pnpm test && pnpm test:python   # 类型 / vitest / python 单测
python3 test/tools/undefined_names.py             # skill 正文里引用的脚本名是否都存在
```

改了阶段契约（`scripts/pipeline_state.py` 的 `STAGE_ORDER` / `STAGE_REQUIRED` / `done` 判据）后，
记得同步 skill 正文（`src/loop-skill.ts`）与手册第五节——三处都由 `describe` 输出对齐。
改动插件后按手册「十一、自检清单」走一遍再打 tag。
