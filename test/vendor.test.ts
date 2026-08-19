import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, writeFileSync, readFileSync, rmSync, existsSync, mkdirSync, readdirSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  VENDORED_SKILLS,
  buildSkillContent,
  parseSkillFrontmatter,
  registerVendoredSkills,
} from '../src/vendor.ts'
import {
  expandStatePath,
  loadState,
  resetState,
  saveState,
  pushEvent,
  renderState,
  emptyState,
} from '../src/state.ts'

describe('parseSkillFrontmatter', () => {
  it('解析合法 frontmatter 并剥离正文', () => {
    const raw = [
      '---',
      'name: demo-skill',
      'description: 演示 skill',
      'whenToUse: 需要演示时',
      'metadata:',
      '  short-description: 演示',
      '---',
      '# Demo',
      '正文内容',
      '',
    ].join('\n')
    const parsed = parseSkillFrontmatter(raw)
    expect(parsed).toBeDefined()
    expect(parsed!.name).toBe('demo-skill')
    expect(parsed!.description).toBe('演示 skill')
    expect(parsed!.whenToUse).toBe('需要演示时')
    expect(parsed!.content).toContain('# Demo')
    expect(parsed!.content).not.toContain('---')
    expect(parsed!.content).toContain('正文内容')
  })

  it('缺少 frontmatter 返回 undefined', () => {
    expect(parseSkillFrontmatter('# no frontmatter')).toBeUndefined()
  })

  it('frontmatter 缺 name/description 返回 undefined', () => {
    expect(parseSkillFrontmatter('---\nname: x\n---\nbody')).toBeUndefined()
    expect(parseSkillFrontmatter('---\ndescription: x\n---\nbody')).toBeUndefined()
  })
})

describe('buildSkillContent', () => {
  it('替换占位符并追加资源尾注', () => {
    const out = buildSkillContent(
      'python3 "{{SKILLS_DIR}}/openharmony-ota-upgrade/scripts/ota_preflight.sh"\npython3 {{PIPELINE_SCRIPT}} status\n',
      '/opt/pkg/skills',
      '/opt/pkg/scripts/pipeline_state.py',
    )
    expect(out).toContain('/opt/pkg/skills/openharmony-ota-upgrade/scripts/ota_preflight.sh')
    expect(out).toContain('/opt/pkg/scripts/pipeline_state.py status')
    expect(out).toContain('## 技能资源')
    expect(out).toContain('技能目录: /opt/pkg/skills')
    expect(out).not.toContain('{{SKILLS_DIR}}')
    expect(out).not.toContain('{{PIPELINE_SCRIPT}}')
  })
})

