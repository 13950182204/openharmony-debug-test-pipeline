# HDF and Product Feature Overrides

Use product-side overrides when a common inherited config enables hardware/model support that the product does not actually provide.

## Investigation pattern

1. Find product config, usually under `vendor/<vendor>/<product>/config.json`.
2. Inspect inherited configs from `productdefine/common/inherit/*.json`.
3. Search generated build args under `out/<product>/args.gn` when present.
4. Find the GN variable that gates the driver/model code.
5. Override the feature in the product's component list.

## Override example

For a product without a real sensor stack:

```json
{
  "component": "drivers_peripheral_sensor",
  "features": [
    "drivers_peripheral_sensor_feature_model = false"
  ]
}
```

For a product without a real vibrator:

```json
{
  "component": "drivers_peripheral_vibrator",
  "features": [
    "drivers_peripheral_vibrator_feature_model = false"
  ]
}
```

## Guardrails

- Only disable a model when the product should not advertise that capability.
- Preserve unrelated local product changes.
- Validate JSON after editing.
- Rebuild the affected product image/components before treating the fix as verified.

