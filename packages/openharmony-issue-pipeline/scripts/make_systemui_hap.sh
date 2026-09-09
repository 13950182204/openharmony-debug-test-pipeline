#!/usr/bin/env bash
# 自主编译 OpenHarmony SystemUI 模块 HAP(不依赖 DevEco GUI / Windows)
# 配方来源: issue #4 总结的经验(npm 官方 hvigor CLI + 仓库内 api23 SDK)
# 用法: make_systemui_hap.sh [-m phone_dropdownpanel] [--sdk-root <path>] [--src <systemui源码路径>]
# 产物: <src>/product/<module>/build/default/outputs/default/<module>-phone_entry-default-signed.hap
set -euo pipefail

MODULE="${1:-phone_dropdownpanel}"
SRC="${SRC:-/tmp/sysui-build4}"                       # systemui 源码(git 仓库)副本
SDK_ROOT="${SDK_ROOT:-/home/cx/os/6.1/prebuilts/ohos-sdk/linux}"   # 完整 api23 SDK(6.1.0.35)
HVIGOR_VERSION="${HVIGOR_VERSION:-6.23.5}"            # 与 DevEco 6.1 捆绑版一致
NODE_BIN="${NODE_BIN:-/home/cx/.nvm/versions/node/v22.23.1/bin}"
LICENSE_HASH="24e7fd9dd6c90c4c7162955ea7a4f33e22f4a68fc7ce9e685859cf02a3c9720e"

[ -d "$SRC" ] || { echo "ERR: src not found: $SRC"; exit 1; }
[ -d "$SDK_ROOT/23/ets" ] || { echo "ERR: sdk-root missing 23/ets: $SDK_ROOT"; exit 1; }
export PATH="$NODE_BIN:$PATH"

echo "== [1/6] SDK licenses 接受标记 =="
mkdir -p "$SDK_ROOT/licenses"
printf '%s' "$LICENSE_HASH" > "$SDK_ROOT/licenses/OpenHarmony-SDK.sha256"

echo "== [2/6] 官方 hvigor CLI(npm) =="
[ -d "$SRC/node_modules/@ohos/hvigor" ] || {
  echo '{"name":"sysui-build-env","version":"1.0.0","private":true}' > "$SRC/package.json"
  ( cd "$SRC" && npm i -D "@ohos/hvigor@$HVIGOR_VERSION" "@ohos/hvigor-ohos-plugin@$HVIGOR_VERSION" --no-fund --no-audit 2>&1 | tail -1 )
}

echo "== [3/6] 构建参数: api23 SDK + compileSdkVersion 23(仅构建副本) =="
echo "sdk.dir=$SDK_ROOT" > "$SRC/local.properties"

echo "== [4/6] 兼容模式 linter 元数据(Linux 官方 SDK 与厂商一致行为) =="
MJSON="$SRC/product/phone/dropdownpanel/src/main/module.json5"
grep -q "Api11ArkTSCheckMode" "$MJSON" || python3 - "$MJSON" <<'PY'
import sys, re
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = s.replace(
  '"ArkTSPartialUpdate",\n        "value": "true"',
  '"ArkTSPartialUpdate",\n        "value": "true"\n      },\n      {\n        "name": "Api11ArkTSCheckMode",\n        "value": "DoArkTSCheckInCompatibleModeInApi11"'
)
open(p, "w", encoding="utf-8").write(s)
PY

echo "== [5/6] 模块级 oh_modules/@ohos/* 符号链接(file: 依赖) =="
python3 - "$SRC" <<'PY'
import os, json, re, sys
root = sys.argv[1]
def parse_ohpkg(p):
    return json.loads(re.sub(r"//.*", "", open(p, encoding="utf-8").read()))
for dirpath, dirnames, filenames in os.walk(root):
    if any(x in dirpath for x in ("/build/", "/.hvigor", "/oh_modules", "/node_modules", "/.git")):
        dirnames[:] = [d for d in dirnames if d not in ("build", ".hvigor", "oh_modules", "node_modules", ".git")]
        continue
    if "oh-package.json5" not in filenames:
        continue
    try:
        d = parse_ohpkg(os.path.join(dirpath, "oh-package.json5"))
    except Exception:
        continue
    deps = d.get("dependencies") or {}
    mdir = os.path.join(dirpath, "oh_modules", "@ohos")
    os.makedirs(mdir, exist_ok=True)
    for name, spec in deps.items():
        if spec.startswith("file:"):
            link = os.path.join(mdir, name)
            if not os.path.exists(link):
                os.symlink(os.path.normpath(os.path.join(dirpath, spec[5:])), link)
PY

echo "== [6/6] 构建(assembleHap) =="
( cd "$SRC" && node node_modules/@ohos/hvigor/bin/hvigor.js \
    --mode module -p product=default -p module="$MODULE@default" -p buildMode=release \
    assembleHap --no-daemon )
echo "== 完成 =="
ls -la "$SRC/product/phone/dropdownpanel/build/default/outputs/default/"*-signed.hap 2>/dev/null || true