describe('registerVendoredSkills', () => {
  const registrations: any[] = []
  const ctx = {
    skills: { register: (reg: any) => registrations.push(reg) },
    logger: { warn: vi.fn(), info: vi.fn() },
  } as any

  beforeEach(() => registrations.length = 0)

  it('注册全部五个 vendored skill，正文占位符已解析', () => {
    registerVendoredSkills(ctx, { stateFile: '~/.dsh/pipeline-state.json' })
    expect(registrations).toHaveLength(5)
    const names = registrations.map((r) => r.name).sort()
    expect(names).toEqual([...VENDORED_SKILLS].sort())
    for (const reg of registrations) {
      expect(reg.source).toBe('bundled')
      expect(reg.content).not.toContain('{{SKILLS_DIR}}')
      expect(reg.content).not.toContain('{{PIPELINE_SCRIPT}}')
      expect(reg.content).toContain('## 技能资源')
      expect(reg.resourceBase.kind).toBe('directory')
    }
    // 各模块 skill 保持独立（模块化）
    expect(registrations.find((r) => r.name === 'glab-mr-submit').content).toContain('glab MR Submit')
  })

  it('缺失或损坏的 skill 记警告并跳过，不阻断其余注册', () => {
    const dir = mkdtempSync(join(tmpdir(), 'pipeline-vendor-'))
    try {
      // 只放 4 个合法 skill；缺 openharmony-ota-upgrade，且 triage 的 SKILL.md 损坏
      const names = VENDORED_SKILLS.filter((n) => n !== 'openharmony-ota-upgrade' && n !== 'openharmony-test-report-triage')
      for (const name of names) {
        mkdirSync(join(dir, 'skills', name), { recursive: true })
        writeFileSync(
          join(dir, 'skills', name, 'SKILL.md'),
          `---\nname: ${name}\ndescription: 测试 skill ${name}\n---\n# ${name}\n正文 {{SKILLS_DIR}}\n`,
          'utf8',
        )
      }
      mkdirSync(join(dir, 'skills', 'openharmony-test-report-triage'), { recursive: true })
      writeFileSync(join(dir, 'skills', 'openharmony-test-report-triage', 'SKILL.md'), '# 无 frontmatter', 'utf8')
      mkdirSync(join(dir, 'scripts'), { recursive: true })
      writeFileSync(join(dir, 'scripts', 'pipeline_state.py'), '', 'utf8')

      registerVendoredSkills(ctx, { stateFile: '~/.dsh/pipeline-state.json' }, dir)
      expect(registrations).toHaveLength(names.length)
      expect(registrations.map((r) => r.name).sort()).toEqual([...names].sort())
      expect(registrations[0].content).toContain('## 技能资源')
      // 缺失与损坏各记一次警告
      const warnCalls = ctx.logger.warn.mock.calls.flat().join('\n')
      expect(warnCalls).toContain('缺失')
      expect(warnCalls).toContain('frontmatter 非法')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('pipeline state', () => {
  let dir: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'pipeline-state-'))
  })

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true })
  })

  it('expandStatePath 展开 ~ 与相对路径', () => {
    expect(expandStatePath('~/x.json').startsWith(homedir())).toBe(true)
    expect(expandStatePath('/abs/x.json')).toBe('/abs/x.json')
  })

  it('save/load 往返一致，事件保留', () => {
    const file = join(dir, 'state.json')
    const state = emptyState()
    state.stage = 'mr'
    state.artifacts = { mrIid: 168, mrUrl: 'http://gitlab/mr/168' }
    pushEvent(state, 'mr', 'MR 已创建')
    saveState(file, state)
    const loaded = loadState(file)
    expect(loaded.stage).toBe('mr')
    expect(loaded.artifacts.mrIid).toBe(168)
    expect(loaded.events).toHaveLength(1)
    expect(loaded.events[0].note).toBe('MR 已创建')
  })

  it('损坏文件备份为 .bak 并返回空状态', () => {
    const file = join(dir, 'state.json')
    writeFileSync(file, '{ not json', 'utf8')
    const state = loadState(file)
    expect(state.stage).toBeNull()
    // 损坏文件被改名备份，原路径不再存在（下次 save 时重建）
    expect(existsSync(file)).toBe(false)
    const bakNames = readdirSync(dir).filter((n) => n.startsWith('state.json.bak-'))
    expect(bakNames).toHaveLength(1)
    expect(readFileSync(join(dir, bakNames[0]), 'utf8')).toBe('{ not json')
  })

  it('reset 落盘为空状态', () => {
    const file = join(dir, 'state.json')
    const state = emptyState()
    state.stage = 'ci'
    saveState(file, state)
    const reset = resetState(file)
    expect(reset.stage).toBeNull()
    expect(loadState(file).stage).toBeNull()
  })

  it('renderState 输出人类可读摘要', () => {
    const state = emptyState()
    state.stage = 'ota'
    state.artifacts = { otaResult: 'pass' }
    pushEvent(state, 'ota', '升级完成')
    const text = renderState(state)
    expect(text).toContain('流水线阶段: ota')
    expect(text).toContain('otaResult: pass')
    expect(text).toContain('[ota] 升级完成')
  })
})
