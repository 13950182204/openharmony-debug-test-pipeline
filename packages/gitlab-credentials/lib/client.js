window.__ModuleLoader__.load({ id: "@superjunier/dsh-gitlab-credentials", factory: (require) => {
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
* management (per-host token save/delete with validation + glab sync status)
* and MR preferences (assignee / target branch / labels / milestone). Token
* fields are password inputs; nothing is persisted in the browser.
*/
const api = new GitlabApi();
/** DSH-aligned field styles (inline; no CSS-module build step). */
const styles = {
	root: {
		display: "flex",
		flexDirection: "column",
		gap: "20px",
		padding: "4px 2px",
		maxWidth: "880px",
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
	table: {
		borderCollapse: "collapse",
		width: "100%",
		fontSize: "12px",
		color: "var(--dsw-alias-label-primary, #f5f5f5)"
	},
	th: {
		textAlign: "left",
		padding: "7px 8px",
		color: "var(--dsw-alias-label-tertiary, #a6a6ad)",
		fontWeight: 500,
		borderBottom: "1px solid var(--dsw-alias-border-l2, rgb(255 255 255 / 14%))"
	},
	td: {
		padding: "8px",
		color: "var(--dsw-alias-label-secondary, #c2c2c8)",
		borderBottom: "1px solid var(--dsw-alias-border-l1, rgb(255 255 255 / 8%))"
	},
	ok: { color: "var(--dsw-alias-state-success-primary, #5bc98b)" },
	bad: { color: "var(--dsw-alias-state-error-primary, #ff7777)" },
	muted: { color: "var(--dsw-alias-label-tertiary, #a6a6ad)" },
	mono: {
		fontFamily: "var(--ds-font-family-code, monospace)",
		fontSize: "11px",
		color: "var(--dsw-alias-label-tertiary, #a6a6ad)"
	}
};
/** Deterministic state tag for one host row. */
function hostBadge(host, glab) {
	if (!host.hasToken) return {
		text: "未配置",
		tone: "bad"
	};
	if (host.lastError !== "") return {
		text: "校验失败",
		tone: "bad"
	};
	if (host.lastChecked === "") return {
		text: "已保存未校验",
		tone: "muted"
	};
	return {
		text: glab ? "有效 · glab 已同步" : "有效 · glab 未同步",
		tone: "ok"
	};
}
function GitlabSection() {
	const [status, setStatus] = (0, react.useState)(null);
	const [error, setError] = (0, react.useState)("");
	const [notice, setNotice] = (0, react.useState)("");
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
	const glabMap = (0, react.useMemo)(() => {
		const map = /* @__PURE__ */ new Map();
		for (const host of status?.hosts ?? []) map.set(host.host, host.glabAuthed);
		return map;
	}, [status]);
	const save = async () => {
		setBusy(true);
		setNotice("");
		setError("");
		try {
			const result = await api.save({
				host,
				token,
				apiProtocol,
				apiHost,
				gitProtocol
			});
			setNotice(`已保存 ${result.user}@${result.host}` + (result.glabError === "" ? "，glab 已同步" : `（glab 同步失败：${result.glabError}）`));
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
		setNotice("");
		setError("");
		try {
			await api.remove(target);
			setNotice(`已删除 ${target}（glab 已登出）`);
			await refresh();
		} catch (cause) {
			setError(cause.message);
		} finally {
			setBusy(false);
		}
	};
	const savePrefs = async () => {
		setBusy(true);
		setNotice("");
		setError("");
		try {
			await api.saveMrPreferences(prefs);
			setNotice("MR 偏好已保存");
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
					"保存时校验令牌（GET /api/v4/user）并同步到 glab CLI。令牌仅存于本机 ",
					/* @__PURE__ */ (0, react_jsx_runtime.jsx)("code", { children: "~/.dsh/gitlab-credentials.json" }),
					"（0600），不会出现在对话或日志中。"
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
								children: "Host"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
								children: "API Host"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
								children: "协议"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("select", {
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
								children: "Git 协议"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("select", {
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
								children: "Token"
							}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
								children: "保存并校验"
							})
						})]
					}),
					error !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						style: {
							...styles.bad,
							fontSize: "12px"
						},
						children: error
					}),
					notice !== "" && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("p", {
						style: {
							...styles.ok,
							fontSize: "12px"
						},
						children: notice
					})
				]
			}),
			status !== null && status.hosts.length > 0 && /* @__PURE__ */ (0, react_jsx_runtime.jsx)("div", {
				style: {
					...styles.card,
					marginTop: "12px"
				},
				children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("table", {
					style: styles.table,
					children: [/* @__PURE__ */ (0, react_jsx_runtime.jsx)("thead", { children: /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("tr", { children: [
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
							children: "状态"
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("th", { style: styles.th })
					] }) }), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("tbody", { children: status.hosts.map((item) => /* @__PURE__ */ (0, react_jsx_runtime.jsxs)("tr", { children: [
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: styles.td,
							children: item.host
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: styles.td,
							children: item.user || "-"
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: styles.td,
							children: item.hasToken ? "已存" : "-"
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: {
								...styles.td,
								...styles.mono
							},
							children: item.fingerprint || "-"
						}),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: styles.td,
							children: item.lastChecked ? new Date(item.lastChecked).toLocaleString() : "-"
						}),
						(() => {
							const badge = hostBadge(item, glabMap.get(item.host) ?? false);
							return /* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
								style: {
									...styles.td,
									...styles[badge.tone]
								},
								children: badge.text
							});
						})(),
						/* @__PURE__ */ (0, react_jsx_runtime.jsx)("td", {
							style: styles.td,
							children: /* @__PURE__ */ (0, react_jsx_runtime.jsx)("button", {
								style: styles.buttonGhost,
								disabled: busy,
								onClick: () => void remove(item.host),
								children: "删除"
							})
						})
					] }, item.host)) })]
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
							children: "指派人"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
							children: "目标分支"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
							style: {
								...styles.input,
								width: "100%"
							},
							value: prefs.targetBranch,
							onChange: (e) => setPrefs({
								...prefs,
								targetBranch: e.target.value
							}),
							placeholder: "v6.1.0.31_release"
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
							children: "标签"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
							children: "里程碑"
						}), /* @__PURE__ */ (0, react_jsx_runtime.jsx)("input", {
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
		console.warn("[dsh-gitlab-credentials] settings section mount failed:", error);
	}
}
//#endregion
exports.apply = apply;
exports.inject = inject;


		return module.exports;
	}
});