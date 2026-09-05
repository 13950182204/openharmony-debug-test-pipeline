#!/usr/bin/env node
/**
 * A linked DSH bundle resolves ESM imports from its real source path.  The
 * scheduler uses a Symbol exported by dsh-tools, so a profile-local copy and
 * the active runtime copy are not interchangeable even at the same version.
 */
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, rmSync, symlinkSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

const packageDir = dirname(dirname(fileURLToPath(import.meta.url)))
const packageJson = JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8'))
const expectedVersion = packageJson.devDependencies?.['@deepseek-ai/dsh-tools']
const source = join(homedir(), '.dsh', 'profiles', 'node_modules', '@deepseek-ai', 'dsh-tools')
const target = join(packageDir, 'node_modules', '@deepseek-ai', 'dsh-tools')

if (!existsSync(source)) {
  console.error('[openharmony-debug-test-pipeline] shared DSH runtime dependency is missing:', source)
  process.exit(1)
}

const runtimeVersion = JSON.parse(readFileSync(join(source, 'package.json'), 'utf8')).version
if (runtimeVersion !== expectedVersion) {
  console.error(`[openharmony-debug-test-pipeline] dsh-tools version mismatch: runtime=${runtimeVersion}, expected=${expectedVersion}`)
  process.exit(1)
}

mkdirSync(dirname(target), { recursive: true })
let targetExists = false
try {
  lstatSync(target)
  targetExists = true
  if (realpathSync(target) === realpathSync(source)) {
    console.log(`[openharmony-debug-test-pipeline] dsh-tools ${runtimeVersion} link already current`)
    process.exit(0)
  }
} catch {
  // A dangling symlink has an lstat entry but no realpath; replace it below.
}
if (targetExists) rmSync(target, { recursive: true, force: true })
symlinkSync(source, target, 'junction')

console.log(`[openharmony-debug-test-pipeline] linked dsh-tools ${runtimeVersion} from the active DSH runtime`)
