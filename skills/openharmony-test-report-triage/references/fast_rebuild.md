# 用例单模块构建与快速重建

改动用例源码文件后，先编译最小的所属测试包或模块，再报告修复为本地验证通过。

## 标准 ACTS 套件包装

目标尚未生成、`out/<product>/build.ninja` 缺失或 GN/构建元数据变化时使用。

从 ACTS 根目录运行：

```bash
cd /home/cx/os/xts/test/xts/acts
./build.sh product_name=rk3568 system_size=standard target_arch=arm64 \
  suite=test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest
```

多个套件可用逗号分隔：

```bash
./build.sh product_name=rk3568 system_size=standard target_arch=arm64 \
  suite=test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest,test/xts/acts/sensors/sensor_standard:sensor_js_test
```

该包装脚本运行目标发现，调用根目录 `build.sh --build-only-gn`，然后 `gn desc`，再走一次快速 ninja 步骤。GN 与 GN DESC 阶段可能打印大量 `suite_type mismatch, no need build ...` 行。

## 仅 ETS 改动的快速重建

GN 输出已生成且只有用例 ETS/资源变化时使用。从源码根运行：

```bash
cd /home/cx/os/xts
./build.sh --fast-rebuild --product-name rk3568 --target-cpu arm64 \
  --deps-guard false \
  --gn-args build_xts=true \
  --gn-args skip_generate_module_list_file=true \
  --gn-args is_standard_system=true \
  --build-target test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest \
  --build-target deploy_testtools \
  --get-warning-list=false \
  --stat-ccache=true \
  --compute-overlap-rate=false
```

更快的原因：
- 根目录 `--fast-rebuild` 跳过 prepare、preloader 与 GN 生成。
- 它同时避开 XTS 包装脚本的 `gn desc //test/xts/acts:xts_acts deps --tree` 目标挑选过程。
- 因此避开了大部分 `suite_type mismatch` 噪音。

已验证的 ACTS 示例，2026-06-01：
- `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`
- `_gn is 0.0 s`
- 无 `suite_type mismatch` 刷屏
- 无 `[0/1] Regenerating ninja files`
- ninja 用约 40 秒构建了变更的 app/签名路径

前置条件：
- `out/rk3568/build.ninja` 存在，且由兼容的产品、目标 CPU 与 XTS 参数生成。
- 目标存在于 `build.ninja`，例如 `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`。
- 没有 `BUILD.gn`、`suite.gni`、产品配置、工具链或目标依赖图改动需要 GN 重新生成。

快速重建因目标缺失或 Ninja 文件过期失败时，回退到标准 ACTS 套件包装一次。

根目录快速重建进入 `[0/1] Regenerating ninja files` 时，它已不在快速路径上。在该工作区，这种重新生成可能因 DCTS/HATS/ACTS 测试包 GN 文件中的 `assert(XTS_SUITENAME != "")` 失败，因为包装环境不存在。对成功 GN 生成后的 ETS-only 用例重建，检查 Ninja 为何认为 manifest 脏：

```bash
cd /home/cx/os/xts/out/rk3568
stamp=build.ninja.stamp
tr ' ' '\n' < build.ninja.d | sed '1s/^build.ninja.stamp://' | sed 's/\\$//' | sed '/^$/d' |
  while read -r dep; do
    if [ ! -e "$dep" ]; then echo "missing $dep";
    elif [ "$dep" -nt "$stamp" ]; then echo "newer $dep";
    fi
  done | sed -n '1,80p'
```

只有生成的 `obj/.../*_deps_data.json` 文件更新时，touch 该 stamp 是可接受的本地快速重建恢复：

```bash
cd /home/cx/os/xts
touch out/rk3568/build.ninja.stamp
```

失败的重新生成把 `build.ninja` 留成极小的重新生成 stub 时，重试前恢复 XTS 精确构建路径创建的备份：

```bash
cp -p out/rk3568/build.ninja.bkp out/rk3568/build.ninja
cp -p out/rk3568/build.ninja.d.bkp out/rk3568/build.ninja.d
cp -p out/rk3568/build.ninja.stamp.bkp out/rk3568/build.ninja.stamp
touch out/rk3568/build.ninja.stamp
```

在 `/home/cx/os/openharmony_V6.1` 中，ACTS 套件包装也可能在 `gn desc` 后进入该重新生成路径，仍能成功恢复。`gn --regeneration gen .` 进程运行期间，`out/<product>/build.ninja` 可能暂时缩成小 stub。进程活跃时不要恢复备份。先等构建命令退出。成功则验证 `build.ninja` 已恢复到正常大尺寸并继续。只在失败/终止的重新生成留下 stub 时才恢复 `build.ninja.bkp`。

## ACTS 归档刷新检查

对 ACTS hap 测试，快速的 app/签名动作可能刷新：

```text
out/rk3568/suites/haps/<Module>.hap
```

但最终套件用例副本可能仍是旧的：

```text
out/rk3568/suites/acts/acts/testcases/<Module>.hap
```

