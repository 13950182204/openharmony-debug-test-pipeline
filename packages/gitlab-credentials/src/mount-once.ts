/**
 * Host single-instance guard (shared family pattern). Prevents double-mount
 * of the same package across bundle layers; the second apply is a no-op for
 * the lifetime of the first instance.
 */

const MOUNTED = Symbol.for('dsh-web-ui.mounted-plugins')

type MountRegistry = Record<symbol, Set<string>>

function mountedSet(): Set<string> {
  const registry = globalThis as MountRegistry
  return (registry[MOUNTED] ??= new Set())
}

/** Wrap a cordis plugin apply so the package runs at most once per process. */
export function mountOnce<T extends (...args: any[]) => unknown>(packageName: string, fn: T): T {
  return ((...args: unknown[]) => {
    const mounted = mountedSet()
    if (mounted.has(packageName)) return
    mounted.add(packageName)
    const ctx = args[0] as { effect?: (effect: () => unknown) => unknown } | undefined
    ctx?.effect?.(() => () => {
      mounted.delete(packageName)
    })
    return fn(...args)
  }) as T
}
