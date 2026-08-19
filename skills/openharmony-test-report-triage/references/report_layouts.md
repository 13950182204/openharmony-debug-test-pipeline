# 报告布局

OpenHarmony ACTS/HATS/DCTS/XTS 报告通常来自 xdevice 类运行器，但精确路径因套件与版本而异。要主动发现文件，而不是硬编码某一种布局。

## 常见文件

- `task_log.log`：模块/用例总数汇总与顶层运行器消息。
- `result/*.xml`：JUnit 风格用例结果文件。这是失败用例名与超时消息最快的来源。
- `<ModuleName>/module_run.log`：模块命令行、执行顺序、通过/失败/阻塞连带、超时值。
- `<ModuleName>/hilog_*/hilog*.gz`：运行期日志。压缩文件用 `zgrep` 或脚本搜索。
- `faultlog`、`cppcrash`、`appfreeze`、`SERVICE_BLOCK`：失败迹象指向崩溃、卡死或服务阻塞时使用。

## 搜索顺序

1. 解析 XML 结果，取失败/超时用例名。
2. 用模块日志定位第一个失败用例与阻塞级联。
3. 用 `rg` 定位用例源码。
4. 提取用例开始、回调日志与失败时间戳附近的 hilog。
5. 仅在症状佐证时检查崩溃/卡死日志。

## 源码查找

先在用户提供的路径下搜索。用户给了父路径时，尝试：

- `test/xts/acts`
- `test/xts/hats`
- `test/xts/dcts`
- `test/xts`

按顺序使用搜索键：

- 精确用例名
- 类名或套件名
- 用例名的尾部组件
- hilog 中记录到的字符串
- 被测 API 名
