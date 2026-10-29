/**
 * Minimal PATH lookup for a binary name (avoids a shell dependency).
 *
 * Cross-platform on purpose: DSH Desktop runs the host half on Windows, where
 * PATH is `;`-separated, entries are absolute `C:\...` paths and executables
 * carry a PATHEXT suffix. Splitting on `:` (the POSIX assumption) never found
 * anything there, which is why the glab sync always reported "binary not found".
 */
import { existsSync, statSync } from 'node:fs'
import { delimiter, join } from 'node:path'

/** Executable suffixes tried on Windows when the name has no extension. */
const WINDOWS_EXTS = ['.exe', '.cmd', '.bat', '.com']

/** Search PATH for an executable; returns its absolute path or undefined. */
export function which(binary: string): string | undefined {
  const pathVar = process.env.PATH ?? ''
  const names = process.platform === 'win32' && !/\.[A-Za-z0-9]+$/.test(binary)
    ? [binary, ...WINDOWS_EXTS.map(ext => `${binary}${ext}`)]
    : [binary]
  for (const dir of pathVar.split(delimiter)) {
    if (dir === '') continue
    for (const name of names) {
      const candidate = join(dir, name)
      try {
        if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
      } catch { /* keep searching */ }
    }
  }
  return undefined
}
