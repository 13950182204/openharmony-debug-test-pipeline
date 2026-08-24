/**
 * Minimal PATH lookup for a binary name (avoids a shell dependency).
 */
import { existsSync, statSync } from 'node:fs'

/** Search PATH for an executable; returns its absolute path or undefined. */
export function which(binary: string): string | undefined {
  const pathVar = process.env.PATH ?? ''
  for (const dir of pathVar.split(':')) {
    if (dir === '') continue
    const candidate = `${dir}/${binary}`
    try {
      if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
    } catch { /* keep searching */ }
  }
  return undefined
}
