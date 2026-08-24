/**
 * Settings-page section content for dsh-gitlab-credentials: credential
 * management (per-host token save/delete with validation + glab sync status)
 * and MR preferences (assignee / target branch / labels / milestone). Token
 * fields are password inputs; nothing is persisted in the browser.
 */
import { useEffect, useMemo, useState } from 'react'
import { GitlabApi, type StatusResponse } from './api.ts'
import type { HostStatus, MrPreferences } from '../protocol.ts'

const api = new GitlabApi()

/** DSH-aligned field styles (inline; no CSS-module build step). */
const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex', flexDirection: 'column', gap: '20px', padding: '4px 2px', maxWidth: '880px',
    color: 'var(--dsw-alias-label-primary, #f5f5f5)',
  },
  h2: { fontSize: '15px', lineHeight: '22px', fontWeight: 600, margin: '0 0 8px' },
  hint: {
    fontSize: '12px', lineHeight: '18px', color: 'var(--dsw-alias-label-tertiary, #a6a6ad)',
    margin: '0 0 12px',
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
  table: { borderCollapse: 'collapse', width: '100%', fontSize: '12px', color: 'var(--dsw-alias-label-primary, #f5f5f5)' },
  th: {
    textAlign: 'left', padding: '7px 8px', color: 'var(--dsw-alias-label-tertiary, #a6a6ad)',
    fontWeight: 500, borderBottom: '1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))',
  },
  td: {
    padding: '8px', color: 'var(--dsw-alias-label-secondary, #c2c2c8)',
    borderBottom: '1px solid var(--dsw-alias-border-l1, rgb(255 255 255 / 8%))',
  },
  ok: { color: 'var(--dsw-alias-state-success-primary, #5bc98b)' },
  bad: { color: 'var(--dsw-alias-state-error-primary, #ff7777)' },
  muted: { color: 'var(--dsw-alias-label-tertiary, #a6a6ad)' },
  mono: { fontFamily: 'var(--ds-font-family-code, monospace)', fontSize: '11px', color: 'var(--dsw-alias-label-tertiary, #a6a6ad)' },
}

/** Deterministic state tag for one host row. */
function hostBadge(host: HostStatus, glab: boolean): { text: string; tone: string } {
  if (!host.hasToken) return { text: '未配置', tone: 'bad' }
  if (host.lastError !== '') return { text: '校验失败', tone: 'bad' }
  if (host.lastChecked === '') return { text: '已保存未校验', tone: 'muted' }
  return { text: glab ? '有效 · glab 已同步' : '有效 · glab 未同步', tone: 'ok' }
}

