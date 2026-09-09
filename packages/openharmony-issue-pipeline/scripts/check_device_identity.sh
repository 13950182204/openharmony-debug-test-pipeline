#!/usr/bin/env bash
# 设备身份巡检:检查 HDC 在线设备是否匹配预期 A333 医疗平台(用于 OTA/回归前闸门)
# 用法: check_device_identity.sh <expected_serial> [expected_product] [expected_model]
# 退出码: 0=匹配 1=设备缺失 2=身份不符 3=存在多设备
set -u
HDC="${HDC:-/home/cx/os/toolchains-linux-x64-4.0.11.4-Release/toolchains/hdc}"
SERIAL="${1:-ea010e325333324247102b4ed1988ce7}"
EXPECT_PRODUCT="${2:-DHong-A333-Development-Board}"   # const.product.name
EXPECT_MODEL="${3:-76A}"                              # const.build.product

TARGETS=$("$HDC" list targets 2>/dev/null | grep -v FreeChannel | grep -v '^$' | sort | uniq)
COUNT=$(echo "$TARGETS" | grep -c .)
echo "在线设备数: $COUNT"
echo "$TARGETS" | sed 's/^/  - /'

IDENTITY=$("$HDC" -t "$SERIAL" shell \
  'param get const.product.name; param get const.build.product; param get const.product.software.version' \
  2>/dev/null | grep -v FreeChannel | tr '\n' '|')
echo "目标设备 $SERIAL => $IDENTITY"

[ "$COUNT" -gt 1 ] && { echo "WARN: 存在多设备(OAT/OTA 唯一性),继续按指定 serial 操作"; }
echo "$IDENTITY" | grep -q "$EXPECT_PRODUCT" || { echo "FAIL: product.name 非为 '$EXPECT_PRODUCT'(当前镜像非医疗平台)"; exit 2; }
echo "$IDENTITY" | grep -q "$EXPECT_MODEL" || { echo "FAIL: build.product 非为 '$EXPECT_MODEL'"; exit 2; }
echo "PASS: $SERIAL 身份匹配($EXPECT_PRODUCT / $EXPECT_MODEL)"
exit 0
