/**
 * tsdown dual-half build: host entry (src/index.ts) -> lib/index.js and
 * browser entry (src/client/index.ts) -> lib/client.js. All @deepseek-ai
 * SDK packages stay external (the dsh runtime provides them at load time);
 * react stays external for the browser half. The client entry imports only
 * types from the SDK, so the runtime bundle carries react + the panel only.
 */
import { defineConfig } from 'tsdown'
import { readFileSync } from 'node:fs'

/**
 * 包名从 package.json 读取，**不再硬编码**：`lib/client.js` 的 `ModuleLoader.load({ id })`
 * 必须与包名一致，否则浏览器半身会静默不注册（服务端加载正常、设置页不出现）。
 * 此处若与 package.json 各写一份，改名时必然漏改一处。
 */
const packageName: string = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
).name

const neverBundle = [/^@deepseek-ai\//, /^node:/, 'schemastery']
const outExtension = () => ({ js: '.js', dts: '.d.ts' })

export default defineConfig([
  {
    entry: { index: 'src/index.ts' },
    outDir: 'lib',
    format: 'esm',
    platform: 'node',
    target: 'node18',
    deps: { neverBundle },
    dts: { entry: 'src/index.ts' },
    outExtension,
    clean: true,
  },
  {
    entry: { client: 'src/client/index.ts' },
    outDir: 'lib',
    // rc.2 client-modules consumes lazy CJS factories registered through
    // ModuleLoader.load; a plain ESM export is fetched but never registered.
    format: 'cjs',
    platform: 'browser',
    target: 'es2022',
    deps: { neverBundle: [...neverBundle, /^react(-dom)?(\/|$)/] },
    banner: `window.__ModuleLoader__.load({ id: ${JSON.stringify(packageName)}, factory: (require) => {\n\t\tvar module = { exports: {} };\n\t\tvar exports = module.exports;\n`,
    footer: '\n\t\treturn module.exports;\n\t}\n});',
    dts: { entry: 'src/client/index.ts' },
    outExtension,
  },
])
