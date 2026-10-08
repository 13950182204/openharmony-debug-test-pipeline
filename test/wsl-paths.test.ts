import { describe, expect, it } from 'vitest'
import { joinModelPath, resolveModelPaths } from '../src/wsl-paths.ts'
import { registerVendoredSkills } from '../src/vendor.ts'
import { buildLoopSkill } from '../src/loop-skill.ts'

const HOST_ROOT = '\\\\wsl.localhost\\Ubuntu-22.04\\home\\cx\\os\\openharmony-debug-test-pipeline'
const WSL_ROOT = '/home/cx/os/openharmony-debug-test-pipeline'

describe('joinModelPath', () => {
  it('统一输出正斜杠（Windows 宿主上不能产出混合路径）', () => {
    expect(joinModelPath(WSL_ROOT, 'skills')).toBe('/home/cx/os/openharmony-debug-test-pipeline/skills')
    // 关键回归：Windows 侧 node:path.join 会给出 '/home/cx/os\\skills'
    expect(joinModelPath(WSL_ROOT, 'skills')).not.toContain('\\')
    expect(joinModelPath('//wsl.localhost/Ubuntu-22.04/home/cx/os/pkg', 'scripts', 'a.py'))
      .toBe('//wsl.localhost/Ubuntu-22.04/home/cx/os/pkg/scripts/a.py')
  })

  it('把宿主原生反斜杠基准规范为正斜杠', () => {
    expect(joinModelPath(HOST_ROOT, 'skills'))
      .toBe('//wsl.localhost/Ubuntu-22.04/home/cx/os/openharmony-debug-test-pipeline/skills')
  })

  it('去掉基准尾部多余分隔符', () => {
    expect(joinModelPath('/home/cx/os/pkg/', 'skills')).toBe('/home/cx/os/pkg/skills')
    expect(joinModelPath('/home/cx/os/pkg//', 'skills')).toBe('/home/cx/os/pkg/skills')
  })
})

describe('resolveModelPaths', () => {
  it('未配置 wslPackageRoot 时回落宿主路径（Linux/WSL 原生安装）', () => {
    const paths = resolveModelPaths(WSL_ROOT, {})
    expect(paths.packageRoot).toBe(WSL_ROOT)
    expect(paths.skillsDir).toBe(`${WSL_ROOT}/skills`)
    expect(paths.pipelineScript).toBe(`${WSL_ROOT}/scripts/pipeline_state.py`)
    expect(paths.stateFile).toBe('~/.dsh/pipeline-state.json')
  })

  it('配置 wslPackageRoot 后模型口径替换宿主口径', () => {
    const paths = resolveModelPaths(HOST_ROOT, { wslPackageRoot: WSL_ROOT })
    expect(paths.packageRoot).toBe(WSL_ROOT)
    expect(paths.skillsDir).toBe(`${WSL_ROOT}/skills`)
    expect(paths.pipelineScript).toBe(`${WSL_ROOT}/scripts/pipeline_state.py`)
  })

  it('空字符串视为未配置', () => {
    expect(resolveModelPaths(WSL_ROOT, { wslPackageRoot: '' }).packageRoot).toBe(WSL_ROOT)
  })

  it('stateFile 原样透传（宿主读写与模型执行共用同一路径）', () => {
    const unc = '//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json'
    expect(resolveModelPaths(WSL_ROOT, { stateFile: unc }).stateFile).toBe(unc)
  })
})

describe('Windows 宿主下的 skill 正文口径', () => {
  const captured: any[] = []
  const ctx = {
    skills: { register: (reg: any) => captured.push(reg) },
    logger: { warn: () => {}, info: () => {} },
  } as any

  it('registerVendoredSkills 的 {{SKILLS_DIR}} 用 WSL 路径渲染', () => {
    captured.length = 0
    registerVendoredSkills(ctx, { wslPackageRoot: WSL_ROOT })
    expect(captured).toHaveLength(5)
    for (const reg of captured) {
      // 每个模块 skill 的正文都带「技能资源」尾注（技能目录来自 {{SKILLS_DIR}}）
      expect(reg.content).toContain('## 技能资源')
      expect(reg.content).toContain(`${WSL_ROOT}/skills`)
      expect(reg.content).not.toContain('{{SKILLS_DIR}}')
      expect(reg.content).not.toContain('{{PIPELINE_SCRIPT}}')
      // 关键回归：不能把宿主反斜杠路径写进模型可见正文
      expect(reg.content).not.toContain('\\\\wsl.localhost')
    }
  })

  it('闭环 skill 的脚本与状态文件路径同样是模型可用口径', () => {
    const skill = buildLoopSkill({
      wslPackageRoot: WSL_ROOT,
      stateFile: '//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json',
    } as any, '/host/path/pkg')
    expect(skill.content).toContain(`python3 ${WSL_ROOT}/scripts/pipeline_state.py reset`)
    expect(skill.content).toContain('//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json')
    expect(skill.content).not.toContain('/host/path/pkg')
  })
})
