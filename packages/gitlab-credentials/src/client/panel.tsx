/**
 * Settings-page section content for dsh-gitlab-credentials: credential
 * management (per-host token save/validation + glab sync read-back) and MR
 * preferences (assignee / target branch / labels / milestone). Token fields
 * are password inputs; nothing is persisted in the browser.
 *
 * Layout notes: state is rendered as a labelled badge (dot + text), never as
 * colour-only text, and the status table uses fixed column widths with nowrap
 * cells so short Chinese headers ("用户", "令牌") cannot break into one
 * character per line inside a narrow column.
 */
import { useEffect, useState } from 'react'
import { GitlabApi, type StatusResponse } from './api.ts'
import type { HostStatus, MrPreferences } from '../protocol.ts'

const api = new GitlabApi()

/** Semantic tones shared by badges and inline notices. */
type Tone = 'ok' | 'warn' | 'bad' | 'muted'

interface BadgeSpec {
  text: string
  tone: Tone
  title?: string
}

const badgeTones: Record<Tone, { fg: string; bg: string; border: string }> = {
  ok: {
    fg: 'var(--dsw-alias-state-success-primary, #5bc98b)',
    bg: 'rgba(91, 201, 139, 0.14)',
    border: 'rgba(91, 201, 139, 0.32)',
  },
  warn: {
    fg: 'var(--dsw-alias-state-warning-primary, #f0b429)',
    bg: 'rgba(240, 180, 41, 0.14)',
    border: 'rgba(240, 180, 41, 0.32)',
  },
  bad: {
    fg: 'var(--dsw-alias-state-error-primary, #ff7777)',
    bg: 'rgba(255, 119, 119, 0.14)',
    border: 'rgba(255, 119, 119, 0.32)',
  },
  muted: {
    fg: 'var(--dsw-alias-label-secondary, #c2c2c8)',
    bg: 'rgba(255, 255, 255, 0.06)',
    border: 'rgba(255, 255, 255, 0.16)',
  },
}

/** DSH-aligned field styles (inline; no CSS-module build step). */
const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex', flexDirection: 'column', gap: '20px', padding: '4px 2px', maxWidth: '940px',
    color: 'var(--dsw-alias-label-primary, #f5f5f5)',
  },
  h2: { fontSize: '15px', lineHeight: '22px', fontWeight: 600, margin: '0 0 8px' },
  hint: {
    fontSize: '12px', lineHeight: '18px', color: 'var(--dsw-alias-label-tertiary, #a6a6ad)',
    margin: '0 0 12px',
  },
  probe: {
    display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '6px',
    fontSize: '12px', lineHeight: '20px', color: 'var(--dsw-alias-label-tertiary, #a6a6ad)',
    margin: '0 0 12px',
  },
  probeCode: {
    fontFamily: 'var(--ds-font-family-code, monospace)', fontSize: '11px',
    padding: '1px 6px', borderRadius: '6px', whiteSpace: 'nowrap',
    background: 'rgba(255, 255, 255, 0.06)', color: 'var(--dsw-alias-label-secondary, #c2c2c8)',
  },
  formRow: {
    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
    gap: '12px 14px', alignItems: 'center',
  },
  field: {
    display: 'grid', gridTemplateColumns: '72px minmax(0, 1fr)', gap: '8px',
    alignItems: 'center', minWidth: 0,
  },
  label: {
    minWidth: 0, fontSize: '12px', lineHeight: '20px', whiteSpace: 'nowrap',
    color: 'var(--dsw-alias-label-secondary, #c2c2c8)',
  },
  action: { display: 'flex', alignItems: 'center', minHeight: '32px' },
  input: {
    boxSizing: 'border-box', height: '32px', padding: '0 10px', borderRadius: '8px',
    border: '1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))',
    background: 'var(--dsw-alias-bg-base, transparent)',
    color: 'var(--dsw-alias-label-primary, #f5f5f5)', fontSize: '13px', lineHeight: '20px',
  },
  button: {
    height: '32px', padding: '0 13px', borderRadius: '8px', border: '0',
    background: 'var(--dsw-alias-button-primary-fill, #4c8dff)',
    color: 'var(--dsw-alias-label-primary-foreground, #fff)', cursor: 'pointer', fontSize: '13px', fontWeight: 500,
  },
  buttonGhost: {
    height: '30px', padding: '0 12px', borderRadius: '8px',
    border: '1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))',
    background: 'transparent', color: 'var(--dsw-alias-label-secondary, #c2c2c8)', cursor: 'pointer', fontSize: '13px',
  },
  card: {
    boxSizing: 'border-box', border: '1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 12%))',
    borderRadius: '12px', background: 'var(--dsw-alias-bg-layer-1, rgb(255 255 255 / 4%))',
    padding: '16px 18px',
  },
  tableWrap: { overflowX: 'auto', margin: '0 -4px' },
  table: {
    borderCollapse: 'collapse', width: '100%', minWidth: '760px',
    tableLayout: 'fixed', fontSize: '12px', color: 'var(--dsw-alias-label-primary, #f5f5f5)',
  },
  th: {
    textAlign: 'left', padding: '7px 8px', whiteSpace: 'nowrap',
    color: 'var(--dsw-alias-label-tertiary, #a6a6ad)', fontWeight: 500,
    borderBottom: '1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))',
  },
  td: {
    padding: '8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
    color: 'var(--dsw-alias-label-secondary, #c2c2c8)',
    borderBottom: '1px solid var(--dsw-alias-border-l1, rgb(255 255 255 / 8%))',
  },
  tdMono: {
    fontFamily: 'var(--ds-font-family-code, monospace)', fontSize: '11px',
    color: 'var(--dsw-alias-label-primary, #f5f5f5)',
  },
  badge: {
    display: 'inline-flex', alignItems: 'center', gap: '5px', whiteSpace: 'nowrap',
    padding: '1px 8px', borderRadius: '999px', border: '1px solid transparent',
    fontSize: '11px', lineHeight: '18px', fontWeight: 500,
  },
  dot: { width: '5px', height: '5px', borderRadius: '50%', display: 'inline-block', flex: 'none' },
  notice: { margin: '8px 0 0', fontSize: '12px', lineHeight: '18px' },
  muted: { color: 'var(--dsw-alias-label-tertiary, #a6a6ad)' },
}

