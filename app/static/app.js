"use strict";

const ROLE = {
  maker: "Refund.Maker",
  checker: "Refund.Checker",
  finance: "Refund.Finance",
  admin: "Refund.Admin",
};
const state = { cfg: null, token: null, me: null, msal: null, account: null,
  idempotencyKey: crypto.randomUUID() };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

function banner(message, kind = "error") {
  const b = $("banner");
  b.textContent = message;
  b.className = kind;
  b.hidden = !message;
}

// ---- auth -----------------------------------------------------------------

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = el("script", { src });
    s.onload = resolve;
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.append(s);
  });
}

async function initEntra(cfg) {
  await loadScript("/static/vendor/msal-browser.min.js");
  state.msal = new msal.PublicClientApplication({
    auth: {
      clientId: cfg.client_id,
      authority: `https://login.microsoftonline.com/${cfg.tenant_id}`,
      redirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await state.msal.initialize();
  const result = await state.msal.handleRedirectPromise();
  state.account = result?.account || state.msal.getAllAccounts()[0] || null;
  $("sign-in").onclick = () => state.msal.loginRedirect({ scopes: [cfg.scope] });
  $("sign-out").onclick = () => state.msal.logoutRedirect({ account: state.account });
  $("sign-in").hidden = !!state.account;
  $("sign-out").hidden = !state.account;
  if (state.account) await loadIdentity();
}

async function accessToken() {
  if (state.cfg.auth_mode === "dev") return state.token;
  try {
    const r = await state.msal.acquireTokenSilent({ scopes: [state.cfg.scope], account: state.account });
    return r.accessToken;
  } catch (e) {
    if (e instanceof msal.InteractionRequiredAuthError) {
      await state.msal.acquireTokenRedirect({ scopes: [state.cfg.scope], account: state.account });
    }
    throw e;
  }
}

async function initDev() {
  const users = await api("/dev/users", {}, false);
  const select = $("dev-user");
  select.replaceChildren(...users.map((u) => el("option", { value: u.user }, u.name)));
  const requested = new URLSearchParams(window.location.search).get("as");
  if (users.some((u) => u.user === requested)) select.value = requested;
  select.addEventListener("change", () => signInDev(select.value).catch((e) => banner(e.message)));
  $("dev-login").hidden = false;
  $("demo-note").hidden = false;
  await signInDev(select.value);
}

async function signInDev(user) {
  const { access_token } = await api("/dev/token", {
    method: "POST",
    body: JSON.stringify({ user }),
  }, false);
  state.token = access_token;
  await loadIdentity();
}

// ---- api ------------------------------------------------------------------

async function request(path, options = {}, auth = true) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (auth) headers.Authorization = `Bearer ${await accessToken()}`;
  const resp = await fetch(path, { ...options, headers });
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const detail = body && body.detail;
    const msg = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail;
    throw new Error(`${resp.status}: ${msg || resp.statusText}`);
  }
  return resp;
}

async function api(path, options = {}, auth = true) {
  const resp = await request(path, options, auth);
  return resp.status === 204 ? null : resp.json();
}

const has = (role) => !!state.me && state.me.roles.includes(role);
const isReader = () => has(ROLE.maker) || has(ROLE.checker) || has(ROLE.finance);
const money = (amount, currency) => `${amount} ${currency}`;

// ---- views ----------------------------------------------------------------

async function loadIdentity() {
  state.me = await api("/api/me");
  const short = state.me.roles.map((r) => r.replace("Refund.", ""));
  $("whoami").replaceChildren(
    state.me.name,
    ...(short.length ? short : ["no roles"]).map((r) => el("span", { class: "chip" }, r)),
  );
  $("summary-section").hidden = !isReader();
  $("create-section").hidden = !has(ROLE.maker);
  $("list-section").hidden = !isReader();
  $("export").hidden = !has(ROLE.finance);
  $("audit-section").hidden = !has(ROLE.finance);
  $("admin-section").hidden = !has(ROLE.admin);
  $("verify-result").textContent = "";
  banner(state.me.roles.length ? "" : "Your account has no Refund roles assigned in Entra ID.");
  await refresh();
}

async function loadSummary() {
  const summary = await api("/api/refunds/summary");
  $("summary-section").replaceChildren(
    ...Object.entries(summary).map(([status, s]) => {
      const totals = Object.entries(s.totals).map(([c, v]) => money(v, c)).join(" · ") || "—";
      return el("div", { class: `card ${status}` },
        el("div", { class: "label" }, status.toLowerCase()),
        el("div", { class: "count" }, String(s.count)),
        el("div", { class: "total" }, totals));
    }),
  );
}

async function loadLimitsHint() {
  const l = await api("/api/refunds/limits");
  $("limits-hint").textContent =
    `Refunds above ${l.dual_approval_threshold} need two different checkers. ` +
    `Maximum per refund: ${l.max_refund_amount}. Requests can't be edited after submission.`;
}

async function act(refund, action) {
  try {
    let body = { expected_version: refund.version };
    if (action === "reject") {
      const reason = prompt("Rejection reason (min 5 characters):");
      if (!reason) return;
      body.reason = reason;
    } else if (action === "approve") {
      body.comment = prompt("Optional approval comment:") || null;
    } else {
      body = undefined;
    }
    await api(`/api/refunds/${refund.id}/${action}`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
    const done = { approve: "approved", reject: "rejected", cancel: "cancelled" }[action];
    banner(`Refund ${done}.`, "ok");
  } catch (e) {
    banner(e.message);
  }
  await refresh();
}

function actionsFor(r) {
  const cell = el("td", { class: "actions" });
  if (r.status === "PENDING") {
    const alreadyApproved = r.approvals.some((a) => a.checker_oid === state.me.oid);
    if (has(ROLE.checker) && r.maker_oid !== state.me.oid && !alreadyApproved) {
      cell.append(
        el("button", { onclick: () => act(r, "approve") }, "Approve"),
        el("button", { class: "danger", onclick: () => act(r, "reject") }, "Reject"),
      );
    }
    if (has(ROLE.maker) && r.maker_oid === state.me.oid) {
      cell.append(el("button", { class: "secondary", onclick: () => act(r, "cancel") }, "Cancel"));
    }
  }
  cell.append(el("button", { class: "link", onclick: () => showHistory(r) }, "History"));
  return cell;
}

async function loadRefunds() {
  const status = $("status-filter").value;
  const refunds = await api(`/api/refunds${status ? `?status=${status}` : ""}`);
  const rows = $("refund-rows");
  rows.replaceChildren(
    ...refunds.map((r) =>
      el("tr", {},
        el("td", {}, new Date(r.created_at).toLocaleString()),
        el("td", {}, `${r.customer_id} / ${r.order_ref}`),
        el("td", {}, money(r.amount, r.currency)),
        el("td", {}, r.reason,
          r.rejection_reason ? el("div", {}, el("code", {}, `Rejected: ${r.rejection_reason}`)) : ""),
        el("td", {}, r.maker_name),
        el("td", {}, el("span", { class: `badge ${r.status}` }, r.status)),
        el("td", {}, `${r.approvals.length}/${r.required_approvals}`,
          ...r.approvals.map((a) => el("div", {}, el("code", {}, a.checker_name)))),
        actionsFor(r)),
    ),
  );
  if (!refunds.length) rows.append(el("tr", {}, el("td", { colspan: "8" }, "No refunds.")));
}

async function showHistory(refund) {
  try {
    const events = await api(`/api/refunds/${refund.id}/history`);
    $("history-title").textContent =
      `History: ${refund.order_ref} (${money(refund.amount, refund.currency)})`;
    $("history-list").replaceChildren(
      ...events.map((e) =>
        el("li", {},
          el("strong", {}, e.action.replace("refund.", "")),
          ` by ${e.actor_name} at ${e.occurred_at.replace("T", " ").slice(0, 19)} UTC`,
          el("div", {}, el("code", {}, JSON.stringify(e.payload))))),
    );
    $("history-dialog").showModal();
  } catch (e) {
    banner(e.message);
  }
}

async function loadAudit() {
  const events = await api("/api/audit/events?limit=1000");
  $("audit-rows").replaceChildren(
    ...events.reverse().map((e) =>
      el("tr", {},
        el("td", {}, String(e.seq)),
        el("td", {}, e.occurred_at.replace("T", " ").slice(0, 19)),
        el("td", {}, e.actor_name),
        el("td", {}, e.actor_roles.map((r) => r.replace("Refund.", "")).join(", ")),
        el("td", {}, e.action),
        el("td", {}, el("code", {}, `${e.entity_type}:${e.entity_id.slice(0, 8)}`)),
        el("td", {}, el("code", {}, JSON.stringify(e.payload))),
        el("td", {}, el("code", { title: e.hash }, `${e.hash.slice(0, 12)}…`))),
    ),
  );
}

async function loadAdmin() {
  const s = await api("/api/admin/settings");
  const form = $("settings-form");
  form.dual_approval_threshold.value = s.dual_approval_threshold;
  form.max_refund_amount.value = s.max_refund_amount;
  $("settings-meta").textContent = s.updated_by
    ? `Last changed by ${s.updated_by} at ${s.updated_at.replace("T", " ").slice(0, 19)} UTC.`
    : "Using deployment defaults.";
  const users = await api("/api/admin/users");
  $("user-rows").replaceChildren(
    ...users.map((u) =>
      el("tr", {},
        el("td", {}, u.name),
        el("td", {}, u.roles.map((r) => r.replace("Refund.", "")).join(", ")),
        el("td", {}, u.last_action),
        el("td", {}, u.last_seen.replace("T", " ").slice(0, 19)))),
  );
  if (!users.length) $("user-rows").append(el("tr", {}, el("td", { colspan: "4" }, "No activity yet.")));
}

async function refresh() {
  try {
    const jobs = [];
    if (isReader()) jobs.push(loadSummary(), loadRefunds());
    if (has(ROLE.maker)) jobs.push(loadLimitsHint());
    if (has(ROLE.finance)) jobs.push(loadAudit());
    if (has(ROLE.admin)) jobs.push(loadAdmin());
    await Promise.all(jobs);
  } catch (e) {
    banner(e.message);
  }
}

async function exportCsv() {
  try {
    const status = $("status-filter").value;
    const resp = await request(`/api/refunds/export.csv${status ? `?status=${status}` : ""}`);
    const url = URL.createObjectURL(await resp.blob());
    const a = el("a", { href: url, download: `refunds-${new Date().toISOString().slice(0, 10)}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    banner(e.message);
  }
}

function wireEvents() {
  $("status-filter").addEventListener("change", refresh);
  $("refresh").addEventListener("click", refresh);
  $("export").addEventListener("click", exportCsv);
  $("history-close").addEventListener("click", () => $("history-dialog").close());
  $("create-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const data = Object.fromEntries(new FormData(form));
    data.currency = data.currency.toUpperCase();
    try {
      const r = await api("/api/refunds", {
        method: "POST",
        headers: { "Idempotency-Key": state.idempotencyKey },
        body: JSON.stringify(data),
      });
      form.reset();
      state.idempotencyKey = crypto.randomUUID();
      const n = r.required_approvals;
      banner(`Refund submitted; needs ${n} approval${n > 1 ? "s from different checkers" : ""}.`, "ok");
      await refresh();
    } catch (e) {
      banner(e.message);
    }
  });
  $("settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries(new FormData(ev.target))),
      });
      banner("Settings saved and recorded in the audit trail.", "ok");
      await refresh();
    } catch (e) {
      banner(e.message);
    }
  });
  $("verify").addEventListener("click", async () => {
    const out = $("verify-result");
    try {
      const r = await api("/api/audit/verify");
      out.className = r.valid ? "valid" : "invalid";
      out.textContent = r.valid
        ? `Chain intact (${r.events_checked} events)`
        : `TAMPERING DETECTED at #${r.first_invalid_seq}: ${r.error}`;
    } catch (e) {
      banner(e.message);
    }
  });
}

async function init() {
  wireEvents();
  state.cfg = await api("/ui-config", {}, false);
  if (state.cfg.auth_mode === "dev") await initDev();
  else await initEntra(state.cfg);
}

init().catch((e) => banner(e.message));