export function GitlabSection(): React.JSX.Element {
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
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

  const glabMap = useMemo(() => {
    const map = new Map<string, boolean>()
    for (const host of status?.hosts ?? []) map.set(host.host, host.glabAuthed)
    return map
  }, [status])

  const save = async (): Promise<void> => {
    setBusy(true); setNotice(''); setError('')
    try {
      const result = await api.save({ host, token, apiProtocol, apiHost, gitProtocol })
      setNotice(`已保存 ${result.user}@${result.host}` + (result.glabError === '' ? '，glab 已同步' : `（glab 同步失败：${result.glabError}）`))
      setToken('')
      await refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (target: string): Promise<void> => {
    setBusy(true); setNotice(''); setError('')
    try {
      await api.remove(target)
      setNotice(`已删除 ${target}（glab 已登出）`)
      await refresh()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const savePrefs = async (): Promise<void> => {
    setBusy(true); setNotice(''); setError('')
    try {
      await api.saveMrPreferences(prefs)
      setNotice('MR 偏好已保存')
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
        <p style={styles.hint}>保存时校验令牌（GET /api/v4/user）并同步到 glab CLI。令牌仅存于本机 <code>~/.dsh/gitlab-credentials.json</code>（0600），不会出现在对话或日志中。</p>
        <div style={styles.card}>
          <div style={styles.formRow}>
            <div style={styles.field}>
              <label style={styles.label}>Host</label>
              <input style={{ ...styles.input, width: '100%' }} value={host} onChange={e => setHost(e.target.value)} placeholder="192.168.11.238" />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>API Host</label>
              <input style={{ ...styles.input, width: '100%' }} value={apiHost} onChange={e => setApiHost(e.target.value)} placeholder="留空 = host" />
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label}>协议</label>
              <select style={{ ...styles.input, width: '100%' }} value={apiProtocol} onChange={e => setApiProtocol(e.target.value as 'http' | 'https')}>
                <option value="http">http</option>
                <option value="https">https</option>
              </select>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Git 协议</label>
              <select style={{ ...styles.input, width: '100%' }} value={gitProtocol} onChange={e => setGitProtocol(e.target.value as 'ssh' | 'https')}>
                <option value="ssh">ssh</option>
                <option value="https">https</option>
              </select>
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label}>Token</label>
              <input style={{ ...styles.input, width: '100%' }} type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="粘贴个人访问令牌 (api scope)" autoComplete="off" />
            </div>
            <div style={styles.action}>
              <button style={styles.button} disabled={busy || token === ''} onClick={() => void save()}>保存并校验</button>
            </div>
          </div>
          {error !== '' && <p style={{ ...styles.bad, fontSize: '12px' }}>{error}</p>}
          {notice !== '' && <p style={{ ...styles.ok, fontSize: '12px' }}>{notice}</p>}
        </div>
        {status !== null && status.hosts.length > 0 && (
          <div style={{ ...styles.card, marginTop: '12px' }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Host</th><th style={styles.th}>用户</th><th style={styles.th}>令牌</th>
                  <th style={styles.th}>指纹</th><th style={styles.th}>最近校验</th><th style={styles.th}>状态</th><th style={styles.th}></th>
                </tr>
              </thead>
              <tbody>
                {status.hosts.map(item => (
                  <tr key={item.host}>
                    <td style={styles.td}>{item.host}</td>
                    <td style={styles.td}>{item.user || '-'}</td>
                    <td style={styles.td}>{item.hasToken ? '已存' : '-'}</td>
                    <td style={{ ...styles.td, ...styles.mono }}>{item.fingerprint || '-'}</td>
                    <td style={styles.td}>{item.lastChecked ? new Date(item.lastChecked).toLocaleString() : '-'}</td>
                    {(() => {
                      const badge = hostBadge(item, glabMap.get(item.host) ?? false)
                      return <td style={{ ...styles.td, ...styles[badge.tone] }}>{badge.text}</td>
                    })()}
                    <td style={styles.td}>
                      <button style={styles.buttonGhost} disabled={busy} onClick={() => void remove(item.host)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2 style={styles.h2}>MR 偏好（glab-mr-submit 默认值）</h2>
        <div style={styles.card}>
          <div style={styles.formRow}>
            <div style={styles.field}>
              <label style={styles.label}>指派人</label>
              <input style={{ ...styles.input, width: '100%' }} value={prefs.assignee} onChange={e => setPrefs({ ...prefs, assignee: e.target.value })} placeholder="cx" />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>目标分支</label>
              <input style={{ ...styles.input, width: '100%' }} value={prefs.targetBranch} onChange={e => setPrefs({ ...prefs, targetBranch: e.target.value })} placeholder="v6.1.0.31_release" />
            </div>
          </div>
          <div style={{ ...styles.formRow, marginTop: '12px' }}>
            <div style={styles.field}>
              <label style={styles.label}>标签</label>
              <input style={{ ...styles.input, width: '100%' }} value={prefs.labels} onChange={e => setPrefs({ ...prefs, labels: e.target.value })} placeholder="逗号分隔，如 XTS,应用修复" />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>里程碑</label>
              <input style={{ ...styles.input, width: '100%' }} value={prefs.milestone} onChange={e => setPrefs({ ...prefs, milestone: e.target.value })} placeholder="留空 = 自动推断" />
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
