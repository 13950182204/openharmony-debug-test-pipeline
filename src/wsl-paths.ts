
/**
 * 「宿主在 Windows、执行在 WSL」时的路径口径解析。
 *
 * 背景：DSH Desktop 直接驱动 agent loop 时，插件是在 **Windows 侧 Node** 里加载的，
 * 但会话的 `bash` 工具把命令转发进 **WSL** 执行（与 `dsh-wsl-workspace` 同一模型）。
 * 于是有两套路径口径：
 *
 * - **宿主路径**（插件自己读文件用）：Windows 侧形如 `\\wsl.localhost\Ubuntu-22.04\home\cx\os\...`；
 * - **模型路径**（写进 skill 正文、由模型在 WSL bash 里执行）：必须是 `/home/cx/os/...`。
 *
 * 两者都从插件包根推导时，Windows 侧的推导结果模型跑不动，所以这里把「模型可见路径」
 * 独立解析：配置了 WSL 口径就用它，否则退回插件包根（Linux/WSL 原生安装时的正确行为）。
 */

/** 是否是 Windows 宿主（DSH Desktop 直驱 agent loop 的场景）。 */
export function isWindowsHost(): boolean {
  return process.platform === 'win32'
}

/** 参与路径解析的配置切片。 */
export interface ModelPathConfig {
  /**
   * 插件包在 WSL 中的绝对路径；配置后 skill 正文与脚本路径使用该口径。
   * Linux/WSL 原生安装时留空（宿主路径本身就是模型可用路径）。
   */
  wslPackageRoot?: string
  /**
   * 流水线状态文件路径；缺省 `~/.dsh/pipeline-state.json`。
   *
   * 该值**同时**用于两处，因此 Windows 宿主上要写成两套都能读的形态
   * （Windows Node 与 WSL 都能访问 WSL 内文件），例如
   * `//wsl.localhost/Ubuntu-22.04/home/cx/.dsh/pipeline-state.json`：
   * - 宿主侧 `/pipeline status` 的 Node 读取；
   * - skill 正文里模型要执行的 `python3 ... --file <状态文件>`。
   */
  stateFile?: string
}

export interface ModelPaths {
  /** 插件包根（模型口径） */
  packageRoot: string
  /** 插件包 skills/ 目录（模型口径） */
  skillsDir: string
  /** 流水线状态脚本（模型口径） */
  pipelineScript: string
  /** 流水线状态文件（宿主读写与模型执行共用） */
  stateFile: string
}

/**
 * 按「模型可执行」的口径拼接相对路径。
 *
 * 不能用 `node:path.join`：Windows 宿主上它会用反斜杠拼接，把 `/home/cx/os`
 * 变成 `/home/cx/os\skills` 这种模型跑不了的混合路径——而这正是本模块要修的问题。
 * 因此统一输出正斜杠（Windows 侧同样能解读正斜杠，两处都可用）。
 *
 * @param base - 基准路径（可能来自配置，也可能是宿主原生路径）
 * @param segments - 相对片段
 */
export function joinModelPath(base: string, ...segments: string[]): string {
  const trimmed = base.replace(/[/\\]+$/, '').replace(/\\/g, '/')
  return [trimmed, ...segments].join('/')
}

/**
 * 解析模型可见路径。`hostPackageRoot` 是插件包在宿主上的真实位置，仅在未配置
 * WSL 口径时作为回退（Linux/WSL 原生安装下它本来就是模型可用的路径）。
 *
 * @param hostPackageRoot - 插件包在宿主上的绝对路径
 * @param config - 路径相关配置
 */
export function resolveModelPaths(hostPackageRoot: string, config: ModelPathConfig = {}): ModelPaths {
  const configured = config.wslPackageRoot
  const packageRoot = configured !== undefined && configured !== '' ? configured : hostPackageRoot
  return {
    packageRoot,
    skillsDir: joinModelPath(packageRoot, 'skills'),
    pipelineScript: joinModelPath(packageRoot, 'scripts', 'pipeline_state.py'),
    stateFile: config.stateFile ?? '~/.dsh/pipeline-state.json',
  }
}