因为套件归档规则把 `suites/haps/<Module>.hap` 用作命令参数，而不是普通 Ninja 输入。成功快速重建后同时检查两个时间戳。

`suites/haps/<Module>.hap` 新鲜但 `suites/acts/acts/testcases/<Module>.hap` 过期时，把所属模块 stamp 强制更新并只重建公共测试目标：

```bash
cd /home/cx/os/xts
touch out/rk3568/obj/test/xts/acts/request/newRequestAuthorityTest/module_ActsRequestAuthorityTest.stamp
./build.sh --fast-rebuild --product-name rk3568 --target-cpu arm64 \
  --deps-guard false \
  --gn-args build_xts=true \
  --gn-args skip_generate_module_list_file=true \
  --gn-args is_standard_system=true \
  --build-target test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest \
  --get-warning-list=false \
  --stat-ccache=true \
  --compute-overlap-rate=false
```

预期输出是一段包含以下内容的短 Ninja 运行：

```text
ACTION //test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest
STAMP obj/test/xts/acts/request/newRequestAuthorityTest/ActsRequestAuthorityTest.stamp
```

## JS HAP 缓存刷新

对 JS HAP 测试，如果 Hvigor 认为 `entry/build/default/outputs/ohosTest/entry-ohosTest-unsigned.hap` 是最新的，成功的 Ninja 运行仍可能打包陈旧的 ABC 内容。症状：

- `compile_app.py` 运行了，但 Hvigor 报告大量 `UP-TO-DATE` 任务。
- `out/<product>/suites/acts/acts/testcases/<Module>.hap` 时间戳新鲜，但设备 hilog 仍显示旧断言文本或旧用例流程。
- `strings <Module>.hap` 仍包含旧断言文本，或 `entry/build/.../entry-ohosTest-unsigned.hap` 比源码改动更旧。

对 JS 用例源码编辑，验证实际包内容，而不只是最终 HAP 时间戳：

```bash
stat -c '%y %s %n' \
  test/xts/acts/<module>/entry/src/ohosTest/js/test/<Case>.test.js \
  test/xts/acts/<module>/entry/build/default/outputs/ohosTest/entry-ohosTest-unsigned.hap \
  out/<product>/suites/acts/acts/testcases/<Module>.hap

strings out/<product>/suites/acts/acts/testcases/<Module>.hap | rg '<new log text>|<old assertion text>'
```

源码比 `entry-ohosTest-unsigned.hap` 新，或 HAP 仍含旧字符串时，重建前清除 JS 工程缓存与所属模块输出：

```bash
cd /home/cx/os/xts
rm -rf test/xts/acts/<module>/entry/build \
       test/xts/acts/<module>/build \
       test/xts/acts/<module>/.hvigor \
       out/<product>/obj/test/xts/acts/<module>/module_<target> \
       out/<product>/obj/test/xts/acts/<module>/module_<target>_compile_app.stamp \
       out/<product>/suites/haps/<Module>.hap \
       out/<product>/suites/acts/acts/testcases/<Module>.hap \
       out/<product>/suites/acts/acts/testcases/<Module>.json \
       out/<product>/suites/acts/acts/testcases/<Module>.moduleInfo

./build.sh --product-name <product> --fast-rebuild \
  --build-target test/xts/acts/<module>:module_<target>_compile_app \
  --build-target test/xts/acts/<module>:<target> \
  --gn-args build_xts=true \
  --gn-args skip_generate_module_list_file=true \
  --gn-args is_standard_system=true \
  --ninja-args=-v
```

预期输出包含一次完整 Hvigor 重建：

```text
Hvigor info: > hvigor Finished :entry:clean
Hvigor info: > hvigor Finished :entry:ohosTest@LegacyOhosTestBuildJS
Hvigor info: > hvigor Finished :entry:ohosTest@LegacyPackageHap
```

然后复查：

```bash
strings out/<product>/suites/acts/acts/testcases/<Module>.hap | rg '<new log text>|<old assertion text>'
```

确认最终归档 HAP 内容后才复制到外部 ACTS 运行器并重跑。

## OpenHarmony V6.1 ACTS 构建循环说明

在 `/home/cx/os/openharmony_V6.1` 工作区，用户常期望单模块 ACTS 构建很快。明确说明实际阶段：

- `test/xts/acts/build.sh product_name=... suite=<path>:<target>` 先运行根目录 `build.sh --build-only-gn`。
- 然后运行 `gn desc out/<product> //test/xts/acts:xts_acts deps --tree`，从完整 ACTS 依赖树中挑选请求的目标。
- 目标挑选完成后才运行 `ninja ... <target> deploy_testtools`。
- GN 与 GN DESC 阶段大量 `suite_type mismatch, no need build ...` 行是正常的过滤噪音，不是构建失败。
- 进程看似缓慢但 `ps -ef --forest` 显示 `gn`、`gn desc` 或 `ninja` 仍在活动时，通常是构建系统开销而非挂起。

