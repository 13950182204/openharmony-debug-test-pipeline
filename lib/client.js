window.__ModuleLoader__.load({ id: "openharmony-debug-test-pipeline", factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;

Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
let react = require("react");
let react_jsx_runtime = require("react/jsx-runtime");
//#region src/client/api.ts
/** POST JSON helper with error extraction. */
async function postJson(path, body) {
	const response = await fetch(path, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify(body)
	});
	const payload = await response.json().catch(() => null);
	if (!response.ok) throw new Error(payload?.error ?? `request failed: HTTP ${response.status}`);
	return payload;
}
var GitlabApi = class {
	/** Fetch credential status + MR preferences. */
	async status() {
		const response = await fetch("/api/dsh-gitlab-credentials/status");
		const payload = await response.json().catch(() => null);
		if (!response.ok) throw new Error(payload?.error ?? `status request failed: HTTP ${response.status}`);
		return payload;
	}
	/** Validate + store a token and sync it into glab. */
	save(input) {
		return postJson("/api/dsh-gitlab-credentials/save", input);
	}
	/** Delete one host (store + glab logout). */
	async remove(host) {
		return (await postJson("/api/dsh-gitlab-credentials/delete", { host })).removed;
	}
	/** Save MR preferences. */
	saveMrPreferences(preferences) {
		return postJson("/api/dsh-gitlab-credentials/mr-preferences", { preferences });
	}
};
//#endregion
//#region src/client/panel.tsx
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
const api = new GitlabApi();
const badgeTones = {
	ok: {
		fg: "var(--dsw-alias-state-success-primary, #5bc98b)",
		bg: "rgba(91, 201, 139, 0.14)",
		border: "rgba(91, 201, 139, 0.32)"
	},
	warn: {
		fg: "var(--dsw-alias-state-warning-primary, #f0b429)",
		bg: "rgba(240, 180, 41, 0.14)",
		border: "rgba(240, 180, 41, 0.32)"
	},
	bad: {
		fg: "var(--dsw-alias-state-error-primary, #ff7777)",
		bg: "rgba(255, 119, 119, 0.14)",
		border: "rgba(255, 119, 119, 0.32)"
	},
	muted: {
		fg: "var(--dsw-alias-label-secondary, #c2c2c8)",
		bg: "rgba(255, 255, 255, 0.06)",
		border: "rgba(255, 255, 255, 0.16)"
	}
};
/** DSH-aligned field styles (inline; no CSS-module build step). */
const styles = {
	root: {
		display: "flex",
		flexDirection: "column",
		gap: "20px",
		padding: "4px 2px",
		maxWidth: "940px",
		color: "var(--dsw-alias-label-primary, #f5f5f5)"
	},
	h2: {
		fontSize: "15px",
		lineHeight: "22px",
		fontWeight: 600,
		margin: "0 0 8px"
	},
	hint: {
		fontSize: "12px",
		lineHeight: "18px",
		color: "var(--dsw-alias-label-tertiary, #a6a6ad)",
		margin: "0 0 12px"
	},
	probe: {
		display: "flex",
		alignItems: "center",
		flexWrap: "wrap",
		gap: "6px",
		fontSize: "12px",
		lineHeight: "20px",
		color: "var(--dsw-alias-label-tertiary, #a6a6ad)",
		margin: "0 0 12px"
	},
	probeCode: {
		fontFamily: "var(--ds-font-family-code, monospace)",
		fontSize: "11px",
		padding: "1px 6px",
		borderRadius: "6px",
		whiteSpace: "nowrap",
		background: "rgba(255, 255, 255, 0.06)",
		color: "var(--dsw-alias-label-secondary, #c2c2c8)"
	},
	formRow: {
		display: "grid",
		gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
		gap: "12px 14px",
		alignItems: "center"
	},
	field: {
		display: "grid",
		gridTemplateColumns: "72px minmax(0, 1fr)",
		gap: "8px",
		alignItems: "center",
		minWidth: 0
	},
	label: {
		minWidth: 0,
		fontSize: "12px",
		lineHeight: "20px",
		whiteSpace: "nowrap",
		color: "var(--dsw-alias-label-secondary, #c2c2c8)"
	},
	action: {
		display: "flex",
		alignItems: "center",
		minHeight: "32px"
	},
	input: {
		boxSizing: "border-box",
		height: "32px",
		padding: "0 10px",
		borderRadius: "8px",
		border: "1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))",
		background: "var(--dsw-alias-bg-base, transparent)",
		color: "var(--dsw-alias-label-primary, #f5f5f5)",
		fontSize: "13px",
		lineHeight: "20px"
	},
	button: {
		height: "32px",
		padding: "0 13px",
		borderRadius: "8px",
		border: "0",
		background: "var(--dsw-alias-button-primary-fill, #4c8dff)",
		color: "var(--dsw-alias-label-primary-foreground, #fff)",
		cursor: "pointer",
		fontSize: "13px",
		fontWeight: 500
	},
	buttonGhost: {
		height: "30px",
		padding: "0 12px",
		borderRadius: "8px",
		border: "1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))",
		background: "transparent",
		color: "var(--dsw-alias-label-secondary, #c2c2c8)",
		cursor: "pointer",
		fontSize: "13px"
	},
	card: {
		boxSizing: "border-box",
		border: "1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 12%))",
		borderRadius: "12px",
		background: "var(--dsw-alias-bg-layer-1, rgb(255 255 255 / 4%))",
		padding: "16px 18px"
	},
	tableWrap: {
		overflowX: "auto",
		margin: "0 -4px"
	},
	table: {
		borderCollapse: "collapse",
		width: "100%",
		minWidth: "760px",
		tableLayout: "fixed",
		fontSize: "12px",
		color: "var(--dsw-alias-label-primary, #f5f5f5)"
	},
	th: {
		textAlign: "left",
		padding: "7px 8px",
		whiteSpace: "nowrap",
		color: "var(--dsw-alias-label-tertiary, #a6a6ad)",
		fontWeight: 500,
		borderBottom: "1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))"
	},
	td: {
		padding: "8px",
		whiteSpace: "nowrap",
		overflow: "hidden",
		textOverflow: "ellipsis",
		color: "var(--dsw-alias-label-secondary, #c2c2c8)",
		borderBottom: "1px solid var(--dsw-alias-border-l1, rgb(255 255 255 / 8%))"
	},
	tdMono: {
		fontFamily: "var(--ds-font-family-code, monospace)",
		fontSize: "11px",
		color: "var(--dsw-alias-label-primary, #f5f5f5)"
	},
	badge: {
		display: "inline-flex",
		alignItems: "center",
		gap: "5px",
		whiteSpace: "nowrap",
		padding: "1px 8px",
		borderRadius: "999px",
		border: "1px solid transparent",
		fontSize: "11px",
		lineHeight: "18px",
		fontWeight: 500
	},
	dot: {
		width: "5px",
		height: "5px",
		borderRadius: "50%",
		display: "inline-block",
		flex: "none"
	},
	notice: {
		margin: "8px 0 0",
		fontSize: "12px",
		lineHeight: "18px"
	},
	muted: { color: "var(--dsw-alias-label-tertiary, #a6a6ad)" }
};
function Badge(props) {
	const tone = badgeTones[props.tone];
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
		style: {
			...styles.badge,
			color: tone.fg,
			background: tone.bg,
			borderColor: tone.border
		},
		title: props.title,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", {
			"aria-hidden": "true",
			style: {
				...styles.dot,
				background: tone.fg
			}
		}), props.children]
	});
}
/** `YYYY-MM-DD HH:mm` in local time; toLocaleString wraps inside narrow cells. */
function formatChecked(iso) {
	if (iso === "") return "—";
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return "—";
	const pad = (value) => String(value).padStart(2, "0");
	return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
/** Token state of one host (validation result only, glab lives in its own cell). */
function checkBadge(host) {
	if (!host.hasToken) return {
		text: "未配置",
		tone: "muted",
		title: "尚未为这台主机保存令牌"
	};
	if (host.lastError !== "") return {
		text: "校验失败",
		tone: "bad",
		title: host.lastError
	};
	if (host.lastChecked === "") return {
		text: "待校验",
		tone: "muted",
		title: "令牌已保存，但还没有成功校验过"
	};
	return {
		text: "有效",
		tone: "ok",
		title: `最近校验：${formatChecked(host.lastChecked)}`
	};
}
/** Whether the saved token actually reached the glab CLI. */
function syncBadge(host, glabAuthed, glabAvailable) {
	if (!host.hasToken) return {
		text: "—",
		tone: "muted",
		title: "没有令牌可同步"
	};
	if (!glabAvailable) return {
		text: "不可用",
		tone: "muted",
		title: "本机找不到 glab，也无法桥接到 WSL；可在插件配置里设置 credentials.glabCommand"
	};
	return glabAuthed ? {
		text: "已同步",
		tone: "ok",
		title: "glab CLI 已登录该主机，MR 流程可直接使用"
	} : {
		text: "未同步",
		tone: "warn",
		title: "保存令牌后会自动同步到 glab；持续失败请查看上方 glab 运行状态"
	};
}
function GitlabSection() {
	const [status, setStatus] = (0, react.useState)(null);
	const [error, setError] = (0, react.useState)("");
	const [notice, setNotice] = (0, react.useState)(null);
	const [busy, setBusy] = (0, react.useState)(false);
	const [host, setHost] = (0, react.useState)("192.168.11.238");
	const [apiHost, setApiHost] = (0, react.useState)("");
	const [apiProtocol, setApiProtocol] = (0, react.useState)("http");
	const [gitProtocol, setGitProtocol] = (0, react.useState)("ssh");
	const [token, setToken] = (0, react.useState)("");
	const [prefs, setPrefs] = (0, react.useState)({
		assignee: "cx",
		targetBranch: "v6.1.0.31_release",
		labels: "",
		milestone: "",
		removeSourceBranch: true
	});
	const refresh = async () => {
		try {
			const value = await api.status();
			setStatus(value);
			setPrefs(value.mrPreferences);
			setError("");
		} catch (cause) {
			setError(cause.message);
		}
	};
	(0, react.useEffect)(() => {
		refresh();
	}, []);
	const glabMap = /* @__PURE__ */ new Map();
	for (const item of status?.hosts ?? []) glabMap.set(item.host, item.glabAuthed);
	const glabProbe = status?.glab;
	const glabAvailable = glabProbe !== void 0 && glabProbe.available;
	const save = async () => {
		setBusy(true);
		setNotice(null);
		setError("");
		try {
			const result = await api.save({
				host,
				token,
				apiProtocol,
				apiHost,
				gitProtocol
			});
			if (result.glabSynced && result.glabVerified) setNotice({
				tone: "ok",
				text: `已保存 ${result.user}@${result.host} · glab 已同步并验证（当前用户 ${result.glabUser}）`
			});
			else if (result.glabSynced) setNotice({
				tone: "warn",
				text: `已保存 ${result.user}@${result.host} · glab 已登录，但回读验证失败：${result.glabError}`
			});
			else setNotice({
				tone: "warn",
				text: `已保存 ${result.user}@${result.host} · glab 未同步：${result.glabError}`
			});
			setToken("");
			await refresh();
		} catch (cause) {
			setError(cause.message);
		} finally {
			setBusy(false);
		}
	};
	const remove = async (target) => {
		setBusy(true);
		setNotice(null);
		setError("");
		try {
			await api.remove(target);
			setNotice({
				tone: "ok",
				text: `已删除 ${target}（glab 已登出）`
			});
			await refresh();
		} catch (cause) {
			setError(cause.message);
		} finally {
			setBusy(false);
		}
	};
	const savePrefs = async () => {
		setBusy(true);
		setNotice(null);
		setError("");
		try {
			await api.saveMrPreferences(prefs);
			setNotice({
				tone: "ok",
				text: "MR 偏好已保存"
			});
			await refresh();
		} catch (cause) {
			setError(cause.message);
		} finally {
			setBusy(false);
		}
	};
	return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
		style: styles.root,
		children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", { children: [
			/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h2", {
				style: styles.h2,
				children: "GitLab 凭据"
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
				style: styles.hint,
				children: [
					"保存时先校验令牌（GET /api/v4/user），再写入凭据文件并同步到 glab CLI，最后回读 glab 当前登录用户做验证。 Windows 宿主会自动经 WSL 调用 glab。令牌仅存于本机 ",
					/* @__PURE__ */ (0, react_jsx_runtime.jsx)("code", { children: "~/.dsh/gitlab-credentials.json" }),
					"（0600），不会出现在对话或日志中。"
				]
			}),
			glabProbe !== void 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("p", {
				style: styles.probe,
				children: [
					/* @__PURE__ */ (0, react_jsx_runtime.jsx)("span", { children: "glab 同步目标" }),
					/* @__PURE__ */ (0, react_jsx_runtime.jsx)(Badge, {
						tone: glabProbe.available ? "ok" : "warn",
						title: glabProbe.error,
						children: glabProbe.available ? "可用" : "不可用"
					}),
					glabProbe.describe !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("code", {
						style: styles.probeCode,
						children: glabProbe.describe
					}),
					glabProbe.error !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("span", {
						style: styles.muted,
						children: [
							"（",
							glabProbe.error,
							"）"
						]
					})
				]
			}),
			/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
				style: styles.card,
				children: [
					/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: styles.formRow,
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							style: styles.field,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
								style: styles.label,
								htmlFor: "gitlab-credential-host",
								children: "Host"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
								id: "gitlab-credential-host",
								style: {
									...styles.input,
									width: "100%"
								},
								value: host,
								onChange: (e) => setHost(e.target.value),
								placeholder: "192.168.11.238"
							})]
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							style: styles.field,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
								style: styles.label,
								htmlFor: "gitlab-credential-api-host",
								children: "API Host"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
								id: "gitlab-credential-api-host",
								style: {
									...styles.input,
									width: "100%"
								},
								value: apiHost,
								onChange: (e) => setApiHost(e.target.value),
								placeholder: "留空 = host"
							})]
						})]
					}),
					/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: {
							...styles.formRow,
							marginTop: "12px"
						},
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							style: styles.field,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
								style: styles.label,
								htmlFor: "gitlab-credential-api-protocol",
								children: "协议"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("select", {
								id: "gitlab-credential-api-protocol",
								style: {
									...styles.input,
									width: "100%"
								},
								value: apiProtocol,
								onChange: (e) => setApiProtocol(e.target.value),
								children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
									value: "http",
									children: "http"
								}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
									value: "https",
									children: "https"
								})]
							})]
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							style: styles.field,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
								style: styles.label,
								htmlFor: "gitlab-credential-git-protocol",
								children: "Git 协议"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("select", {
								id: "gitlab-credential-git-protocol",
								style: {
									...styles.input,
									width: "100%"
								},
								value: gitProtocol,
								onChange: (e) => setGitProtocol(e.target.value),
								children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
									value: "ssh",
									children: "ssh"
								}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("option", {
									value: "https",
									children: "https"
								})]
							})]
						})]
					}),
					/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: {
							...styles.formRow,
							marginTop: "12px"
						},
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
							style: styles.field,
							children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
								style: styles.label,
								htmlFor: "gitlab-credential-token",
								children: "Token"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
								id: "gitlab-credential-token",
								style: {
									...styles.input,
									width: "100%"
								},
								type: "password",
								value: token,
								onChange: (e) => setToken(e.target.value),
								placeholder: "粘贴个人访问令牌 (api scope)",
								autoComplete: "off"
							})]
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
							style: styles.action,
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								style: styles.button,
								disabled: busy || token === "",
								onClick: () => void save(),
								children: busy ? "保存中…" : "保存并校验"
							})
						})]
					}),
					error !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						style: {
							...styles.muted,
							color: badgeTones.bad.fg,
							fontSize: "12px"
						},
						children: error
					}),
					notice !== null && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						style: {
							...styles.notice,
							color: badgeTones[notice.tone].fg
						},
						role: "status",
						"aria-live": "polite",
						children: notice.text
					})
				]
			}),
			status !== null && status.hosts.length > 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
				style: {
					...styles.card,
					marginTop: "12px"
				},
				children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
					style: styles.tableWrap,
					children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("table", {
						style: styles.table,
						"aria-label": "已保存的 GitLab 凭据",
						children: [
							/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("colgroup", { children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "150px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "72px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "64px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "96px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "124px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "82px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "78px" } }),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("col", { style: { width: "64px" } })
							] }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("thead", { children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("tr", { children: [
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "Host"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "用户"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "令牌"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "指纹"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "最近校验"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "校验"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "glab"
								}),
								/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", {
									style: styles.th,
									children: "操作"
								})
							] }) }),
							/* @__PURE__ */ (0, react_jsx_runtime.jsx)("tbody", { children: status.hosts.map((item) => {
								const check = checkBadge(item);
								const sync = syncBadge(item, glabMap.get(item.host) ?? false, glabAvailable);
								return /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("tr", { children: [
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: {
											...styles.td,
											...styles.tdMono
										},
										title: item.host,
										children: item.host
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										children: item.user || "—"
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										children: item.hasToken ? "已存" : "—"
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: {
											...styles.td,
											...styles.tdMono
										},
										title: item.fingerprint || "",
										children: item.fingerprint || "—"
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										title: item.lastChecked || "",
										children: formatChecked(item.lastChecked)
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Badge, {
											tone: check.tone,
											title: check.title,
											children: check.text
										})
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)(Badge, {
											tone: sync.tone,
											title: sync.title,
											children: sync.text
										})
									}),
									/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
										style: styles.td,
										children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
											style: styles.buttonGhost,
											disabled: busy,
											"aria-label": `删除 ${item.host} 的凭据`,
											onClick: () => void remove(item.host),
											children: "删除"
										})
									})
								] }, item.host);
							}) })
						]
					})
				})
			})
		] }), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("section", { children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("h2", {
			style: styles.h2,
			children: "MR 偏好（glab-mr-submit 默认值）"
		}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
			style: styles.card,
			children: [
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					style: styles.formRow,
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: styles.field,
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
							style: styles.label,
							htmlFor: "gitlab-mr-assignee",
							children: "指派人"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							id: "gitlab-mr-assignee",
							style: {
								...styles.input,
								width: "100%"
							},
							value: prefs.assignee,
							onChange: (e) => setPrefs({
								...prefs,
								assignee: e.target.value
							}),
							placeholder: "cx"
						})]
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: styles.field,
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
							style: styles.label,
							htmlFor: "gitlab-mr-target",
							children: "目标分支"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							id: "gitlab-mr-target",
							style: {
								...styles.input,
								width: "100%"
							},
							value: prefs.targetBranch,
							onChange: (e) => setPrefs({
								...prefs,
								targetBranch: e.target.value
							}),
							placeholder: "v6.1.0.35_lts"
						})]
					})]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					style: {
						...styles.formRow,
						marginTop: "12px"
					},
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: styles.field,
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
							style: styles.label,
							htmlFor: "gitlab-mr-labels",
							children: "标签"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							id: "gitlab-mr-labels",
							style: {
								...styles.input,
								width: "100%"
							},
							value: prefs.labels,
							onChange: (e) => setPrefs({
								...prefs,
								labels: e.target.value
							}),
							placeholder: "逗号分隔，如 XTS,应用修复"
						})]
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
						style: styles.field,
						children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("label", {
							style: styles.label,
							htmlFor: "gitlab-mr-milestone",
							children: "里程碑"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							id: "gitlab-mr-milestone",
							style: {
								...styles.input,
								width: "100%"
							},
							value: prefs.milestone,
							onChange: (e) => setPrefs({
								...prefs,
								milestone: e.target.value
							}),
							placeholder: "留空 = 自动推断"
						})]
					})]
				}),
				/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("div", {
					style: {
						...styles.formRow,
						marginTop: "12px"
					},
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsxs)("label", {
						style: {
							...styles.label,
							display: "flex",
							alignItems: "center",
							gap: "8px"
						},
						children: ["合并后删除源分支", /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							type: "checkbox",
							checked: prefs.removeSourceBranch,
							onChange: (e) => setPrefs({
								...prefs,
								removeSourceBranch: e.target.checked
							}),
							style: { accentColor: "var(--dsw-alias-button-primary-fill, #4c8dff)" }
						})]
					}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
						style: styles.action,
						children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
							style: styles.button,
							disabled: busy,
							onClick: () => void savePrefs(),
							children: "保存偏好"
						})
					})]
				})
			]
		})] })]
	});
}
//#endregion
//#region src/client/index.ts
/** Required services: the slot host plus locale (if provided). */
const inject = ["slots", "locale"];
/** Stable section id (tab key) inside the settings page. */
const SECTION_ID = "gitlab-credentials";
/**
* Register the settings section. The slot seat only materializes when the
* settings surface renders it, so registering on apply is safe.
*/
function apply(ctx) {
	try {
		ctx.slots.inject("settings.section", () => ctx.slots.register({
			name: "settings.section",
			id: SECTION_ID,
			order: 30,
			label: () => "GitLab 凭据",
			locale: "dsh-gitlab-credentials"
		}, GitlabSection));
	} catch (error) {
		console.warn("[openharmony-debug-test-pipeline] settings section mount failed:", error);
	}
}
//#endregion
exports.apply = apply;
exports.inject = inject;


		return module.exports;
	}
});