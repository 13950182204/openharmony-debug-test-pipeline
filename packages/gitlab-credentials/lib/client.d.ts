window.__ModuleLoader__.load({ id: "@superjunier/dsh-gitlab-credentials", factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;

import { Context } from "@deepseek-ai/cordis";
//#region src/client/panel.d.ts
declare function GitlabSection(): React.JSX.Element;
//#endregion
//#region src/client/index.d.ts
type ClientContext = Context & {
  slots: {
    inject: (name: string, mount: () => void) => void;
    register: (options: {
      name: string;
      id: string;
      order: number;
      label: () => string;
      locale: string;
    }, component: typeof GitlabSection) => void;
  };
};
/** Required services: the slot host plus locale (if provided). */
declare const inject: string[];
/**
 * Register the settings section. The slot seat only materializes when the
 * settings surface renders it, so registering on apply is safe.
 */
declare function apply(ctx: ClientContext): void;
//#endregion
export { apply, inject };

		return module.exports;
	}
});