function Badge(props: { tone: Tone; title?: string; children: string }): React.JSX.Element {
  const tone = badgeTones[props.tone]
  return (
    <span
      style={{ ...styles.badge, color: tone.fg, background: tone.bg, borderColor: tone.border }}
      title={props.title}
    >
      <span aria-hidden="true" style={{ ...styles.dot, background: tone.fg }} />
      {props.children}
    </span>
  )
}

/** `YYYY-MM-DD HH:mm` in local time; toLocaleString wraps inside narrow cells. */
function formatChecked(iso: string): string {
  if (iso === '') return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** Token state of one host (validation result only, glab lives in its own cell). */
function checkBadge(host: HostStatus): BadgeSpec {
  if (!host.hasToken) return { text: '未配置', tone: 'muted', title: '尚未为这台主机保存令牌' }
  if (host.lastError !== '') return { text: '校验失败', tone: 'bad', title: host.lastError }
  if (host.lastChecked === '') return { text: '待校验', tone: 'muted', title: '令牌已保存，但还没有成功校验过' }
  return { text: '有效', tone: 'ok', title: `最近校验：${formatChecked(host.lastChecked)}` }
}

/** Whether the saved token actually reached the glab CLI. */
function syncBadge(host: HostStatus, glabAuthed: boolean, glabAvailable: boolean): BadgeSpec {
  if (!host.hasToken) return { text: '—', tone: 'muted', title: '没有令牌可同步' }
  if (!glabAvailable) {
    return { text: '不可用', tone: 'muted', title: '本机找不到 glab，也无法桥接到 WSL；可在插件配置里设置 credentials.glabCommand' }
  }
  return glabAuthed
    ? { text: '已同步', tone: 'ok', title: 'glab CLI 已登录该主机，MR 流程可直接使用' }
    : { text: '未同步', tone: 'warn', title: '保存令牌后会自动同步到 glab；持续失败请查看上方 glab 运行状态' }
}

export function GitlabSection(): React.JSX.Element {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState<{ text: string; tone: Tone } | null>(null)
  const [busy, setBusy] = useState(false)

  const [host, setHost] = useState('192.168.11.238')
  const [apiHost, setApiHost] = useState('')
  const [apiProtocol, setApiProtocol] = useState<'http' | 'https'>('http')
  const [gitProtocol, setGitProtocol] = useState<'ssh' | 'https'>('ssh')
  const [token, setToken] = useState('')

  const [prefs, setPrefs] = useState<MrPreferences>({
    assignee: 'cx', targetBranch: 'v6.1.0.31_release', labels: '', milestone: '', removeSourceBranch: true,
  })

  const refresh = async (): Promise<void> => {
    try {
      const value = await api.status()
      setStatus(value)
      setPrefs(value.mrPreferences)
      setError('')
    } catch (cause) {
      setError((cause as Error).message)
    }
  }

  useEffect(() => { void refresh() }, [])

  const glabMap = new Map<string, boolean>()
  for (const item of status?.hosts ?? []) glabMap.set(item.host, item.glabAuthed)
  const glabProbe = status?.glab
  const glabAvailable = glabProbe !== undefined && glabProbe.available

  const save = async (): Promise<void> => {
    setBusy(true); setNotice(null); setError('')
    try {
      const result = await api.save({ host, token, apiProtocol, apiHost, gitProtocol })
      if (result.glabSynced && result.glabVerified) {
        setNotice({ tone: 'ok', text: `已保存 ${result.user}@${result.host} · glab 已同步并验证（当前用户 ${result.glabUser}）` })
      } else if (result.glabSynced) {
        setNotice({ tone: 'warn', text: `已保存 ${result.user}@${result.host} · glab 已登录，但回读验证失败：${result.glabError}` })
      } else {
        setNotice({ tone: 'warn', text: `已保存 ${result.user}@${result.host} · glab 未同步：${result.glabError}` })
      }
      setToken('')
      await refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (target: string): Promise<void> => {
    setBusy(true); setNotice(null); setError('')
    try {
      await api.remove(target)
      setNotice({ tone: 'ok', text: `已删除 ${target}（glab 已登出）` })
      await refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const savePrefs = async (): Promise<void> => {
    setBusy(true); setNotice(null); setError('')
    try {
      await api.saveMrPreferences(prefs)
      setNotice({ tone: 'ok', text: 'MR 偏好已保存' })
      await refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={styles.root}>
      <section>
        <h2 style={styles.h2}>GitLab 凭据</h2>
        <p style={styles.hint}>
          保存时先校验令牌（GET /api/v4/user），再写入凭据文件并同步到 glab CLI，最后回读 glab 当前登录用户做验证。
          Windows 宿主会自动经 WSL 调用 glab。令牌仅存于本机 <code>~/.dsh/gitlab-credentials.json</code>（0600），不会出现在对话或日志中。
        </p>
        {glabProbe !== undefined && (
          <p style={styles.probe}>
            <span>glab 同步目标</span>
            <Badge tone={glabProbe.available ? 'ok' : 'warn'} title={glabProbe.error}>
              {glabProbe.available ? '可用' : '不可用'}
            </Badge>
            {glabProbe.describe !== '' && <code style={styles.probeCode}>{glabProbe.describe}</code>}
            {glabProbe.error !== '' && <span style={styles.muted}>（{glabProbe.error}）</span>}
          </p>
        )}
        <div style={styles.card}>
          <div style={styles.formRow}>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-credential-host">Host</label>
              <input id="gitlab-credential-host" style={{ ...styles.input, width: '100%' }} value={host} onChange={e => setHost(e.target.value)} placeholder="192.168.11.238" />
            </div>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-credential-api-host">API Host</label>
              <input id="gitlab-credential-api-host" style={{ ...styles.input, width: '100%' }} value={apiHost} onChange={e => setApiHost(e.target.value)} placeholder="留空 = host" />
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-credential-api-protocol">协议</label>
              <select id="gitlab-credential-api-protocol" style={{ ...styles.input, width: '100%' }} value={apiProtocol} onChange={e => setApiProtocol(e.target.value as 'http' | 'https')}>
                <option value="http">http</option>
                <option value="https">https</option>
              </select>
            </div>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-credential-git-protocol">Git 协议</label>
              <select id="gitlab-credential-git-protocol" style={{ ...styles.input, width: '100%' }} value={gitProtocol} onChange={e => setGitProtocol(e.target.value as 'ssh' | 'https')}>
                <option value="ssh">ssh</option>
                <option value="https">https</option>
              </select>
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-credential-token">Token</label>
              <input id="gitlab-credential-token" style={{ ...styles.input, width: '100%' }} type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="粘贴个人访问令牌 (api scope)" autoComplete="off" />
            </div>
            <div style={styles.action}>
              <button style={styles.button} disabled={busy || token === ''} onClick={() => void save()}>
                {busy ? '保存中…' : '保存并校验'}
              </button>
            </div>
          </div>
          {error !== '' && <p style={{ ...styles.muted, color: badgeTones.bad.fg, fontSize: '12px' }}>{error}</p>}
          {notice !== null && (
            <p style={{ ...styles.notice, color: badgeTones[notice.tone].fg }} role="status" aria-live="polite">
              {notice.text}
            </p>
          )}
        </div>
        {status !== null && status.hosts.length > 0 && (
          <div style={{ ...styles.card, marginTop: '12px' }}>
            <div style={styles.tableWrap}>
              <table style={styles.table} aria-label="已保存的 GitLab 凭据">
                <colgroup>
                  <col style={{ width: '150px' }} />
                  <col style={{ width: '72px' }} />
                  <col style={{ width: '64px' }} />
                  <col style={{ width: '96px' }} />
                  <col style={{ width: '124px' }} />
                  <col style={{ width: '82px' }} />
                  <col style={{ width: '78px' }} />
                  <col style={{ width: '64px' }} />
                </colgroup>
                <thead>
                  <tr>
                    <th style={styles.th}>Host</th>
                    <th style={styles.th}>用户</th>
                    <th style={styles.th}>令牌</th>
                    <th style={styles.th}>指纹</th>
                    <th style={styles.th}>最近校验</th>
                    <th style={styles.th}>校验</th>
                    <th style={styles.th}>glab</th>
                    <th style={styles.th}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {status.hosts.map(item => {
                    const check = checkBadge(item)
                    const sync = syncBadge(item, glabMap.get(item.host) ?? false, glabAvailable)
                    return (
                      <tr key={item.host}>
                        <td style={{ ...styles.td, ...styles.tdMono }} title={item.host}>{item.host}</td>
                        <td style={styles.td}>{item.user || '—'}</td>
                        <td style={styles.td}>{item.hasToken ? '已存' : '—'}</td>
                        <td style={{ ...styles.td, ...styles.tdMono }} title={item.fingerprint || ''}>{item.fingerprint || '—'}</td>
                        <td style={styles.td} title={item.lastChecked || ''}>{formatChecked(item.lastChecked)}</td>
                        <td style={styles.td}>
                          <Badge tone={check.tone} title={check.title}>{check.text}</Badge>
                        </td>
                        <td style={styles.td}>
                          <Badge tone={sync.tone} title={sync.title}>{sync.text}</Badge>
                        </td>
                        <td style={styles.td}>
                          <button
                            style={styles.buttonGhost}
                            disabled={busy}
                            aria-label={`删除 ${item.host} 的凭据`}
                            onClick={() => void remove(item.host)}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      <section>
        <h2 style={styles.h2}>MR 偏好（glab-mr-submit 默认值）</h2>
        <div style={styles.card}>
          <div style={styles.formRow}>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-mr-assignee">指派人</label>
              <input id="gitlab-mr-assignee" style={{ ...styles.input, width: '100%' }} value={prefs.assignee} onChange={e => setPrefs({ ...prefs, assignee: e.target.value })} placeholder="cx" />
            </div>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-mr-target">目标分支</label>
              <input id="gitlab-mr-target" style={{ ...styles.input, width: '100%' }} value={prefs.targetBranch} onChange={e => setPrefs({ ...prefs, targetBranch: e.target.value })} placeholder="v6.1.0.35_lts" />
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-mr-labels">标签</label>
              <input id="gitlab-mr-labels" style={{ ...styles.input, width: '100%' }} value={prefs.labels} onChange={e => setPrefs({ ...prefs, labels: e.target.value })} placeholder="逗号分隔，如 XTS,应用修复" />
            </div>
            <div style={styles.field}>
              <label style={styles.label} htmlFor="gitlab-mr-milestone">里程碑</label>
              <input id="gitlab-mr-milestone" style={{ ...styles.input, width: '100%' }} value={prefs.milestone} onChange={e => setPrefs({ ...prefs, milestone: e.target.value })} placeholder="留空 = 自动推断" />
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <label style={{ ...styles.label, display: 'flex', alignItems: 'center', gap: '8px' }}>
              合并后删除源分支
            <input
              type="checkbox"
              checked={prefs.removeSourceBranch}
              onChange={e => setPrefs({ ...prefs, removeSourceBranch: e.target.checked })}
              style={{ accentColor: 'var(--dsw-alias-button-primary-fill, #4c8dff)' }}
            />
            </label>
            <div style={styles.action}>
              <button style={styles.button} disabled={busy} onClick={() => void savePrefs()}>保存偏好</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
