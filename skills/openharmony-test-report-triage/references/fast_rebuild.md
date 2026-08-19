# Testcase Single-Module Build And Fast Rebuild

When a testcase source file is changed, compile the smallest owning test package or module before reporting the fix as locally validated.

## Standard ACTS Suite Wrapper

Use this when the target has not been generated yet, when `out/<product>/build.ninja` is missing, or when GN/build metadata changed.

Run from the ACTS root:

```bash
cd /home/cx/os/xts/test/xts/acts
./build.sh product_name=rk3568 system_size=standard target_arch=arm64 \
  suite=test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest
```

Multiple suites can be comma-separated:

```bash
./build.sh product_name=rk3568 system_size=standard target_arch=arm64 \
  suite=test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest,test/xts/acts/sensors/sensor_standard:sensor_js_test
```

This wrapper runs target discovery and calls root `build.sh --build-only-gn`, then `gn desc`, then a fast ninja step. It may print many `suite_type mismatch, no need build ...` lines during GN and GN DESC phases.

## Fast Rebuild For ETS-Only Changes

Use this after the GN output has already been generated and only testcase ETS/resources changed. Run from the source root:

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

Why this is faster:
- Root `--fast-rebuild` skips prepare, preloader, and GN generation.
- It also avoids the XTS wrapper's `gn desc //test/xts/acts:xts_acts deps --tree` target-picking pass.
- Therefore it avoids most `suite_type mismatch` noise.

Verified ACTS example, 2026-06-01:
- `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`
- `_gn is 0.0 s`
- no `suite_type mismatch` spam
- no `[0/1] Regenerating ninja files`
- ninja built the changed app/signing path in about 40 seconds

Preconditions:
- `out/rk3568/build.ninja` exists and was generated with compatible product, target CPU, and XTS args.
- The target exists in `build.ninja`, for example `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`.
- No `BUILD.gn`, `suite.gni`, product config, toolchain, or target dependency graph change needs GN regeneration.

If fast rebuild fails because the target is missing or Ninja files are stale, fall back to the standard ACTS suite wrapper once.

If root fast rebuild enters `[0/1] Regenerating ninja files`, it is no longer on the fast path. In this workspace that regeneration can fail with `assert(XTS_SUITENAME != "")` from DCTS/HATS/ACTS test package GN files because the wrapper environment is not present. For an ETS-only testcase rebuild after a successful GN generation, check why Ninja thinks the manifest is dirty:

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

If only generated `obj/.../*_deps_data.json` files are newer, touching the stamp is an acceptable local fast-rebuild recovery:

```bash
cd /home/cx/os/xts
touch out/rk3568/build.ninja.stamp
```

If a failed regeneration leaves `build.ninja` as a tiny regeneration stub, restore the backup created by the XTS accurate-build path before retrying:

```bash
cp -p out/rk3568/build.ninja.bkp out/rk3568/build.ninja
cp -p out/rk3568/build.ninja.d.bkp out/rk3568/build.ninja.d
cp -p out/rk3568/build.ninja.stamp.bkp out/rk3568/build.ninja.stamp
touch out/rk3568/build.ninja.stamp
```

In `/home/cx/os/openharmony_V6.1`, the ACTS suite wrapper may also enter this
regeneration path after `gn desc` and still recover successfully. While the
`gn --regeneration gen .` process is running, `out/<product>/build.ninja` can
temporarily shrink to a small stub. Do not restore backups while the process is
active. First wait for the build command to exit. If it succeeds, verify that
`build.ninja` has returned to its normal large size and continue. Restore
`build.ninja.bkp` only after a failed/terminated regeneration leaves the stub in
place.

## ACTS Archive Refresh Check

For ACTS hap tests, the fast app/signing action may refresh:

```text
out/rk3568/suites/haps/<Module>.hap
```

but the final suite testcase copy may remain stale:

```text
out/rk3568/suites/acts/acts/testcases/<Module>.hap
```

because the suite archive rule uses `suites/haps/<Module>.hap` as a command argument, not a normal Ninja input. Check both timestamps after a successful fast rebuild.

If `suites/haps/<Module>.hap` is fresh but `suites/acts/acts/testcases/<Module>.hap` is stale, force the owning module stamp newer and rebuild only the public test target:

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

Expected output is a short Ninja run containing:

```text
ACTION //test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest
STAMP obj/test/xts/acts/request/newRequestAuthorityTest/ActsRequestAuthorityTest.stamp
```

## JS HAP Cache Refresh

For JS HAP tests, a successful Ninja run can still package stale ABC content if Hvigor considers
`entry/build/default/outputs/ohosTest/entry-ohosTest-unsigned.hap` up to date. Symptoms:

