# 上游核查

定稿修复前，检查 OpenHarmony 上游是否已改动同一用例、辅助代码、框架、服务、驱动或产品源码。为 XTS 失败修改产品/框架源码前，这是必做步骤。

## 仓库

- ACTS: `https://gitcode.com/openharmony/xts_acts.git`
- HATS: `https://gitcode.com/openharmony/xts_hats.git`
- DCTS: `https://gitcode.com/openharmony/xts_dcts.git`
- 产品/框架/服务源码：从本地路径推断 GitCode 仓库，再用 `git ls-remote` 验证。
  - 例：`foundation/communication/bluetooth_service/...` -> `https://gitcode.com/openharmony/communication_bluetooth_service.git`
  - 例：`foundation/communication/bluetooth/...` -> `https://gitcode.com/openharmony/communication_bluetooth.git`
  - 在本地 monorepo 或厂商镜像中，本地 `origin` 可能不暴露 OpenHarmony 拆分仓库。使用路径映射并在 GitCode 上验证。

用户未指定分支时的默认检查分支：

- `OpenHarmony-6.1-Release`

用户写了小写分支如 `openharmony-6.1-release` 时，规范化为 GitCode 发布分支风格，但验证分支存在。用户分支存在时优先使用精确的用户分支。

用户要求同时检查 `6.1-release` 与 `6.1-LTS` 时，规范化并检查 `OpenHarmony-6.1-Release` 与 `OpenHarmony-6.1-LTS` 两者。请求了多个分支时，不要只在默认分支上停下。

## 方法

1. 确认可能需要改什么：
   - 用例源码：映射到 ACTS/HATS/DCTS。
   - 产品/框架/服务源码：把本地源码路径映射到对应 OpenHarmony GitCode 拆分仓库。
   - 对 ACTS 失败，检查 `https://gitcode.com/openharmony/xts_acts.git` 的用例侧改动，同时检查所属拆分仓库的产品/框架/服务改动，如 `multimedia_audio_framework`、`multimedia_player_framework`、`multimedia_camera_framework`、`graphic_graphic_2d` 或 `communication_bluetooth`。
2. 检查分支存在：

```bash
git ls-remote --heads <repo-url> OpenHarmony-6.1-Release
```

3. 需要源码对比或历史搜索时，浅克隆到 `/tmp`：

```bash
git clone --depth 80 --filter=blob:none --no-checkout \
  --branch OpenHarmony-6.1-Release <repo-url> /tmp/<repo>_6_1_check
```

4. 用以下命令定位精确用例路径：

```bash
git -C /tmp/<repo>_6_1_check ls-tree -r --name-only HEAD | rg '<case or file name>'
```

对产品/框架/服务源码，使用相对于该拆分仓库的本地路径。

5. 对比本地源码片段与上游：

```bash
git -C /tmp/<repo>_6_1_check show HEAD:<path> | sed -n '<start>,<end>p'
```

6. 发明本地修复前搜索相似的上游补丁：

```bash
git -C /tmp/<repo>_6_1_check log --oneline -- <path>
git -C /tmp/<repo>_6_1_check log --oneline -S'<error code, log text, enum, or testcase name>' -- <path>
git -C /tmp/<repo>_6_1_check show --stat --oneline <commit>
git -C /tmp/<repo>_6_1_check show --unified=80 <commit> -- <path>
```

运行期失败用报告中的决定性证据搜索，如错误码、hilog 短语、回调名或枚举。上游有相近但不完整的补丁时，明确指出它覆盖了哪条路径，以及为什么当前报告仍需要额外的本地改动。

## 汇报

始终汇报：

- 仓库
- 分支
- 来自 `git ls-remote` 的 commit 哈希
- 上游文件路径
- 用例或产品/框架源码是否与本地不同
- 拆分仓库检查所用的搜索词或本地路径
- 最小建议回移
- 无匹配补丁时，检查过的精确搜索词或文件

上游改动只改了可变的外部 URL 时，仍要说明测试想验证什么，以及为什么新 URL 更合适。
