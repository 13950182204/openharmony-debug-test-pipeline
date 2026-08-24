/**
 * tsdown dual-half build: host entry (src/index.ts) -> lib/index.js and
 * browser entry (src/client/index.ts) -> lib/client.js. All @deepseek-ai
 * SDK packages stay external (the dsh runtime provides them at load time);
 * react stays external for the browser half. The client entry imports only
 * types from the SDK, so the runtime bundle carries react + the panel only.
 */
import { defineConfig } from 'tsdown'

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
    deps: { neverBundle: [...neverBundle, 'react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'] },
    banner: 'window.__ModuleLoader__.load({ id: "@linxin666/dsh-gitlab-credentials", factory: (require) => {\n\t\tvar module = { exports: {} };\n\t\tvar exports = module.exports;\n',
    footer: '\n\t\treturn module.exports;\n\t}\n});',
    dts: { entry: 'src/client/index.ts' },
    outExtension,
  },
])