- `compile_app.py` runs, but Hvigor reports many `UP-TO-DATE` tasks.
- `out/<product>/suites/acts/acts/testcases/<Module>.hap` has a fresh timestamp, but device hilog still shows old assertion text or old case flow.
- `strings <Module>.hap` still contains old assertion text, or `entry/build/.../entry-ohosTest-unsigned.hap` is older than the source change.

For a JS testcase source edit, verify the actual package content, not only the final HAP timestamp:

```bash
stat -c '%y %s %n' \
  test/xts/acts/<module>/entry/src/ohosTest/js/test/<Case>.test.js \
  test/xts/acts/<module>/entry/build/default/outputs/ohosTest/entry-ohosTest-unsigned.hap \
  out/<product>/suites/acts/acts/testcases/<Module>.hap

strings out/<product>/suites/acts/acts/testcases/<Module>.hap | rg '<new log text>|<old assertion text>'
```

If the source is newer than `entry-ohosTest-unsigned.hap`, or the HAP still contains old strings,
clear the JS project cache and the owning module's output before rebuilding:

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

Expected output includes a full Hvigor rebuild:

```text
Hvigor info: > hvigor Finished :entry:clean
Hvigor info: > hvigor Finished :entry:ohosTest@LegacyOhosTestBuildJS
Hvigor info: > hvigor Finished :entry:ohosTest@LegacyPackageHap
```

Then re-check:

```bash
strings out/<product>/suites/acts/acts/testcases/<Module>.hap | rg '<new log text>|<old assertion text>'
```

Only copy to the external ACTS runner and rerun after the final archived HAP content is confirmed.

## OpenHarmony V6.1 ACTS Build Loop Notes

In the `/home/cx/os/openharmony_V6.1` workspace, users often expect a single-module ACTS build to be quick. Be explicit about the actual phases:

- `test/xts/acts/build.sh product_name=... suite=<path>:<target>` first runs root `build.sh --build-only-gn`.
- It then runs `gn desc out/<product> //test/xts/acts:xts_acts deps --tree` to pick the requested target from the full ACTS dependency tree.
- Only after target picking does it run `ninja ... <target> deploy_testtools`.
- Many `suite_type mismatch, no need build ...` lines during GN and GN DESC are normal filtering noise, not test build failures.
- If the process appears slow but `ps -ef --forest` shows `gn`, `gn desc`, or `ninja` still active, it is normally build-system overhead rather than a hang.

For JS/ETS ACTS HAPs, a fresh signed HAP timestamp is not enough. Verify the compiled ABC inside the HAP:

```bash
cd /home/cx/os/openharmony_V6.1
stat -c '%y %s %n' \
  test/xts/acts/<module>/entry/src/ohosTest/ets/test/<Case>.ets \
  out/<product>/suites/haps/<Module>.hap \
  out/<product>/suites/acts/acts/testcases/<Module>.hap

unzip -l out/<product>/suites/haps/<Module>.hap | sed -n '1,80p'
```

If `out/<product>/suites/haps/<Module>.hap` is fresh but `ets/modules.abc` still has the old time, assume stale Hvigor or module output cache. Clean from the repository root, not from `test/xts/acts`, otherwise paths such as `out/<product>/...` may resolve to the wrong directory:

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

Then rebuild with the suite wrapper when target discovery or archive generation is needed:

```bash
cd /home/cx/os/openharmony_V6.1/test/xts/acts
./build.sh product_name=<product> system_size=standard target_arch=arm64 \
  suite=test/xts/acts/<module>:<target>
```

After a valid build, sync all generated testcase files to both the runner directory and the user's export directory when requested:

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

For HATS native testcase modules, the generated artifacts are usually an ELF plus
matching `.json` and `.moduleInfo` files under
`out/<product>/suites/hats/testcases/`. Sync them to both the runner directory
and the user's export directory, mirroring the ACTS `new_acts_testcase` flow:

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

Rerun and validate from the report, not only the console:

```bash
cd /home/cx/os/acts
./run.sh run -l <Module>
```

Parse the latest report:

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

Treat `disabled`, `blocked`, or `missed` cases after a long timeout or device disconnect as possible fallout. Use `module_run.log`, `task_log.log`, and hilog to identify the first failing case before changing more code.

## Reporting

Always report:
- command used
- whether it was suite wrapper or fast rebuild
- pass/fail result
- generated artifact path; for ACTS hap tests, confirm the final `out/rk3568/suites/acts/acts/testcases/<Module>.hap` timestamp, not only `out/rk3568/suites/haps/<Module>.hap`
- fallback reason if fast rebuild was not used
