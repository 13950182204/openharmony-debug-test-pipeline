# HDF 与产品功能覆盖

当公共继承配置启用了产品实际不具备的硬件/model 支持时，使用产品侧覆盖。

## 调查模式

1. 找到产品配置，通常在 `vendor/<vendor>/<product>/config.json`。
2. 检查 `productdefine/common/inherit/*.json` 的继承配置。
3. 存在时搜索 `out/<product>/args.gn` 中的生成构建参数。
4. 找到门控驱动/model 代码的 GN 变量。
5. 在产品组件列表中覆盖该功能。

## 覆盖示例

对没有真实传感器栈的产品：

```json
{
  "component": "drivers_peripheral_sensor",
  "features": [
    "drivers_peripheral_sensor_feature_model = false"
  ]
}
```

对没有真实振动器的产品：

```json
{
  "component": "drivers_peripheral_vibrator",
  "features": [
    "drivers_peripheral_vibrator_feature_model = false"
  ]
}
```

## 护栏

- 只有当产品不应宣传该能力时才禁用某个 model。
- 保留无关的本地产品改动。
- 编辑后校验 JSON。
- 重新构建受影响的产品镜像/组件后，才视为修复已验证。
