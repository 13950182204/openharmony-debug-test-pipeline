# Upstream Checks

Before finalizing a fix, check whether OpenHarmony upstream already changed the same testcase, helper, framework, service, driver, or product source. This is required before editing product/framework source for an XTS failure.

## Repositories

- ACTS: `https://gitcode.com/openharmony/xts_acts.git`
- HATS: `https://gitcode.com/openharmony/xts_hats.git`
- DCTS: `https://gitcode.com/openharmony/xts_dcts.git`
- Product/framework/service source: infer the GitCode repository from the local path, then verify it with `git ls-remote`.
  - Example: `foundation/communication/bluetooth_service/...` -> `https://gitcode.com/openharmony/communication_bluetooth_service.git`
  - Example: `foundation/communication/bluetooth/...` -> `https://gitcode.com/openharmony/communication_bluetooth.git`
  - In a local monorepo or vendor mirror, the local `origin` may not reveal the OpenHarmony split repository. Use the path mapping and verify on GitCode.

Default branch to check when the user does not specify a branch:

- `OpenHarmony-6.1-Release`

If the user writes a lowercase branch such as `openharmony-6.1-release`, normalize to the GitCode release branch style but verify branch existence. Prefer the exact user branch when it exists.

If the user asks to check both `6.1-release` and `6.1-LTS`, normalize and check both `OpenHarmony-6.1-Release` and `OpenHarmony-6.1-LTS`. Do not stop after the default branch when multiple branches are requested.

## Method

1. Identify what may need changing:
   - Testcase source: map to ACTS/HATS/DCTS.
   - Product/framework/service source: map the local source path to the corresponding OpenHarmony GitCode split repository.
   - For ACTS failures, check `https://gitcode.com/openharmony/xts_acts.git` for testcase-side changes and also check the owning split repository for product/framework/service changes, such as `multimedia_audio_framework`, `multimedia_player_framework`, `multimedia_camera_framework`, `graphic_graphic_2d`, or `communication_bluetooth`.
2. Check branch existence:

```bash
git ls-remote --heads <repo-url> OpenHarmony-6.1-Release
```

3. Fetch the branch shallowly into `/tmp` when source comparison or history search is needed:

```bash
git clone --depth 80 --filter=blob:none --no-checkout \
  --branch OpenHarmony-6.1-Release <repo-url> /tmp/<repo>_6_1_check
```

4. Locate the exact testcase path with:

```bash
git -C /tmp/<repo>_6_1_check ls-tree -r --name-only HEAD | rg '<case or file name>'
```

For product/framework/service source, use the local path relative to that split repository.

5. Compare the local source span with upstream:

```bash
git -C /tmp/<repo>_6_1_check show HEAD:<path> | sed -n '<start>,<end>p'
```

6. Search for similar upstream patches before inventing a local fix:

```bash
git -C /tmp/<repo>_6_1_check log --oneline -- <path>
git -C /tmp/<repo>_6_1_check log --oneline -S'<error code, log text, enum, or testcase name>' -- <path>
git -C /tmp/<repo>_6_1_check show --stat --oneline <commit>
git -C /tmp/<repo>_6_1_check show --unified=80 <commit> -- <path>
```

For runtime failures, search with the decisive evidence from the report, such as an error code, hilog phrase, callback name, or enum. If upstream has a nearby but incomplete patch, state exactly which path it covers and why the current report still needs an additional local change.

## Reporting

Always report:

- repository
- branch
- commit hash from `git ls-remote`
- upstream file path
- whether the testcase or product/framework source differs from local
- search terms or local paths used for split-repository checks
- minimal suggested backport
- if no matching patch exists, the exact search terms or files checked

If the upstream change only changes a mutable external URL, still explain what the test is trying to verify and why the new URL is more suitable.
