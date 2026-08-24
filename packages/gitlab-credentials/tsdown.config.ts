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
    format: 'esm',
    platform: 'browser',
    target: 'es2022',
    deps: { neverBundle: [...neverBundle, 'react', 'react-dom', 'react-dom/client', 'react/jsx-runtime'] },
    dts: { entry: 'src/client/index.ts' },
    outExtension,
  },
])
