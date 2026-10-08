/**
 * Host-side smoke test: store atomicity + 0600 perms + MR preferences +
 * summaries never leak tokens. Runs without network or glab.
 */
import { mkdtempSync, readFileSync, statSync, rmSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { CredentialStore, defaultStorePath } from '../src/store.ts'
import { DEFAULT_MR_PREFERENCES } from '../src/protocol.ts'

const dir = mkdtempSync(join(tmpdir(), 'dsh-gitlab-cred-'))
const file = join(dir, 'cred.json')

let failures = 0
function check(label: string, condition: boolean, detail = ''): void {
  if (condition) console.log(`ok   ${label}`)
  else { console.error(`FAIL ${label} ${detail}`); failures++ }
}

try {
  const store = new CredentialStore({ filePath: file })
  const doc = store.doc()
  check('empty doc defaults', doc.version === 1 && Object.keys(doc.hosts).length === 0)
  check('default mr preferences', doc.mrPreferences.assignee === DEFAULT_MR_PREFERENCES.assignee && doc.mrPreferences.removeSourceBranch === true)

  store.upsert({
    host: 'gitlab.example.com', apiProtocol: 'https', apiHost: 'gitlab.example.com', gitProtocol: 'ssh',
    token: 'TOKEN-fingerprint-value-1234', user: 'tester', lastChecked: '', lastError: '',
  })
  const stat = statSync(file)
  check('file mode 0600', (stat.mode & 0o777) === 0o600, `mode=${(stat.mode & 0o777).toString(8)}`)
  const parsed = JSON.parse(readFileSync(file, 'utf8'))
  check('persisted roundtrip', parsed.hosts['gitlab.example.com'].token === 'TOKEN-fingerprint-value-1234')

  const summaries = store.summaries()
  check('summary hasToken', summaries[0].hasToken === true)
  check('summary fingerprint', summaries[0].fingerprint === 'TOKE...1234', summaries[0].fingerprint)
  check('summary never carries token', !JSON.stringify(summaries).includes('TOKEN-fingerprint-value-1234'))

  store.setMrPreferences({ assignee: 'cx', targetBranch: 'v6.1.0.31_release', labels: 'XTS,应用修复', milestone: '', removeSourceBranch: true })
  const prefs = store.mrPreferences()
  check('mr preferences saved', prefs.labels === 'XTS,应用修复' && prefs.targetBranch === 'v6.1.0.31_release')

  store.markChecked('gitlab.example.com', 'tester', '')
  check('lastChecked recorded', store.record('gitlab.example.com')?.lastChecked !== '')

  check('remove host', store.remove('gitlab.example.com') === true)
  check('remove missing host', store.remove('gitlab.example.com') === false)

  // config.storeFile 缺省时回落 ~/.dsh/gitlab-credentials.json（WSL/原生安装依赖该行为）。
  // 桌面形态下由 config.storeFile 指到 WSL 那份文件，见 src/index.ts 的 Config.storeFile。
  const fallback = defaultStorePath()
  check('default store path is ~/.dsh/gitlab-credentials.json',
    fallback === join(homedir(), '.dsh', 'gitlab-credentials.json'), fallback)

  // 桌面形态：filePath 指向一个父目录尚不存在的路径时必须能自建目录（0600/0700）。
  const deep = join(dir, 'nested', 'deeper', 'cred.json')
  const deepStore = new CredentialStore({ filePath: deep })
  deepStore.upsert({
    host: 'desktop.example.com', apiProtocol: 'http', apiHost: 'desktop.example.com', gitProtocol: 'ssh',
    token: 'TOKEN-desktop-value-9876', user: 'cx', lastChecked: '', lastError: '',
  })
  // 真正被强制的契约是**文件** 0600（save() 里 mkdir 0700 + 写 tmp 0600 + chmod 兜底，
  // 但父目录权限受 umask/既有目录影响，不作为断言）。
  check('nested filePath keeps file mode 0600', (statSync(deep).mode & 0o777) === 0o600)
  check('nested filePath roundtrip', JSON.parse(readFileSync(deep, 'utf8')).hosts['desktop.example.com'].user === 'cx')
} catch (error) {
  console.error('unexpected throw:', error)
  failures++
} finally {
  rmSync(dir, { recursive: true, force: true })
}

if (failures === 0) console.log('ALL STORE TESTS PASSED')
else { console.error(`${failures} FAILURES`); process.exit(1) }
