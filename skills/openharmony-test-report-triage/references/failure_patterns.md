# Failure Patterns

These are reusable patterns, not automatic conclusions. Always verify with the current report, source, and product config.

## Product lacks hardware but model/mock is exposed

Symptoms:
- Tests discover a sensor/vibrator/camera/etc. on a product that physically lacks it.
- HDF/common inherited config enables a peripheral model.
- Hilog or API output shows mock names such as `sensor_test` or preset vibrator effects.

Fix direction:
- Product-side feature override is preferred.
- Explicitly disable the relevant peripheral model or remove the advertised capability for that product.
- Do not patch tests to ignore hardware that the product still advertises.

## Timeout with many blocked cases

Symptoms:
- One testcase fails with timeout.
- Many later cases are blocked in the same module.
- `module_run.log` shows the runner stopped after the first timeout.

Fix direction:
- Focus on the first timeout case.
- Check whether a promise/callback error path fails to call `done()`.
- Check whether the product/framework failed to deliver the callback that the test waits for.

## Floating-point or timing tolerance

Symptoms:
- Actual value is extremely close to expected, such as `1e-11` vs `0`.
- The API is mathematically or timing sensitive.

Fix direction:
- Use a small epsilon or tolerance when the API contract allows it.
- Keep the tolerance narrow and explain the numeric evidence.

## Callback/state machine edge

Symptoms:
- Operation callback returns success, but a later state/event callback never arrives.
- Test logs stop at "wait for callback" and then timeout.

Fix direction:
- Identify which framework event maps to the JS/native callback.
- Product/framework root fix should emit the missing state/event when API semantics require it.
- Test-side workaround may avoid exact timing or EOF boundaries, but label it as a workaround when it does not fix product semantics.

## JS HAP async API timeout with working sync path

Symptoms:
- A JS ACTS HAP case times out at 5000 ms.
- Hilog shows the case entered, then stops after a promise/callback API call or before `done()`.
- Adjacent sync API cases for the same capability pass.
- Replacing a callback helper such as `getProperties(callback)` with an available sync equivalent such as
  `getWindowProperties()` removes one source of nondeterminism.

Fix direction:
- First prove the missing callback/promise return with hilog around the exact case.
- Check whether the API has sync variants and whether existing cases already validate those sync variants.
- For a local XTS workaround, keep a real assertion on a deterministic observable result, such as return type or error code.
- Do not blindly assert a state transition if the product can legally return without changing visible state on this board.
- After rebuilding a JS HAP, confirm actual ABC/HAP contents with `strings`; if stale strings remain, follow the JS HAP cache refresh flow in `fast_rebuild.md`.

## External network resource drift

Symptoms:
- Only a network-dependent request/download testcase fails.
- XML may have an empty message, but hilog shows an assertion mismatch.
- The testcase uses a public URL and assumes specific headers, content length, status, or response behavior.
- Runtime response headers differ from the test assumption.

Example:
- `SUB_Request_DownloadManagement_Download_0100` uses `https://gitee.com`.
- The testcase description says it is testing an undefined file size and asserts `pro.sizes[0] == -1`.
- The runtime response includes `content-length`, so request reports a positive size such as `642022`.
- Upstream `xts_acts` `OpenHarmony-6.1-Release` changed this URL to `https://weibo.com` while keeping the `-1` assertion.

Detailed case record:
- Report: `zxts/ActsRequestAuthorityTest`, total 259, passed 258, failed 1, blocked 0.
- Module/case: `requestDownloadJSUnit#SUB_Request_DownloadManagement_Download_0100`.
- What it tests: request-agent download should report unknown file size as `-1`.
- Test input: `action = DOWNLOAD`, public URL originally `https://gitee.com`, save path `./SUB_Request_DownloadManagement_Download_0100`, network `ANY`, overwrite enabled.
- Test flow: create `request.agent.Config`, call `request.agent.create(baseContext, config)`, register `task.on('completed', completedCallback)`, call `task.start()`, read `pro.sizes[0]` in the completed callback, assert `pro.sizes[0] == -1`, then call `done()`.
- Expected: completed progress reports `sizes[0] = -1` because the resource is meant to have undefined size.
- Actual evidence: hilog recorded completed progress with `processed: 642022`, `sizes: [642022]`, `content-type: text/html; charset=utf-8`, and `content-length: 642022`; the assertion failed with `expect 642022 equals -1`.
- Root cause: the testcase depended on mutable external URL behavior. `https://gitee.com` returned a normal HTML response with `Content-Length`, so the request framework correctly reported a known size.
- Upstream check: ACTS repo `https://gitcode.com/openharmony/xts_acts.git`, branch `OpenHarmony-6.1-Release`, commit `9830c07a91a0cdaa19dc0eab4fd99b8967bafce2`, path `request/newRequestAuthorityTest/entry/src/ohosTest/ets/test/requestDownload.test.ets`, changed the URL to `https://weibo.com` and kept `expect(pro.sizes[0]).assertEqual(-1)`.
- Minimal local backport: in the matching monorepo path, change only this case URL from `https://gitee.com` to `https://weibo.com`.
- Testcase compile: because this is a testcase-source change, compile `test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest`. After GN output exists, prefer root `build.sh --fast-rebuild --build-target test/xts/acts/request/newRequestAuthorityTest:ActsRequestAuthorityTest` to avoid repeated GN and `suite_type mismatch` noise.

Fix direction:
- Prefer replacing the public URL with a controlled test endpoint that has the intended response behavior.
- When upstream already changed the URL or fixture, prefer backporting that minimal change first.
- If the API contract allows both unknown and known size, adjust the assertion to accept both and explain the weakened test meaning.
- Do not change product/framework code to hide a valid `Content-Length` just to satisfy a stale test assumption.
