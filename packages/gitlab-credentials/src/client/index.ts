/**
 * Browser-half entry for dsh-gitlab-credentials. Registers the settings-page
 * section (the "GitLab 凭据" tab inside 设置) through the official
 * `settings.section` slot; the section content is the credential + MR-pref
 * editor. Failure policy: registration problems are logged, never thrown —
 * an external plugin must not take the GUI down.
 */
import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import type {} from '@deepseek-ai/dsh-client-ui-settings/client'
import { GitlabSection } from './panel.tsx'

/** Required services: the slot host plus locale (if provided). */
export const inject = ['slots', 'locale']

/** Stable section id (tab key) inside the settings page. */
const SECTION_ID = 'gitlab-credentials'

/**
 * Register the settings section. The slot seat only materializes when the
 * settings surface renders it, so registering on apply is safe.
 */
export function apply(ctx: ClientContext): void {
  try {
    ctx.slots.inject('settings.section', () => ctx.slots.register({
      name: 'settings.section',
      id: SECTION_ID,
      order: 30,
      label: () => 'GitLab 凭据',
      locale: 'dsh-gitlab-credentials',
    }, GitlabSection))
  } catch (error) {
    console.warn('[dsh-gitlab-credentials] settings section mount failed:', error)
  }
}