对 JS/ETS ACTS HAP，新鲜签名 HAP 时间戳不够。验证 HAP 内编译后的 ABC：

```bash
cd /home/cx/os/openharmony_V6.1
stat -c '%y %s %n' \
  test/xts/acts/<module>/entry/src/ohosTest/ets/test/<Case>.ets \
  out/<product>/suites/haps/<Module>.hap \
  out/<product>/suites/acts/acts/testcases/<Module>.hap

unzip -l out/<product>/suites/haps/<Module>.hap | sed -n '1,80p'
```

`out/<product>/suites/haps/<Module>.hap` 新鲜但 `ets/modules.abc` 仍是旧时间时，视为 Hvigor 或模块输出缓存陈旧。从仓库根清理，不要从 `test/xts/acts` 清理，否则 `out/<product>/...` 等路径可能解析到错误目录：

```bash
cd /home/cx/os/openharmony_V6.1
rm -rf test/xts/acts/<module>/.hvigor \
       test/xts/acts/<module>/build \
       test/xts/acts/<module>/entry/build \
       out/<product>/obj/test/xts/acts/<module> \
       out/<product>/suites/haps/<Module>.hap \
       out/<product>/suites/acts/acts/testcases/<Module>.hap \
       out/<product>/suites/acts/acts/testcases/<Module>.json \
       out/<product>/suites/acts/acts/testcases/<Module>.moduleInfo
```

需要目标发现或归档生成时，用套件包装重建：

```bash
cd /home/cx/os/openharmony_V6.1/test/xts/acts
./build.sh product_name=<product> system_size=standard target_arch=arm64 \
  suite=test/xts/acts/<module>:<target>
```

有效构建后，被要求时把生成的全部用例文件同步到运行器目录与用户导出目录：

```bash
cd /home/cx/os/openharmony_V6.1
mkdir -p /home/cx/os/acts/testcases /home/cx/os/acts/new_acts_testcase
cp -p out/<product>/suites/acts/acts/testcases/<Module>.hap \
      out/<product>/suites/acts/acts/testcases/<Module>.json \
      out/<product>/suites/acts/acts/testcases/<Module>.moduleInfo \
      /home/cx/os/acts/testcases/
cp -p out/<product>/suites/acts/acts/testcases/<Module>.hap \
      out/<product>/suites/acts/acts/testcases/<Module>.json \
      out/<product>/suites/acts/acts/testcases/<Module>.moduleInfo \
      /home/cx/os/acts/new_acts_testcase/
sha256sum /home/cx/os/acts/testcases/<Module>.* \
          /home/cx/os/acts/new_acts_testcase/<Module>.*
```

对 HATS 原生测试用例模块，生成产物通常是 ELF 加上 `out/<product>/suites/hats/testcases/` 下匹配的 `.json` 与 `.moduleInfo` 文件。把它们同步到运行器目录与用户导出目录，镜像 ACTS 的 `new_acts_testcase` 流程：

```bash
cd /home/cx/os/openharmony_V6.1
mkdir -p /home/cx/os/hats/testcases /home/cx/os/hats/new_hats_testcase
cp -p out/<product>/suites/hats/testcases/<Module> \
      out/<product>/suites/hats/testcases/<Module>.json \
      out/<product>/suites/hats/testcases/<Module>.moduleInfo \
      /home/cx/os/hats/testcases/
cp -p out/<product>/suites/hats/testcases/<Module> \
      out/<product>/suites/hats/testcases/<Module>.json \
      out/<product>/suites/hats/testcases/<Module>.moduleInfo \
      /home/cx/os/hats/new_hats_testcase/
sha256sum /home/cx/os/hats/testcases/<Module>* \
          /home/cx/os/hats/new_hats_testcase/<Module>*
```

用报告而非仅控制台重跑并验证：

```bash
cd /home/cx/os/acts
./run.sh run -l <Module>
```

解析最新报告：

```bash
python3 - <<'PY'
import xml.etree.ElementTree as ET
from pathlib import Path
p = Path('/home/cx/os/acts/reports/<latest-report>')
for f in [p / 'summary_report.xml', p / 'result/<Module>.xml']:
    root = ET.parse(f).getroot()
    print(f, root.attrib)
    for tc in root.iter('testcase'):
        if tc.get('result') == 'false' and tc.get('status') == 'run':
            print('FAIL', tc.get('classname'), tc.get('name'), tc.get('time'), tc.get('message'))
PY
```

长时间超时或设备断开后的 `disabled`、`blocked`、`missed` 用例视为可能的连带。改更多代码前用 `module_run.log`、`task_log.log` 与 hilog 定位第一个失败用例。

## 汇报

始终汇报：
- 使用的命令
- 是套件包装还是快速重建
- 通过/失败结果
- 生成的产物路径；对 ACTS hap 测试，确认最终 `out/rk3568/suites/acts/acts/testcases/<Module>.hap` 时间戳，而不只是 `out/rk3568/suites/haps/<Module>.hap`
- 未用快速重建时的回退原因
