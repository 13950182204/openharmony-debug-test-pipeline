/**
 * 插件配置。默认值即可开箱即用;如需修改,可在 profile 的 cordis.patch.yml
 * 中覆盖该行的 config。
 */
export interface Config {
  /**
   * 父插件 openharmony-debug-test-pipeline 的根目录。skill 正文中
   * {{PARENT_PLUGIN_DIR}} 占位符替换为它,用于引用父插件的
   * scripts/pipeline_state.py 等脚本。缺省为本包根目录的上一级
   * (packages/openharmony-issue-pipeline → 仓库根)。
   */
  parentPluginDir?: string
}
