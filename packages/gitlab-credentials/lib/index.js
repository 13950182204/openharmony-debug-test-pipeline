import z from "schemastery";
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { defineTool } from "@deepseek-ai/dsh-tools";
//#region src/protocol.ts
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
const API_PREFIX = "/api/dsh-gitlab-credentials";
/** Stable cordis plugin name (host + browser halves spell the same id). */
const PLUGIN_ID = "gitlab-credentials";
/** Settings namespace of the plugin (see src/index.ts). */
const SETTINGS_NS = "dsh-gitlab-credentials";
/** Default MR preferences. */
const DEFAULT_MR_PREFERENCES = {
	assignee: "cx",
	targetBranch: "v6.1.0.31_release",
	labels: "",
	milestone: "",
	removeSourceBranch: true
};
//#endregion
//#region src/store.ts
/**
* CredentialStore — the single on-disk source for GitLab tokens and MR
* preferences. Atomic write, 0600 file / 0700 directory, same trust model as
* dsh-ssh's host store. The path is injectable so tests can use a temp dir.
*/
/** Resolve the default store path without importing the plugin (shared by tests). */
function defaultStorePath() {
	return join(homedir(), ".dsh", "gitlab-credentials.json");
}
/** A store error with no secrets in any message. */
var StoreError = class extends Error {};
var CredentialStore = class {
	filePath;
	cached;
	constructor(options = {}) {
		this.filePath = options.filePath ?? defaultStorePath();
	}
	/** Read (and cache) the document; missing/corrupt file yields defaults. */
	doc() {
		if (this.cached !== void 0) return this.cached;
		let parsed;
		try {
			if (!existsSync(this.filePath)) {
				this.cached = {
					version: 1,
					hosts: {},
					mrPreferences: { ...DEFAULT_MR_PREFERENCES }
				};
				return this.cached;
			}
			parsed = JSON.parse(readFileSync(this.filePath, "utf8"));
		} catch (error) {
			throw new StoreError(`cannot read credential store: ${error.message}`);
		}
		if (typeof parsed !== "object" || parsed === null) throw new StoreError("credential store document is not an object");
		const document = parsed;
		const hosts = {};
		if (document.hosts && typeof document.hosts === "object") for (const [key, value] of Object.entries(document.hosts)) {
			if (key.includes("\n") || typeof value !== "object" || value === null) continue;
			const record = value;
			if (typeof record.host !== "string" || typeof record.token !== "string") continue;
			hosts[key] = {
				host: record.host,
				apiProtocol: record.apiProtocol === "https" ? "https" : "http",
				apiHost: typeof record.apiHost === "string" ? record.apiHost : "",
				gitProtocol: record.gitProtocol === "https" ? "https" : "ssh",
				token: record.token,
				user: typeof record.user === "string" ? record.user : "",
				lastChecked: typeof record.lastChecked === "string" ? record.lastChecked : "",
				lastError: typeof record.lastError === "string" ? record.lastError : ""
			};
		}
		this.cached = {
			version: 1,
			hosts,
			mrPreferences: {
				...DEFAULT_MR_PREFERENCES,
				...document.mrPreferences && typeof document.mrPreferences === "object" ? document.mrPreferences : {}
			}
		};
		return this.cached;
	}
	/** Persist the current document atomically (tmp + rename) with 0600 mode. */
	save() {
		const dir = dirname(this.filePath);
		mkdirSync(dir, {
			recursive: true,
			mode: 448
		});
		const tmp = join(dir, `.gitlab-credentials.${process.pid}.${randomBytes(6).toString("hex")}.tmp`);
		const payload = JSON.stringify(this.doc(), null, 2);
		writeFileSync(tmp, payload, { mode: 384 });
		chmodSync(tmp, 384);
		renameSync(tmp, this.filePath);
		chmodSync(this.filePath, 384);
	}
	/** List a safe per-host summary (never includes the token). */
	summaries() {
		const doc = this.doc();
		return Object.values(doc.hosts).map((record) => ({
			host: record.host,
			apiProtocol: record.apiProtocol,
			apiHost: record.apiHost,
			gitProtocol: record.gitProtocol,
			user: record.user,
			hasToken: record.token !== "",
			fingerprint: record.token === "" ? "" : `${record.token.slice(0, 4)}...${record.token.slice(-4)}`,
			lastChecked: record.lastChecked,
			lastError: record.lastError,
			glabAuthed: false
		}));
	}
	/** Upsert one host record (validated by the caller). */
	upsert(record) {
		if (record.host === "" || record.host.includes(" ") || record.host.includes("/")) throw new StoreError("host must be a non-empty hostname without spaces or slashes");
		const doc = this.doc();
		doc.hosts[record.host] = record;
		this.save();
	}
	/** Delete one host; returns true when something was removed. */
	remove(host) {
		const doc = this.doc();
		if (!(host in doc.hosts)) return false;
		delete doc.hosts[host];
		this.save();
		return true;
	}
	/** Read one record by host (undefined when absent). */
	record(host) {
		return this.doc().hosts[host];
	}
	/** Replace MR preferences; returns a detached copy. */
	setMrPreferences(prefs) {
		const doc = this.doc();
		doc.mrPreferences = {
			...DEFAULT_MR_PREFERENCES,
			...prefs
		};
		this.save();
		return { ...doc.mrPreferences };
	}
	/** Detached MR preferences. */
	mrPreferences() {
		return { ...this.doc().mrPreferences };
	}
	/** Mark a validation attempt on one host (success or error). */
	markChecked(host, user, error) {
		const record = this.doc().hosts[host];
		if (record === void 0) return;
		record.user = user;
		record.lastChecked = (/* @__PURE__ */ new Date()).toISOString();
		record.lastError = error;
		this.save();
	}
};
//#endregion
//#region src/loopback.ts
/** IPv4 127/8 predicate (four decimal octets, first == 127). */
function isIPv4Loopback(v4) {
	const parts = v4.split(".");
	return parts.length === 4 && parts[0] === "127" && parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255);
}
/** Whether a socket remote address names the loopback range (127/8, ::1, IPv4-mapped). */
function isLoopbackAddress(address) {
	if (address === void 0) return false;
	const normalized = address.toLowerCase();
	if (normalized === "::1") return true;
	if (normalized.startsWith("::ffff:")) return isIPv4Loopback(normalized.slice(7));
	return isIPv4Loopback(normalized);
}
/** Whether a normalized URL hostname names the loopback authority (localhost, [::1], 127/8). */
function isLoopbackHostname(hostname) {
	if (hostname === "localhost" || hostname === "[::1]") return true;
	return isIPv4Loopback(hostname);
}
/**
* Request-level trust fence: a loopback socket address AND a loopback Host
* header, plus browser same-origin markers. The socket address is
* authoritative; X-Forwarded-For is never trusted.
*/
function isLoopbackRequest(request) {
	if (!isLoopbackAddress(request.socket.remoteAddress)) return false;
	const host = request.headers.host;
	if (typeof host !== "string") return false;
	let hostUrl;
	try {
		hostUrl = new URL("http://" + host);
	} catch {
		return false;
	}
	if (!isLoopbackHostname(hostUrl.hostname)) return false;
	if (request.headers["sec-fetch-site"] === "cross-site") return false;
	const origin = request.headers.origin;
	if (origin === void 0) return true;
	try {
		return new URL(origin).host === hostUrl.host;
	} catch {
		return false;
	}
}
//#endregion
//#region src/which.ts
/**
* Minimal PATH lookup for a binary name (avoids a shell dependency).
*/
/** Search PATH for an executable; returns its absolute path or undefined. */
function which(binary) {
	const pathVar = process.env.PATH ?? "";
	for (const dir of pathVar.split(":")) {
		if (dir === "") continue;
		const candidate = `${dir}/${binary}`;
		try {
			if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
		} catch {}
	}
}
//#endregion
//#region src/glab.ts
/**
* GitLab + glab integration: token validation against the GitLab API and
* glab CLI session sync. The token is passed to the glab child process over
* STDIN only — never as a command-line argument, never logged, never written
* to any repository or MR artifact.
*/
/**
* Validate a token against GET /api/v4/user. Returns the username and scope
* info; throws with a human-readable message on failure. The token never
* appears in the error text.
*/
async function validateToken(apiProtocol, apiHost, token) {
	const url = `${apiHost === "" ? "" : `${apiProtocol}://${apiHost}`}/api/v4/user`;
	let response;
	try {
		response = await fetch(url, {
			headers: {
				"PRIVATE-TOKEN": token,
				"user-agent": "dsh-gitlab-credentials"
			},
			signal: AbortSignal.timeout(1e4)
		});
	} catch (error) {
		throw new Error(`cannot reach GitLab API at ${url} (${error.message})`);
	}
	if (!response.ok) throw new Error(`GitLab API validation failed: HTTP ${response.status}`);
	const body = await response.json().catch(() => null);
	if (body === null || typeof body.username !== "string") throw new Error("GitLab API validation failed: unexpected response shape");
	return {
		user: body.username,
		scopes: []
	};
}
/** Locate the glab binary (PATH first, then the snap path). */
function resolveGlab() {
	const fromPath = which("glab");
	if (fromPath !== void 0) return fromPath;
	for (const candidate of [
		"/snap/glab/current/glab",
		"/usr/local/bin/glab",
		"/usr/bin/glab"
	]) try {
		if (existsSync(candidate)) return candidate;
	} catch {}
}
/** Run glab with args, feeding `input` to STDIN; resolves with stdout. */
function runGlab(args, input) {
	return new Promise((resolve) => {
		const binary = resolveGlab();
		if (binary === void 0) {
			resolve({
				code: 127,
				stdout: "",
				stderr: "glab binary not found"
			});
			return;
		}
		const child = spawn(binary, args, { stdio: [
			"pipe",
			"pipe",
			"pipe"
		] });
		let stdout = "";
		let stderr = "";
		child.stdout.on("data", (chunk) => {
			stdout += chunk.toString("utf8");
		});
		child.stderr.on("data", (chunk) => {
			stderr += chunk.toString("utf8");
		});
		child.on("close", (code) => resolve({
			code: code ?? -1,
			stdout,
			stderr
		}));
		child.stdin.on("error", () => {});
		if (input !== void 0) child.stdin.write(input);
		child.stdin.end();
	});
}
/**
* Log the token into the glab CLI for one host. Token flows over STDIN to
* `glab auth login --stdin`; the command arguments never carry it.
*/
async function glabLogin(host, apiProtocol, gitProtocol, token) {
	const result = await runGlab([
		"auth",
		"login",
		"--hostname",
		host,
		"--api-host",
		host,
		"--api-protocol",
		apiProtocol,
		"--git-protocol",
		gitProtocol,
		"--stdin"
	], `${token}\n`);
	if (result.code !== 0) throw new Error(`glab auth login failed (exit ${result.code}): ${result.stderr.trim() || result.stdout.trim() || "unknown error"}`);
}
/** Log out one host from glab (best-effort; ignores missing sessions). */
async function glabLogout(host) {
	await runGlab([
		"auth",
		"logout",
		"--hostname",
		host
	]);
}
/** Whether glab currently reports an authenticated session for the host. */
async function glabAuthed(host) {
	return (await runGlab([
		"auth",
		"status",
		"--hostname",
		host
	])).code === 0;
}
//#endregion
//#region src/routes.ts
/** Cap on JSON request bodies (credentials + preferences are small). */
const MAX_JSON_BODY_BYTES = 262144;
/** One JSON response. */
function writeJson(res, status, body) {
	const payload = JSON.stringify(body);
	res.writeHead(status, {
		"content-type": "application/json; charset=utf-8",
		"referrer-policy": "no-referrer"
	});
	res.end(payload);
}
/** Read a JSON request body (undefined when too large or unparseable). */
async function readJsonBody(req) {
	const chunks = [];
	let size = 0;
	for await (const chunk of req) {
		const buffer = chunk;
		size += buffer.length;
		if (size > MAX_JSON_BODY_BYTES) return void 0;
		chunks.push(buffer);
	}
	try {
		const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
		return typeof parsed === "object" && parsed !== null ? parsed : void 0;
	} catch {
		return;
	}
}
/** Normalize the apiHost: empty means the host itself (glab semantics). */
function apiHostValue(value) {
	return typeof value === "string" ? value.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "") : "";
}
/** Sanitize arbitrary text into a hostname-shaped id (no spaces/slashes). */
function hostValue(value) {
	const host = typeof value === "string" ? value.trim() : "";
	if (host === "" || host.includes(" ") || host.includes("/") || host.includes(":")) return "";
	return host;
}
function makeRoutes(deps) {
	const { store } = deps;
	/** Guard helper: fence + method + JSON body. */
	const guard = (req, res, method) => {
		if (!isLoopbackRequest(req)) {
			writeJson(res, 403, { error: "forbidden: loopback-only" });
			return false;
		}
		if (req.method !== method) {
			writeJson(res, 405, { error: `method not allowed: ${req.method}` });
			return false;
		}
		return true;
	};
	return { routes: [
		{
			kind: "exact",
			path: `${API_PREFIX}/status`,
			handler: async (req, res) => {
				if (!guard(req, res, "GET")) return;
				writeJson(res, 200, {
					hosts: await Promise.all(store.summaries().map(async (summary) => ({
						...summary,
						glabAuthed: await glabAuthed(summary.host).catch(() => false)
					}))),
					mrPreferences: store.mrPreferences()
				});
			}
		},
		{
			kind: "exact",
			path: `${API_PREFIX}/save`,
			handler: async (req, res) => {
				if (!guard(req, res, "POST")) return;
				const body = await readJsonBody(req);
				if (body === void 0) {
					writeJson(res, 400, { error: "invalid JSON body" });
					return;
				}
				const host = hostValue(body.host);
				const token = typeof body.token === "string" ? body.token.trim() : "";
				const apiProtocol = body.apiProtocol === "https" ? "https" : "http";
				const gitProtocol = body.gitProtocol === "https" ? "https" : "ssh";
				if (host === "") {
					writeJson(res, 400, { error: "host must be a non-empty hostname without spaces, slashes or colons" });
					return;
				}
				if (token === "") {
					writeJson(res, 400, { error: "token must not be empty" });
					return;
				}
				const apiHost = apiHostValue(body.apiHost) || host;
				try {
					const { user } = await validateToken(apiProtocol, apiHost, token);
					const record = {
						host,
						apiProtocol,
						apiHost,
						gitProtocol,
						token,
						user,
						lastChecked: (/* @__PURE__ */ new Date()).toISOString(),
						lastError: ""
					};
					store.upsert(record);
					let glabError = "";
					try {
						await glabLogin(host, apiProtocol, gitProtocol, token);
					} catch (error) {
						glabError = error.message;
					}
					writeJson(res, 200, {
						host,
						user,
						glabError,
						glabAuthed: glabError === "" ? await glabAuthed(host).catch(() => false) : false
					});
				} catch (error) {
					writeJson(res, 400, { error: error.message.replace(/[A-Za-z0-9_-]{20,}/g, "<redacted>") });
				}
			}
		},
		{
			kind: "exact",
			path: `${API_PREFIX}/delete`,
			handler: async (req, res) => {
				if (!guard(req, res, "POST")) return;
				const body = await readJsonBody(req);
				if (body === void 0) {
					writeJson(res, 400, { error: "invalid JSON body" });
					return;
				}
				const host = hostValue(body.host);
				if (host === "") {
					writeJson(res, 400, { error: "host is required" });
					return;
				}
				const removed = store.remove(host);
				await glabLogout(host).catch(() => void 0);
				writeJson(res, 200, { removed });
			}
		},
		{
			kind: "exact",
			path: `${API_PREFIX}/mr-preferences`,
			handler: async (req, res) => {
				if (!guard(req, res, "POST")) return;
				const body = await readJsonBody(req);
				if (body === void 0 || typeof body.preferences !== "object" || body.preferences === null) {
					writeJson(res, 400, { error: "preferences object is required" });
					return;
				}
				const prefs = body.preferences;
				writeJson(res, 200, { mrPreferences: store.setMrPreferences({
					assignee: typeof prefs.assignee === "string" ? prefs.assignee : "",
					targetBranch: typeof prefs.targetBranch === "string" ? prefs.targetBranch : "",
					labels: typeof prefs.labels === "string" ? prefs.labels : "",
					milestone: typeof prefs.milestone === "string" ? prefs.milestone : "",
					removeSourceBranch: prefs.removeSourceBranch !== false
				}) });
			}
		}
	] };
}
//#endregion
//#region src/tools.ts
/**
* Agent tools: a read-only status surface for the credential store plus the
* MR preferences gate (also read by the glab-mr-submit scripts). Tokens never
* leave the host — the tool reports user, fingerprint and glab sync state.
*/
/** One text content block (the only render shape these tools emit). */
function text(value) {
	return [{
		type: "text",
		text: value
	}];
}
/** The status tool: per-host GitLab credential state (no tokens, ever). */
function gitlabCredStatusTool(store) {
	return defineTool({
		name: "gitlab_cred_status",
		description: "Show GitLab credential state per configured host (user, token fingerprint, last check, glab sync). Triggers: GitLab token, GitLab 认证, 凭据, MR 提交失败需要认证, gitlab authentication. Tokens are never exposed; if a host is missing or not synced, ask the user to save it in 设置 > GitLab 凭据.",
		parameters: {},
		output: {
			schema: {
				type: "object",
				additionalProperties: false,
				properties: {
					hosts: {
						type: "array",
						required: true,
						items: {
							type: "object",
							additionalProperties: false,
							properties: {
								host: {
									type: "string",
									required: true
								},
								user: {
									type: "string",
									required: true
								},
								hasToken: {
									type: "boolean",
									required: true
								},
								fingerprint: {
									type: "string",
									required: true
								},
								lastChecked: {
									type: "string",
									required: true
								},
								glabAuthed: {
									type: "boolean",
									required: true
								},
								lastError: { type: "string" }
							}
						}
					},
					mrPreferences: {
						type: "object",
						additionalProperties: false,
						properties: {
							assignee: {
								type: "string",
								required: true
							},
							targetBranch: {
								type: "string",
								required: true
							},
							labels: {
								type: "string",
								required: true
							},
							milestone: {
								type: "string",
								required: true
							},
							removeSourceBranch: {
								type: "boolean",
								required: true
							}
						}
					}
				}
			},
			render: (_args, value) => {
				const hosts = value.hosts ?? [];
				const head = ["host | user | token | fingerprint | lastChecked | glab", "--- | --- | --- | --- | --- | ---"];
				const rows = hosts.map((host) => [
					host.host,
					host.user || "-",
					host.hasToken ? "yes" : "no",
					host.fingerprint || "-",
					host.lastChecked || "-",
					host.glabAuthed ? "authed" : host.hasToken ? "not-synced" : "none"
				].join(" | "));
				const body = rows.length === 0 ? ["no GitLab credentials configured — ask the user to add one in 设置 > GitLab 凭据"] : [...head, ...rows];
				const prefs = value.mrPreferences;
				if (prefs !== void 0) body.push("", "MR preferences:", `assignee=${prefs.assignee ?? ""} targetBranch=${prefs.targetBranch ?? ""} labels=${prefs.labels ?? ""} milestone=${prefs.milestone ?? ""} removeSourceBranch=${String(prefs.removeSourceBranch ?? "")}`);
				return text(body.join("\n"));
			}
		},
		async execute() {
			return {
				hosts: await Promise.all(store.summaries().map(async (summary) => ({
					...summary,
					glabAuthed: await glabAuthed(summary.host).catch(() => false)
				}))),
				mrPreferences: store.mrPreferences()
			};
		}
	});
}
//#endregion
//#region src/mount-once.ts
/**
* Host single-instance guard (shared family pattern). Prevents double-mount
* of the same package across bundle layers; the second apply is a no-op for
* the lifetime of the first instance.
*/
const MOUNTED = Symbol.for("dsh-web-ui.mounted-plugins");
function mountedSet() {
	const registry = globalThis;
	return registry[MOUNTED] ??= /* @__PURE__ */ new Set();
}
/** Wrap a cordis plugin apply so the package runs at most once per process. */
function mountOnce(packageName, fn) {
	return ((...args) => {
		const mounted = mountedSet();
		if (mounted.has(packageName)) return;
		mounted.add(packageName);
		args[0]?.effect?.(() => () => {
			mounted.delete(packageName);
		});
		return fn(...args);
	});
}
//#endregion
//#region src/index.ts
/** Stable cordis plugin name. */
const name = PLUGIN_ID;
/** Services required before the surfaces can mount. */
const inject = [
	"webServer",
	"tools",
	"systemPrompt"
];
/** Settings namespace of the plugin (web settings surface edits live here). */
const SETTINGS_NAMESPACE = SETTINGS_NS;
const Config = z.object({
	announceToAgent: z.boolean().default(true),
	enabled: z.boolean().default(true),
	storeFile: z.string()
});
const DEFAULT_ANNOUNCE = true;
/** Order of the announcement section within the tool-guidance band. */
const SECTION_ORDER = 155;
/** Model-facing announcement: plugin presence, capabilities, and limits. */
const CREDENTIALS_GUIDANCE = "本机已安装 dsh-gitlab-credentials 插件（GitLab 凭据管理）：设置页「GitLab 凭据」栏管理每个 GitLab 主机的访问令牌（保存时校验后同步到 glab CLI，供 MR 流程使用）；同一栏可配置 MR 偏好（指派人/目标分支/标签/里程碑）。能力：gitlab_cred_status 工具报告各主机令牌状态（用户/指纹/glab 同步情况，绝不回显令牌）；令牌仅存于 ~/.dsh/gitlab-credentials.json（0600），写入与 glab 同步由用户在 GUI 完成，agent 不掌握令牌明文。限制：令牌需在 GUI 保存后 agent 方可使用；失效时提示用户在设置中重新保存。用户提到「GitLab 令牌 / 凭据 / MR 认证 / 授权提交」时即指本插件，请据此协作。";
/** Stable once-guard per process (bundle re-apply is tolerated, double-mount is not). */
const apply = mountOnce(PLUGIN_ID, applyImpl);
function applyImpl(ctx, config) {
	let current = () => config ?? {};
	const resolve = () => ({
		announceToAgent: current().announceToAgent ?? DEFAULT_ANNOUNCE,
		enabled: current().enabled ?? true
	});
	const storeFile = config?.storeFile;
	const store = new CredentialStore(storeFile !== void 0 && storeFile !== "" ? { filePath: storeFile } : {});
	const { routes } = makeRoutes({ store });
	let disposeRoutes;
	let disposeTools;
	let disposeSection;
	/** Register (or drop) every surface to match the current source. */
	const sync = () => {
		if (disposeSection !== void 0) {
			disposeSection();
			disposeSection = void 0;
		}
		if (disposeRoutes !== void 0) {
			disposeRoutes();
			disposeRoutes = void 0;
		}
		if (disposeTools !== void 0) {
			disposeTools();
			disposeTools = void 0;
		}
		const value = resolve();
		if (!value.enabled) return;
		if (value.announceToAgent) disposeSection = ctx.systemPrompt.section({
			name: `plugin:${PLUGIN_ID}`,
			order: SECTION_ORDER,
			text: CREDENTIALS_GUIDANCE
		});
		disposeRoutes = ctx.effect(() => {
			const disposers = routes.map((route) => ctx.webServer.register(route));
			return () => {
				for (const dispose of disposers) dispose();
			};
		}, `${PLUGIN_ID}: routes`);
		disposeTools = ctx.effect(() => {
			const disposer = ctx.tools.register(gitlabCredStatusTool(store));
			return () => disposer();
		}, `${PLUGIN_ID}: tools`);
	};
	ctx.inject(["settings"], (settingsCtx) => {
		const legacy = settingsCtx.settings;
		if (typeof legacy.installSection === "function") {
			legacy.installSection(ctx, SETTINGS_NAMESPACE, Config, config ?? {}, {
				setSource: (source) => {
					current = source;
					sync();
				},
				onChange: sync
			});
			return;
		}
		settingsCtx.settings.configure({ auto: false }, ctx.fiber);
	});
	sync();
}
//#endregion
export { CREDENTIALS_GUIDANCE, Config, SETTINGS_NAMESPACE, apply, inject, name };
