# 失败模式

这些是可复用模式，不是自动结论。始终用当前报告、源码与产品配置验证。

## 产品缺硬件但暴露了 model/mock

症状：
- 测试在物理上没有某传感器的产品上发现了传感器/振动器/摄像头等。
- HDF/公共继承配置启用了某个外设 model。
- hilog 或 API 输出显示 `sensor_test` 等 mock 名或预设振动效果。

修复方向：
- 优先产品侧功能覆盖。
- 显式禁用相关外设 model，或移除该产品宣传的能力。
- 不要为让测试忽略产品仍在宣传的硬件而改测试。

## 超时伴随大量阻塞用例

症状：
- 一个用例超时失败。
- 同一模块中许多后续用例被阻塞。
- `module_run.log` 显示运行器在第一个超时后停止。

修复方向：
- 聚焦第一个超时用例。
- 检查 promise/回调错误路径是否未能调用 `done()`。
- 检查产品/框架是否未能送达测试等待的回调。

## 浮点或时序容差

症状：
- 实际值极接近期望值，如 `1e-11` 对 `0`。
- API 对数学或时序敏感。

修复方向：
- API 契约允许时使用小的 epsilon 或容差。
- 保持容差狭窄并解释数值证据。

## 回调/状态机边界

症状：
- 操作回调返回成功，但后续状态/事件回调从未到达。
- 测试日志停在「等待回调」然后超时。

修复方向：
- 确认哪个框架事件映射到 JS/原生回调。
- 当 API 语义要求时，产品/框架根因修复应发出缺失的状态/事件。
- 测试侧 workaround 可以避开精确时序或 EOF 边界，但当它不修复产品语义时必须标注为 workaround。

## JS HAP 异步 API 超时但同步路径正常

症状：
- JS ACTS HAP 用例在 5000 ms 超时。
- hilog 显示用例进入后，在 promise/回调 API 调用之后或 `done()` 之前停止。
- 同一能力的相邻同步 API 用例通过。
- 把 `getProperties(callback)` 之类的回调辅助换成可用的同步等价物如 `getWindowProperties()`，可消除一个不确定性来源。

修复方向：
- 先用精确用例附近的 hilog 证明缺失的回调/promise 返回。
- 检查 API 是否有同步变体，以及既有用例是否已验证这些同步变体。
- 本地 XTS workaround 时，对确定性的可观察结果（如返回类型或错误码）保留真实断言。
- 如果产品在该板上可以合法返回而不改变可见状态，不要盲目断言状态迁移。
- 重建 JS HAP 后用 `strings` 确认实际 ABC/HAP 内容；若仍有陈旧字符串，按 `fast_rebuild.md` 中的 JS HAP 缓存刷新流程处理。

## 外部网络资源漂移

症状：
- 只有依赖网络的请求/下载用例失败。
- XML 可能消息为空，但 hilog 显示断言不匹配。
- 用例使用公共 URL，并假设特定头部、内容长度、状态或响应行为。
- 运行期响应头与测试假设不同。

示例：
- `SUB_Request_DownloadManagement_Download_0100` 使用 `https://gitee.com`。
- 用例描述说它在测试未定义文件大小，断言 `pro.sizes[0] == -1`。
- 运行期响应包含 `content-length`，所以请求报告了 `642022` 这样的正大小。
- 上游 `xts_acts` 的 `OpenHarmony-6.1-Release` 把该 URL 改为 `https://weibo.com`，同时保留 `-1` 断言。

详细用例记录：
- 报告：`zxts/ActsRequestAuthorityTest`，共 259，通过 258，失败 1，阻塞 0。
- 模块/用例：`requestDownloadJSUnit#SUB_Request_DownloadManagement_Download_0100`。
- 测试内容：request-agent 下载应把未知文件大小报告为 `-1`。
- 测试输入：`action = DOWNLOAD`，公共 URL 原为 `https://gitee.com`，保存路径 `./SUB_Request_DownloadManagement_Download_0100`，网络 `ANY`，允许覆盖。
- 测试流程：创建 `request.agent.Config`，调用 `request.agent.create(baseContext, config)`，注册 `task.on('completed', completedCallback)`，调用 `task.start()`，在 completed 回调中读取 `pro.sizes[0]`，断言 `pro.sizes[0] == -1`，然后调用 `done()`。
- 期望：completed 进度报告 `sizes[0] = -1`，因为该资源本应无定义大小。
- 实际证据：hilog 记录 completed 进度 `processed: 642022`、`sizes: [642022]`、`content-type: text/html; charset=utf-8`、`content-length: 642022`；断言以 `expect 642022 equals -1` 失败。
- 根因：用例依赖可变的外部 URL 行为。`https://gitee.com` 返回了带 `Content-Length` 的正常 HTML 响应，请求框架因此正确报告了已知大小。
- 上游核查：ACTS 仓库 `https://gitcode.com/openharmony/xts_acts.git`，分支 `OpenHarmony-6.1-Release`，commit `9830c07a91a0cdaa19dc0eab4fd99b8967bafce2`，路径 `request/newRequestAuthorityTest/entry/src/ohosTest/ets/test/requestDownload.test.ets`，把 URL 改为 `https://weibo.com` 并保留 `expect(pro.sizes[0]).assertEqual(-1)`。
- 最小本地回移：在匹配的 monorepo 路径中，只把该用例 URL 从 `https://gitee.com` 改为 `https://weibo.com`。
- 用例编译：因为这是用例源码改动，编译 `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`。GN 输出存在后，优先根目录 `build.sh --fast-rebuild --build-target test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`，避免重复 GN 与 `suite_type mismatch` 噪音。

修复方向：
- 优先把公共 URL 换成具有预期响应行为的受控测试端点。
- 上游已改 URL 或夹具时，优先最小回移该改动。
- API 契约同时允许未知与已知大小时，调整断言同时接受两者，并说明被削弱的测试含义。
- 不要为迎合过时的测试假设而改产品/框架代码来隐藏合法的 `Content-Length`。
