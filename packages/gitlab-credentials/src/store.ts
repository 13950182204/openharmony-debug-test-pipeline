/**
 * CredentialStore — the single on-disk source for GitLab tokens and MR
 * preferences. Atomic write, 0600 file / 0700 directory, same trust model as
 * dsh-ssh's host store. The path is injectable so tests can use a temp dir.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync, chmodSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'
import { randomBytes } from 'node:crypto'
import { DEFAULT_MR_PREFERENCES, type CredentialRecord, type CredentialStoreDoc, type HostStatus, type MrPreferences } from './protocol.ts'

export interface CredentialStoreOptions {
  /** Store file path; defaults to ~/.dsh/gitlab-credentials.json. */
  filePath?: string
}

/** Resolve the default store path without importing the plugin (shared by tests). */
export function defaultStorePath(): string {
  return join(homedir(), '.dsh', 'gitlab-credentials.json')
}

/** A store error with no secrets in any message. */
export class StoreError extends Error {}

export class CredentialStore {
  private readonly filePath: string
  private cached: CredentialStoreDoc | undefined

  constructor(options: CredentialStoreOptions = {}) {
    this.filePath = options.filePath ?? defaultStorePath()
  }

  /** Read (and cache) the document; missing/corrupt file yields defaults. */
  doc(): CredentialStoreDoc {
    if (this.cached !== undefined) return this.cached
    let parsed: unknown
    try {
      if (!existsSync(this.filePath)) {
        this.cached = { version: 1, hosts: {}, mrPreferences: { ...DEFAULT_MR_PREFERENCES } }
        return this.cached
      }
      parsed = JSON.parse(readFileSync(this.filePath, 'utf8'))
    } catch (error) {
      throw new StoreError(`cannot read credential store: ${(error as Error).message}`)
    }
    if (typeof parsed !== 'object' || parsed === null) {
      throw new StoreError('credential store document is not an object')
    }
    const document = parsed as Partial<CredentialStoreDoc>
    const hosts: Record<string, CredentialRecord> = {}
    if (document.hosts && typeof document.hosts === 'object') {
      for (const [key, value] of Object.entries(document.hosts)) {
        if (key.includes('\n') || typeof value !== 'object' || value === null) continue
        const record = value as Partial<CredentialRecord>
        if (typeof record.host !== 'string' || typeof record.token !== 'string') continue
        hosts[key] = {
          host: record.host,
          apiProtocol: record.apiProtocol === 'https' ? 'https' : 'http',
          apiHost: typeof record.apiHost === 'string' ? record.apiHost : '',
          gitProtocol: record.gitProtocol === 'https' ? 'https' : 'ssh',
          token: record.token,
          user: typeof record.user === 'string' ? record.user : '',
          lastChecked: typeof record.lastChecked === 'string' ? record.lastChecked : '',
          lastError: typeof record.lastError === 'string' ? record.lastError : '',
        }
      }
    }
    this.cached = {
      version: 1,
      hosts,
      mrPreferences: {
        ...DEFAULT_MR_PREFERENCES,
        ...(document.mrPreferences && typeof document.mrPreferences === 'object' ? document.mrPreferences : {}),
      },
    }
    return this.cached
  }

  /** Persist the current document atomically (tmp + rename) with 0600 mode. */
  private save(): void {
    const dir = dirname(this.filePath)
    mkdirSync(dir, { recursive: true, mode: 0o700 })
    const tmp = join(dir, `.gitlab-credentials.${process.pid}.${randomBytes(6).toString('hex')}.tmp`)
    const payload = JSON.stringify(this.doc(), null, 2)
    writeFileSync(tmp, payload, { mode: 0o600 })
    chmodSync(tmp, 0o600)
    renameSync(tmp, this.filePath)
    chmodSync(this.filePath, 0o600)
  }

  /** List a safe per-host summary (never includes the token). */
  summaries(): HostStatus[] {
    const doc = this.doc()
    return Object.values(doc.hosts).map(record => ({
      host: record.host,
      apiProtocol: record.apiProtocol,
      apiHost: record.apiHost,
      gitProtocol: record.gitProtocol,
      user: record.user,
      hasToken: record.token !== '',
      fingerprint: record.token === '' ? '' : `${record.token.slice(0, 4)}...${record.token.slice(-4)}`,
      lastChecked: record.lastChecked,
      lastError: record.lastError,
      glabAuthed: false,
    }))
  }

  /** Upsert one host record (validated by the caller). */
  upsert(record: CredentialRecord): void {
    if (record.host === '' || record.host.includes(' ') || record.host.includes('/')) {
      throw new StoreError('host must be a non-empty hostname without spaces or slashes')
    }
    const doc = this.doc()
    doc.hosts[record.host] = record
    this.save()
  }

  /** Delete one host; returns true when something was removed. */
  remove(host: string): boolean {
    const doc = this.doc()
    if (!(host in doc.hosts)) return false
    delete doc.hosts[host]
    this.save()
    return true
  }

  /** Read one record by host (undefined when absent). */
  record(host: string): CredentialRecord | undefined {
    return this.doc().hosts[host]
  }

  /** Replace MR preferences; returns a detached copy. */
  setMrPreferences(prefs: MrPreferences): MrPreferences {
    const doc = this.doc()
    doc.mrPreferences = { ...DEFAULT_MR_PREFERENCES, ...prefs }
    this.save()
    return { ...doc.mrPreferences }
  }

  /** Detached MR preferences. */
  mrPreferences(): MrPreferences {
    return { ...this.doc().mrPreferences }
  }

  /** Mark a validation attempt on one host (success or error). */
  markChecked(host: string, user: string, error: string): void {
    const doc = this.doc()
    const record = doc.hosts[host]
    if (record === undefined) return
    record.user = user
    record.lastChecked = new Date().toISOString()
    record.lastError = error
    this.save()
  }
}
