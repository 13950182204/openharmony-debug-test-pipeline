/**
 * dsh-gitlab-credentials — shared protocol types and stable surface ids.
 *
 * The plugin stores GitLab personal access tokens per host (0600, never
 * replayed into conversation logs), syncs them into the glab CLI for the
 * MR workflow, and keeps MR preference defaults (assignee / target branch /
 * labels / milestone) for glab-mr-submit. Host half owns the store and the
 * /api/dsh-gitlab-credentials route family; the browser half renders the
 * settings-page section.
 */

/** Wire prefix for the route family (loopback-only, see routes.ts). */
export const API_PREFIX = '/api/dsh-gitlab-credentials'

/** Stable cordis plugin name (host + browser halves spell the same id). */
export const PLUGIN_ID = 'gitlab-credentials'

/** Settings namespace of the plugin (see src/index.ts). */
export const SETTINGS_NS = 'dsh-gitlab-credentials'

/** One configured GitLab host. */
export interface CredentialRecord {
  /** Lookup key and glab --hostname value, e.g. 192.168.11.238 or gitlab.example.com. */
  host: string
  /** API protocol used for both validation and glab (http | https). */
  apiProtocol: 'http' | 'https'
  /** API host override; empty means the host itself. */
  apiHost: string
  /** Git protocol used by glab (`ssh` | `https`). */
  gitProtocol: 'ssh' | 'https'
  /** Personal access token (plaintext in the 0600 store only). */
  token: string
  /** Username answered by GET /api/v4/user at last validation. */
  user: string
  /** ISO timestamp of the last successful validation. */
  lastChecked: string
  /** Last validation error message, if any (never contains the token). */
  lastError: string
}

/** MR preferences used by glab-mr-submit as defaults (GUI-editable). */
export interface MrPreferences {
  /** MR assignee username, e.g. `cx` (without the @). */
  assignee: string
  /** Target branch, e.g. v6.1.0.31_release. */
  targetBranch: string
  /** Comma-separated additional labels, e.g. `XTS,应用修复`. */
  labels: string
  /** Optional milestone title or id; empty = auto-infer non-blocking. */
  milestone: string
  /** Delete the source branch on merge. */
  removeSourceBranch: boolean
}

/** On-disk document (versioned). */
export interface CredentialStoreDoc {
  version: 1
  hosts: Record<string, CredentialRecord>
  mrPreferences: MrPreferences
}

/** Default MR preferences. */
export const DEFAULT_MR_PREFERENCES: MrPreferences = {
  assignee: 'cx',
  targetBranch: 'v6.1.0.31_release',
  labels: '',
  milestone: '',
  removeSourceBranch: true,
}

/** A host summary safe for conversation logs and GUI (no token). */
export interface HostStatus {
  host: string
  apiProtocol: string
  apiHost: string
  gitProtocol: string
  user: string
  /** true when a token is stored, false otherwise. */
  hasToken: boolean
  /** Token fingerprint (first 4 + last 4 chars), for identification only. */
  fingerprint: string
  /** ISO timestamp or empty when never validated. */
  lastChecked: string
  /** Last validation error (token is never included). */
  lastError: string
  /** Whether glab reports an authenticated session for this host. */
  glabAuthed: boolean
}
