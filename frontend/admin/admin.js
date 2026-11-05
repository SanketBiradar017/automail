/* AutoMail Admin Panel. Talks only to /api/admin/*; every number comes from the API. */
(() => {
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const S = { me: null, view: "overview", range: "30d", start: "", end: "", token: 0 };
const RANK = { support: 1, admin: 2, super_admin: 3 };
const can = (role) => S.me && RANK[S.me.role] >= RANK[role];

/* ---------------------------------------------------------------- helpers */
class ApiError extends Error { constructor(m, status) { super(m); this.status = status; } }

async function api(path, { method = "GET", body } = {}) {
    const res = await fetch("/api/admin" + path, {
        method, credentials: "same-origin",
        headers: { "X-Admin-Request": "1", ...(body ? { "Content-Type": "application/json" } : {}) },
        body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && S.me) { S.me = null; showLogin(); }
    if (!res.ok) {
        const d = data.detail;
        throw new ApiError(Array.isArray(d) ? d.map(x => x.msg).join("; ") : (d || `Request failed (${res.status})`), res.status);
    }
    return data;
}
const qs = (o) => { const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== "" && v != null) p.set(k, v); }); return p.toString(); };
const rangeQS = () => qs({ range: S.range, tz: TZ, ...(S.range === "custom" ? { start: S.start, end: S.end } : {}) });

const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
const money = (cents, cur = "USD") => new Intl.NumberFormat([], { style: "currency", currency: cur }).format((cents || 0) / 100);
const dt = (iso) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
const day = (s) => new Date(s + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric" });
const pill = (s, label) => `<span class="pill ${esc(s)}">${esc(label ?? String(s).replace(/_/g, " "))}</span>`;
const moneyMap = (m) => { const e = Object.entries(m || {}); return e.length ? e.map(([c, v]) => money(v, c)).join(" · ") : money(0); };

function toast(msg, kind = "") { const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg; $("toasts").append(t); setTimeout(() => t.remove(), 4200); }
const skeleton = () => `<div class="card skel-card"><span class="skel" style="width:40%"></span><span class="skel"></span><span class="skel" style="width:80%"></span><span class="skel" style="width:60%"></span></div>`;
const empty = (msg, ico = "📭") => `<div class="state"><div class="ico">${ico}</div><div>${esc(msg)}</div></div>`;
const errState = (msg) => `<div class="state err"><div class="ico">⚠️</div><div>${esc(msg)}</div><button class="btn small" data-retry>Retry</button></div>`;

function modal(html, wide) {
    const card = $("modalCard"); card.className = "modal-card" + (wide ? " wide" : ""); card.innerHTML = html; $("modal").hidden = false;
    const f = card.querySelector("input,select,textarea,button"); f && f.focus();
}
const closeModal = () => { $("modal").hidden = true; $("modalCard").innerHTML = ""; };
function confirmBox(title, text, okLabel, danger = true) {
    return new Promise(res => {
        modal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p><div class="modal-actions"><button class="btn" data-x>Cancel</button><button class="btn ${danger ? "danger" : "primary"}" data-ok>${esc(okLabel)}</button></div>`);
        $("modalCard").onclick = (e) => { if (e.target.closest("[data-x]")) { closeModal(); res(false); } if (e.target.closest("[data-ok]")) { closeModal(); res(true); } };
    });
}
async function act(btn, fn, okMsg) {
    if (btn) btn.disabled = true;
    try { await fn(); okMsg && toast(okMsg, "ok"); return true; }
    catch (e) { toast(e.message, "err"); return false; }
    finally { if (btn) btn.disabled = false; }
}

function pager(meta) {
    if (!meta || meta.total === 0) return "";
    const from = (meta.page - 1) * meta.page_size + 1, to = Math.min(meta.total, meta.page * meta.page_size);
    return `<div class="pager"><span>${from}–${to} of ${num(meta.total)}</span><div>
        <button class="btn small" data-page="${meta.page - 1}" ${meta.page <= 1 ? "disabled" : ""}>← Prev</button>
        <button class="btn small" data-page="${meta.page + 1}" ${meta.page >= meta.pages ? "disabled" : ""}>Next →</button></div></div>`;
}
const table = (cols, rows, rowAttr) => rows.length ? `<div class="tbl-wrap"><table><thead><tr>${cols.map(c => `<th class="${c.n ? "n" : ""}">${esc(c.h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr ${rowAttr ? rowAttr(r, i) : ""}>${cols.map(c => `<td class="${c.n ? "n" : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : "";

/* ----------------------------------------------------------------- charts */
function lineChart(el, rows, defs, { fmt = num, height = 230 } = {}) {
    const hidden = new Set();
    const draw = () => {
        const vis = defs.filter(d => !hidden.has(d.key));
        const W = 760, H = height, L = 40, R = 12, T = 12, B = 26, iw = W - L - R, ih = H - T - B;
        const max = Math.max(1, ...rows.flatMap(r => vis.map(d => r[d.key] || 0)));
        const x = i => L + (rows.length === 1 ? iw / 2 : i / (rows.length - 1) * iw), y = v => T + ih - (v / max) * ih;
        const step = Math.max(1, Math.ceil(rows.length / 7));
        const grid = [0, .5, 1].map(f => { const v = max * f; return `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="tx" x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${esc(fmt(Math.round(v * 100) / 100))}</text>`; }).join("");
        const xl = rows.map((r, i) => i % step ? "" : `<text class="tx" x="${x(i)}" y="${H - 8}" text-anchor="middle">${day(r.date)}</text>`).join("");
        const lines = vis.map(d => `<polyline class="ln" style="stroke:${d.color}" points="${rows.map((r, i) => `${x(i)},${y(r[d.key] || 0)}`).join(" ")}"/>` +
            (rows.length <= 45 ? rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r[d.key] || 0)}" r="2.6" fill="${d.color}"/>`).join("") : "")).join("");
        el.innerHTML = `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img">${grid}${xl}${lines}<line class="cross" y1="${T}" y2="${T + ih}" x1="0" x2="0" hidden/></svg><div class="tip" hidden></div></div>
            <div class="legend">${defs.map(d => `<button type="button" data-k="${d.key}" class="${hidden.has(d.key) ? "off" : ""}"><i style="background:${d.color}"></i>${esc(d.label)}</button>`).join("")}</div>`;
        const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross");
        svg.onpointermove = (ev) => {
            const b = svg.getBoundingClientRect(), px = (ev.clientX - b.left) / b.width * W;
            const i = Math.max(0, Math.min(rows.length - 1, rows.length === 1 ? 0 : Math.round((px - L) / iw * (rows.length - 1))));
            cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.hidden = false; tip.hidden = false;
            tip.innerHTML = `<b>${day(rows[i].date)}</b>` + defs.map(d => `<div><i style="background:${d.color}"></i><span>${esc(d.label)}</span><span>${esc(fmt(rows[i][d.key] || 0))}</span></div>`).join("");
            tip.style.left = Math.min(Math.max(x(i) / W * b.width + 12, 4), b.width - 170) + "px";
        };
        svg.onpointerleave = () => { tip.hidden = true; cross.hidden = true; };
        el.querySelectorAll(".legend button").forEach(b => b.onclick = () => { hidden.has(b.dataset.k) ? hidden.delete(b.dataset.k) : hidden.add(b.dataset.k); if (hidden.size === defs.length) hidden.clear(); draw(); });
    };
    if (!rows.length) { el.innerHTML = empty("No data in this range.", "📈"); return; }
    draw();
}
const barList = (items, valueFmt = num) => {
    const max = Math.max(1, ...items.map(i => i.value));
    return items.length ? `<div class="bars">${items.map(i => `<div class="bar-row"><span class="ellipsis" title="${esc(i.label)}">${esc(i.label)}</span><div class="bar-track"><div class="bar-fill" style="width:${i.value / max * 100}%;${i.color ? `background:${i.color}` : ""}"></div></div><b>${esc(valueFmt(i.value))}</b></div>`).join("")}</div>` : empty("Nothing to show yet.");
};
const kpi = (label, value, sub = "", cls = "") => `<div class="card kpi ${cls}"><div class="lbl">${esc(label)}</div><div class="val">${value}</div><div class="sub">${esc(sub)}</div></div>`;

/* ------------------------------------------------------------------ views */
const ICON = {
    overview: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5"/><path d="M16 5.2a3.5 3.5 0 0 1 0 6.6M18 14.8c2 .6 3.2 2.3 3.5 5.2"/>',
    emails: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7 8.5 6 8.5-6"/>',
    gmail: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    ai: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3.2"/>',
    billing: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 10h18M7 15h4"/>',
    followups: '<path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"/>',
    system: '<path d="M3 12h4l2.5-6 4 12 2.5-6h5"/>',
    logs: '<path d="M7 4h10a2 2 0 0 1 2 2v14l-3-2-2 2-2-2-2 2-2-2-3 2V6a2 2 0 0 1 2-2z"/><path d="M9 9h6M9 13h6"/>',
    notifications: '<path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.800l.1-.1a1.700 1.700 0 0 0 .3-1.800 1.700 1.700 0 0 0-1.500-1H3a2 2 0 1 1 0-4h.1a1.700 1.700 0 0 0 1.500-1.100 1.700 1.700 0 0 0-.3-1.800l-.1-.1a2 2 0 1 1 2.800-2.800l.1.1a1.700 1.700 0 0 0 1.800.3H9a1.700 1.700 0 0 0 1-1.500V3a2 2 0 1 1 4 0v.1a1.700 1.700 0 0 0 1 1.500 1.700 1.700 0 0 0 1.800-.3l.1-.1a2 2 0 1 1 2.800 2.800l-.1.1a1.700 1.700 0 0 0-.3 1.800V9a1.700 1.700 0 0 0 1.500 1H21a2 2 0 1 1 0 4h-.1a1.700 1.700 0 0 0-1.500 1z"/>',
    admins: '<path d="M12 3 4.5 6v5.5c0 4.4 3 7.6 7.5 9.5 4.5-1.900 7.5-5.100 7.500-9.500V6z"/><path d="m9 12 2 2 4-4"/>',
};
const VIEWS = [
    { id: "overview", title: "Overview", group: "Dashboard", ranged: true, render: vOverview },
    { id: "users", title: "Users", group: "Customers", render: vUsers },
    { id: "emails", title: "Email Activity", group: "Customers", ranged: true, render: vEmails },
    { id: "followups", title: "Follow-ups", group: "Customers", ranged: true, render: vFollowups },
    { id: "gmail", title: "Gmail Connections", group: "Customers", render: vGmail },
    { id: "ai", title: "AI Usage", group: "Platform", ranged: true, render: vAI },
    { id: "billing", title: "Billing", group: "Platform", ranged: true, render: vBilling },
    { id: "system", title: "System Health", group: "Operations", render: vSystem },
    { id: "logs", title: "Logs", group: "Operations", ranged: true, render: vLogs },
    { id: "notifications", title: "Notifications", group: "Operations", render: vNotifications },
    { id: "admins", title: "Admins", group: "Operations", role: "super_admin", render: vAdmins },
    { id: "settings", title: "Settings", group: "Operations", role: "super_admin", render: vSettings },
];

/* Each render(el, token) returns nothing; it must bail if S.token changed (user navigated). */
const alive = (t) => t === S.token;
async function load(el, t, fn) {
    el.innerHTML = skeleton();
    try { await fn(); } catch (e) { if (alive(t)) { el.innerHTML = errState(e.message); el.querySelector("[data-retry]").onclick = () => load(el, t, fn); } }
}

/* ---- Overview */
async function vOverview(el, t) {
    el.innerHTML = `<div class="grid cards">${Array(8).fill(skeleton()).join("")}</div>`;
    const run = async () => {
        const d = await api("/overview?" + rangeQS()); if (!alive(t)) return;
        const c = d.counts, cur = d.revenue_currency;
        const failRate = c.emails_sent + c.emails_failed ? Math.round(1000 * c.emails_failed / (c.emails_sent + c.emails_failed)) / 10 : null;
        el.innerHTML = `
            <div class="grid cards">
                ${kpi("Total users", num(c.users_total), `${num(c.users_new)} new in range · ${num(c.users_suspended)} suspended`)}
                ${kpi("Active users", num(c.users_active), "sent mail or signed in during range")}
                ${kpi("Emails generated", num(c.emails_generated), "AI drafts in range")}
                ${kpi("Emails sent", num(c.emails_sent), `${num(c.emails_scheduled)} scheduled · ${num(c.emails_pending)} pending`)}
                ${kpi("Failed emails", num(c.emails_failed), failRate == null ? "no sends yet" : failRate + "% of attempts", c.emails_failed ? "bad" : "")}
                ${kpi("Active subscriptions", num(c.subscriptions_active), `${num(c.subscriptions_canceled)} canceled in range`)}
                ${kpi("Revenue", moneyMap(d.revenue), "succeeded payments in range")}
                ${kpi("MRR", moneyMap(d.mrr), "active subscriptions, monthly-normalised")}
            </div>
            <div class="grid cols-3">
                <div class="card"><div class="card-head"><h3>Email volume</h3></div><div id="ovA"></div></div>
                <div class="card"><div class="card-head"><h3>New users</h3></div><div id="ovB"></div></div>
            </div>
            <div class="grid cols-2" style="margin-top:16px">
                <div class="card"><div class="card-head"><h3>Revenue (${esc(cur)})</h3></div><div id="ovC"></div></div>
                <div class="card"><div class="card-head"><h3>At a glance</h3></div>${barList([
                    { label: "Gmail connections", value: c.gmail_connected }, { label: "Scheduled emails", value: c.emails_scheduled },
                    { label: "Pending emails", value: c.emails_pending }, { label: "Suspended users", value: c.users_suspended, color: "var(--bad)" }])}</div>
            </div>
            <p class="muted" style="margin-top:14px;font-size:12.5px">${esc(d.note)}</p>`;
        lineChart($("ovA"), d.series, [{ key: "sent", label: "Sent", color: "var(--c1)" }, { key: "failed", label: "Failed", color: "var(--c5)" }, { key: "generated", label: "Generated", color: "var(--c2)" }]);
        lineChart($("ovB"), d.series, [{ key: "new_users", label: "New users", color: "var(--c3)" }], { height: 230 });
        lineChart($("ovC"), d.series, [{ key: "revenue_cents", label: "Revenue", color: "var(--c4)" }], { fmt: v => money(v, cur) });
    };
    await load(el, t, run);
}

/* ---- Users */
async function vUsers(el, t) {
    const st = { search: "", status: "", gmail: "", sort: "created", direction: "desc", page: 1 };
    el.innerHTML = `<div class="page-head"><div><p>Customers who have signed in with Google. Account controls never expose tokens or email contents.</p></div></div>
        <div class="card"><div class="toolbar">
            <input class="grow" id="uSearch" placeholder="Search by name or email…" aria-label="Search users">
            <select id="uStatus" aria-label="Status"><option value="">All statuses</option><option value="active">Active</option><option value="suspended">Suspended</option></select>
            <select id="uGmail" aria-label="Gmail"><option value="">Any Gmail state</option><option value="connected">Gmail connected</option><option value="disconnected">Gmail disconnected</option></select>
            <select id="uSort" aria-label="Sort"><option value="created">Newest</option><option value="last_login">Last login</option><option value="emails">Most emails</option><option value="email">Email A–Z</option></select>
        </div><div id="uBody"></div></div>`;
    const body = $("uBody");
    const run = async () => {
        body.innerHTML = skeleton();
        try {
            const d = await api("/users?" + qs({ ...st, page_size: 20 })); if (!alive(t)) return;
            body.innerHTML = d.users.length ? table([
                { h: "User", f: u => `<strong>${esc(u.name || u.email)}</strong><span class="sub">${esc(u.email)}</span>` },
                { h: "Status", f: u => pill(u.status) }, { h: "Gmail", f: u => u.gmail_connected ? pill("connected") : pill("disconnected") },
                { h: "Sent", n: 1, f: u => num(u.emails_sent) }, { h: "Failed", n: 1, f: u => num(u.emails_failed) },
                { h: "Last login", f: u => dt(u.last_login_at) }, { h: "Joined", f: u => dt(u.created_at) }],
                d.users, u => `class="click" data-uid="${u.id}" tabindex="0"`) + pager(d) : empty(st.search || st.status || st.gmail ? "No users match these filters." : "No users have signed in yet.", "👤");
            body.onclick = (e) => { const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; run(); return; } const r = e.target.closest("[data-uid]"); r && userModal(+r.dataset.uid, run); };
            body.onkeydown = (e) => { if (e.key === "Enter") { const r = e.target.closest("[data-uid]"); r && userModal(+r.dataset.uid, run); } };
        } catch (e) { body.innerHTML = errState(e.message); body.querySelector("[data-retry]").onclick = run; }
    };
    let timer; $("uSearch").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { st.search = e.target.value; st.page = 1; run(); }, 300); };
    [["uStatus", "status"], ["uGmail", "gmail"], ["uSort", "sort"]].forEach(([id, k]) => $(id).onchange = (e) => { st[k] = e.target.value; st.page = 1; run(); });
    run();
}

async function userModal(id, refresh) {
    modal(`<div class="skel-card">${skeleton()}</div>`, true);
    try {
        const d = await api("/users/" + id); const u = d.user, g = d.gmail, e = d.emails;
        modal(`<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start"><div><h3>${esc(u.name || u.email)}</h3><span class="muted">${esc(u.email)}</span></div>${pill(u.status)}</div>
            ${u.status === "suspended" ? `<p class="form-error">Suspended ${dt(u.suspended_at)}${u.suspended_reason ? " — " + esc(u.suspended_reason) : ""}</p>` : ""}
            <div class="grid cards" style="margin:16px 0 8px;grid-template-columns:repeat(auto-fit,minmax(110px,1fr))">
                ${kpi("Sent", num(e.sent))}${kpi("Failed", num(e.failed), "", e.failed ? "bad" : "")}${kpi("Replied", num(e.replied))}${kpi("Bounced", num(e.bounced))}${kpi("AI requests", num(d.ai.requests))}</div>
            <div class="grid cols-2"><div><h4 style="margin:12px 0 8px">Account</h4><dl class="kv">
                <dt>Joined</dt><dd>${dt(u.created_at)}</dd><dt>Last login</dt><dd>${dt(u.last_login_at)} (${num(u.login_count)} logins)</dd>
                <dt>Last email sent</dt><dd>${dt(e.last_sent_at)}</dd><dt>AI tokens</dt><dd>${num(d.ai.input_tokens)} in / ${num(d.ai.output_tokens)} out</dd>
                <dt>Subscription</dt><dd>${d.subscription ? `${esc(d.subscription.plan)} ${pill(d.subscription.status)}` : "None"}</dd></dl></div>
            <div><h4 style="margin:12px 0 8px">Gmail connection</h4><dl class="kv"><dt>State</dt><dd>${pill(g.state)}</dd><dt>Connected</dt><dd>${dt(g.connected_at)}</dd>
                <dt>Token expires</dt><dd>${dt(g.token_expiry)}</dd><dt>Refresh token</dt><dd>${g.has_refresh_token ? "Stored (hidden)" : "None"}</dd>
                <dt>Scopes</dt><dd>${g.scopes.map(s => `<span class="pill">${esc(s)}</span>`).join(" ") || "—"}</dd></dl></div></div>
            <h4 style="margin:16px 0 8px">Recent activity <small>(metadata only — message content is never shown)</small></h4>
            ${d.recent_emails.length ? table([{ h: "When", f: r => dt(r.at) }, { h: "Type", f: r => esc(r.kind) }, { h: "To", f: r => esc(r.recipient) }, { h: "Status", f: r => pill(r.status) }], d.recent_emails) : empty("No emails yet.")}
            ${d.audit.length ? `<h4 style="margin:16px 0 8px">Admin actions on this user</h4>${table([{ h: "When", f: r => dt(r.at) }, { h: "Admin", f: r => esc(r.admin) }, { h: "Action", f: r => esc(r.action) }, { h: "Detail", f: r => esc(r.detail || "") }], d.audit)}` : ""}
            <div class="modal-actions">
                ${can("admin") ? (u.status === "suspended" ? `<button class="btn" data-do="reactivate">Reactivate</button>` : `<button class="btn danger" data-do="suspend">Suspend</button>`) : ""}
                ${can("admin") && g.state !== "disconnected" ? `<button class="btn danger" data-do="disconnect">Disconnect Gmail</button>` : ""}
                <button class="btn primary" data-x>Close</button></div>`, true);
        $("modalCard").onclick = async (ev) => {
            if (ev.target.closest("[data-x]")) return closeModal();
            const b = ev.target.closest("[data-do]"); if (!b) return;
            const k = b.dataset.do; let body;
            if (k === "suspend") {
                body = await promptBox("Suspend user", "They will be blocked from signing in and from using Gmail through AutoMail.", "Reason (optional)");
                if (body === null) return;
            } else if (k === "disconnect") {
                if (!await confirmBox("Disconnect Gmail?", "Stored credentials are deleted. The user must sign in with Google again.", "Disconnect")) return;
            }
            const done = await act(b, () => api(`/users/${id}/${k === "disconnect" ? "disconnect-gmail" : k}`, { method: "POST", body: k === "suspend" ? { reason: body } : undefined }), "Done");
            if (done) { closeModal(); refresh && refresh(); }
        };
    } catch (e) { modal(errState(e.message)); }
}
function promptBox(title, text, label) {
    return new Promise(res => {
        modal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p><label>${esc(label)}</label><input id="pbIn" maxlength="300"><div class="modal-actions"><button class="btn" data-x>Cancel</button><button class="btn danger" data-ok>Confirm</button></div>`);
        $("modalCard").onclick = (e) => { if (e.target.closest("[data-x]")) { closeModal(); res(null); } if (e.target.closest("[data-ok]")) { const v = $("pbIn").value; closeModal(); res(v); } };
    });
}

/* ---- Email activity */
async function vEmails(el, t) {
    const st = { source: "sent", status: "", search: "", page: 1 };
    el.innerHTML = `<div class="page-head"><div><p>Delivery activity across all users. Subjects and bodies are never shown; recipients are masked.</p></div></div>
        <div id="eSum" class="grid cards"></div><div class="tabs" id="eTabs"><button data-s="sent" class="on">Sent &amp; failed</button><button data-s="scheduled">Scheduled &amp; pending</button></div>
        <div class="card"><div class="toolbar"><input class="grow" id="eSearch" placeholder="Search user or recipient…"><select id="eStatus"></select></div><div id="eBody"></div></div>`;
    const opts = { sent: [["", "All"], ["sent", "Sent"], ["failed", "Failed"], ["pending", "Pending"], ["replied", "Replied"], ["bounced", "Bounced"]],
                   scheduled: [["", "All"], ["scheduled", "Scheduled"], ["pending", "Pending"], ["processing", "Processing"], ["failed", "Failed"], ["sent", "Sent"], ["cancelled", "Cancelled"], ["draft", "Draft"]] };
    const fillOpts = () => { $("eStatus").innerHTML = opts[st.source].map(([v, l]) => `<option value="${v}" ${st.status === v ? "selected" : ""}>${l}</option>`).join(""); };
    const run = async () => {
        const body = $("eBody"); body.innerHTML = skeleton();
        try {
            const d = await api("/emails?" + qs({ ...st, page_size: 20 }) + "&" + rangeQS()); if (!alive(t)) return;
            $("eSum").innerHTML = ["sent", "failed", "scheduled", "pending"].map(k => `<button class="card kpi ${k === "failed" && d.summary.failed ? "bad" : ""}" data-sum="${k}" style="text-align:left;cursor:pointer"><div class="lbl">${k[0].toUpperCase() + k.slice(1)}</div><div class="val">${num(d.summary[k])}</div><div class="sub">${k === "scheduled" || k === "pending" ? "current queue" : "in range"}</div></button>`).join("");
            body.innerHTML = d.items.length ? table(st.source === "sent" ? [
                { h: "When", f: r => dt(r.at) }, { h: "User", f: r => esc(r.user || "—") }, { h: "Recipient", f: r => esc(r.recipient) }, { h: "Type", f: r => esc(r.kind) },
                { h: "Status", f: r => pill(r.status) }, { h: "Files", n: 1, f: r => r.attachments || "" }, { h: "Error", f: r => r.error ? `<span class="ellipsis" title="${esc(r.error)}">${esc(r.error)}</span>` : "" }] : [
                { h: "Fires at", f: r => dt(r.at) }, { h: "Recipient", f: r => esc(r.recipient) }, { h: "Type", f: r => esc(r.kind) }, { h: "Timezone", f: r => esc(r.timezone) },
                { h: "Status", f: r => pill(r.status) }, { h: "Sent", n: 1, f: r => r.send_count }, { h: "Error", f: r => r.error ? `<span class="ellipsis" title="${esc(r.error)}">${esc(r.error)}</span>` : "" }], d.items) + pager(d)
                : empty("No emails match these filters in this range.");
        } catch (e) { body.innerHTML = errState(e.message); body.querySelector("[data-retry]").onclick = run; }
    };
    el.onclick = (e) => {
        const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; return run(); }
        const tab = e.target.closest("#eTabs [data-s]"); if (tab) { st.source = tab.dataset.s; st.status = ""; st.page = 1; document.querySelectorAll("#eTabs button").forEach(b => b.classList.toggle("on", b === tab)); fillOpts(); return run(); }
        const s = e.target.closest("[data-sum]"); if (s) { const k = s.dataset.sum; st.source = (k === "scheduled" || k === "pending") ? "scheduled" : "sent"; st.status = k; st.page = 1;
            document.querySelectorAll("#eTabs button").forEach(b => b.classList.toggle("on", b.dataset.s === st.source)); fillOpts(); run(); }
    };
    let timer; $("eSearch").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { st.search = e.target.value; st.page = 1; run(); }, 300); };
    $("eStatus").onchange = (e) => { st.status = e.target.value; st.page = 1; run(); };
    fillOpts(); run();
}

/* ---- Follow-ups */
async function vFollowups(el, t) {
    const st = { status: "", page: 1 };
    el.innerHTML = `<div class="page-head"><div><p>Status of automated follow-up sequences. Recipients are masked; message text is not shown.</p></div></div><div id="fCards"></div><div class="card" style="margin-bottom:16px"><div class="card-head"><h3>Sequences created</h3></div><div id="fChart"></div></div><div class="card"><div class="toolbar"><select id="fStatus"><option value="">All statuses</option>${["waiting", "due", "sent", "replied", "stopped", "failed", "cancelled"].map(s => `<option>${s}</option>`).join("")}</select></div><div id="fBody"></div></div>`;
    const run = async () => {
        $("fBody").innerHTML = skeleton();
        try {
            const d = await api("/followups?" + qs({ ...st, page_size: 20 }) + "&" + rangeQS()); if (!alive(t)) return;
            const bs = d.by_status, total = Object.values(bs).reduce((a, b) => a + b, 0);
            $("fCards").innerHTML = `<div class="grid cards">${kpi("Sequences", num(total), `${num(d.followups_sent_total)} follow-ups sent`)}${["waiting", "due", "replied", "failed", "stopped", "cancelled"].map(s => kpi(s[0].toUpperCase() + s.slice(1), num(bs[s] || 0), "", s === "failed" && bs[s] ? "bad" : "")).join("")}</div>`;
            lineChart($("fChart"), d.series, [{ key: "created", label: "Created", color: "var(--c1)" }], { height: 180 });
            $("fBody").innerHTML = d.items.length ? table([{ h: "Recipient", f: r => esc(r.recipient) }, { h: "User", f: r => esc(r.user || "—") }, { h: "Status", f: r => pill(r.status) },
                { h: "Progress", f: r => `${r.sent}/${r.max}` }, { h: "Next action", f: r => dt(r.next_action_at) }, { h: "Last checked", f: r => dt(r.last_checked_at) },
                { h: "Error", f: r => r.error ? `<span class="ellipsis" title="${esc(r.error)}">${esc(r.error)}</span>` : "" }], d.items) + pager(d) : empty("No follow-up sequences.");
        } catch (e) { $("fBody").innerHTML = errState(e.message); $("fBody").querySelector("[data-retry]").onclick = run; }
    };
    el.onclick = (e) => { const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; run(); } };
    $("fStatus").onchange = (e) => { st.status = e.target.value; st.page = 1; run(); };
    run();
}

/* ---- Gmail */
async function vGmail(el, t) {
    const run = async () => {
        const d = await api("/gmail"); if (!alive(t)) return;
        const s = d.summary;
        el.innerHTML = `<div class="page-head"><div><p>OAuth connection health. Tokens are never displayed; only their presence and expiry.</p></div></div>
            <div class="grid cards">${kpi("Connected", num(s.connected), "", "ok")}${kpi("Re-auth required", num(s.reauth_required), "token expired, no refresh token", s.reauth_required ? "bad" : "")}${kpi("Disconnected", num(s.disconnected), "signed in before, no live connection", s.disconnected ? "bad" : "")}${kpi("Total accounts", num(s.total))}</div>
            <div class="card">${d.connections.length ? table([
                { h: "Account", f: r => `<strong>${esc(r.email)}</strong>${r.suspended ? ' <span class="pill suspended">suspended</span>' : ""}` }, { h: "State", f: r => pill(r.state) },
                { h: "Connected", f: r => dt(r.connected_at) }, { h: "Token expiry", f: r => dt(r.token_expiry) }, { h: "Refresh token", f: r => r.has_refresh_token ? "Yes" : '<span class="muted">No</span>' },
                { h: "Scopes", f: r => r.scopes.map(x => `<span class="pill">${esc(x)}</span>`).join(" ") || "—" }, { h: "Disconnected", f: r => dt(r.disconnected_at) },
                { h: "", f: r => r.user_id && r.state !== "disconnected" && can("admin") ? `<button class="btn small danger" data-dc="${r.user_id}">Disconnect</button>` : "" }], d.connections) : empty("No accounts yet.", "🔗")}</div>`;
        el.onclick = async (e) => {
            const b = e.target.closest("[data-dc]"); if (!b) return;
            if (await confirmBox("Disconnect Gmail?", "Stored credentials are deleted. The user must sign in again.", "Disconnect"))
                (await act(b, () => api(`/users/${b.dataset.dc}/disconnect-gmail`, { method: "POST" }), "Disconnected")) && run();
        };
    };
    await load(el, t, run);
}

/* ---- AI usage */
async function vAI(el, t) {
    await load(el, t, async () => {
        const d = await api("/ai?" + rangeQS()); if (!alive(t)) return;
        const o = d.totals, p = d.pricing;
        el.innerHTML = `<div class="page-head"><div><p>Gemini API usage recorded from real calls (prompts and replies are never stored).</p></div></div>
            <div class="grid cards">${kpi("Requests", num(o.requests), `${num(o.success)} succeeded`)}${kpi("Input tokens", num(o.input_tokens))}${kpi("Output tokens", num(o.output_tokens))}
                ${kpi("Estimated cost", "$" + o.estimated_cost.toFixed(4), `at $${p.input_per_million}/M in, $${p.output_per_million}/M out`)}${kpi("Avg latency", o.avg_latency_ms + " ms")}${kpi("Error rate", o.error_rate == null ? "—" : o.error_rate + "%", `${num(o.errors)} failed`, o.errors ? "bad" : "")}</div>
            <p class="muted" style="font-size:12.5px;margin:0 0 14px">${esc(p.note)}</p>
            <div class="grid cols-2"><div class="card"><div class="card-head"><h3>Requests &amp; errors</h3></div><div id="aiA"></div></div><div class="card"><div class="card-head"><h3>Tokens</h3></div><div id="aiB"></div></div></div>
            <div class="grid cols-2" style="margin-top:16px"><div class="card"><div class="card-head"><h3>Estimated cost per day</h3></div><div id="aiC"></div></div>
                <div class="card"><div class="card-head"><h3>By feature</h3></div>${d.by_feature.length ? table([{ h: "Feature", f: r => esc(r.feature.replace(/_/g, " ")) }, { h: "Requests", n: 1, f: r => num(r.requests) }, { h: "Tokens in/out", n: 1, f: r => `${num(r.input_tokens)} / ${num(r.output_tokens)}` }, { h: "Errors", n: 1, f: r => num(r.errors) }, { h: "Cost", n: 1, f: r => "$" + r.cost.toFixed(4) }], d.by_feature) : empty("No AI calls in this range yet.", "🤖")}</div></div>
            ${d.recent_errors.length ? `<div class="card" style="margin-top:16px"><div class="card-head"><h3>Recent failures</h3></div>${table([{ h: "When", f: r => dt(r.at) }, { h: "Feature", f: r => esc(r.feature) }, { h: "Error", f: r => esc(r.error) }], d.recent_errors)}</div>` : ""}`;
        lineChart($("aiA"), d.series, [{ key: "requests", label: "Requests", color: "var(--c1)" }, { key: "errors", label: "Errors", color: "var(--c5)" }], { height: 200 });
        lineChart($("aiB"), d.series, [{ key: "input_tokens", label: "Input", color: "var(--c2)" }, { key: "output_tokens", label: "Output", color: "var(--c4)" }], { height: 200 });
        lineChart($("aiC"), d.series, [{ key: "cost", label: "Cost (USD)", color: "var(--c3)" }], { height: 200, fmt: v => "$" + Number(v).toFixed(4) });
    });
}

/* ---- Billing */
async function vBilling(el, t) {
    let tab = "plans";
    el.innerHTML = `<div class="page-head"><div><p>Plans, subscriptions and a payment ledger. Entries here are recorded by admins; connect a payment processor to automate them.</p></div></div>
        <div class="tabs" id="bTabs"><button data-t="plans" class="on">Plans</button><button data-t="subs">Subscriptions</button><button data-t="pay">Payments</button></div><div id="bBody"></div>`;
    const st = { page: 1, status: "" };
    const run = async () => {
        const body = $("bBody"); body.innerHTML = skeleton();
        try {
            if (tab === "plans") {
                const d = await api("/plans"); if (!alive(t)) return;
                body.innerHTML = `<div class="card"><div class="card-head"><h3>Pricing plans</h3>${can("admin") ? '<button class="btn primary small" data-newplan>+ New plan</button>' : ""}</div>${d.plans.length ? table([
                    { h: "Plan", f: p => `<strong>${esc(p.name)}</strong><span class="sub">${esc(p.features)}</span>` }, { h: "Price", f: p => `${money(p.price_cents, p.currency)} / ${p.interval}` },
                    { h: "Email limit", f: p => p.email_limit == null ? "Unlimited" : num(p.email_limit) }, { h: "Active subs", n: 1, f: p => num(p.subscribers) }, { h: "Status", f: p => pill(p.is_active ? "active" : "canceled", p.is_active ? "active" : "inactive") },
                    { h: "", f: p => can("admin") ? `<button class="btn small" data-edit="${p.id}">Edit</button>` : "" }], d.plans) : empty("No plans yet. Create your first plan.", "🏷️")}</div>`;
                body.onclick = (e) => { if (e.target.closest("[data-newplan]")) planModal(null, run); const b = e.target.closest("[data-edit]"); b && planModal(d.plans.find(p => p.id === +b.dataset.edit), run); };
            } else if (tab === "subs") {
                const d = await api("/subscriptions?" + qs({ ...st, page_size: 20 })); if (!alive(t)) return;
                body.innerHTML = `<div class="card"><div class="toolbar"><select id="sbStatus"><option value="">All</option><option>active</option><option>canceled</option><option>past_due</option></select><span class="grow"></span>${can("admin") ? '<button class="btn primary small" data-newsub>+ Assign plan</button>' : ""}</div>${d.subscriptions.length ? table([
                    { h: "User", f: s => esc(s.user) }, { h: "Plan", f: s => `${esc(s.plan)} <span class="sub">${money(s.price_cents, s.currency)} / ${s.interval}</span>` }, { h: "Status", f: s => pill(s.status) },
                    { h: "Started", f: s => dt(s.started_at) }, { h: "Renews / ends", f: s => dt(s.current_period_end) }, { h: "Canceled", f: s => s.canceled_at ? `${dt(s.canceled_at)}<span class="sub">${esc(s.cancel_reason || "")}</span>` : "—" },
                    { h: "", f: s => s.status === "active" && can("admin") ? `<button class="btn small danger" data-cancel="${s.id}">Cancel</button>` : "" }], d.subscriptions) + pager(d) : empty("No subscriptions yet.", "🧾")}</div>`;
                $("sbStatus").value = st.status; $("sbStatus").onchange = (e) => { st.status = e.target.value; st.page = 1; run(); };
                body.onclick = async (e) => {
                    const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; return run(); }
                    if (e.target.closest("[data-newsub]")) return subModal(run);
                    const c = e.target.closest("[data-cancel]"); if (!c) return;
                    const reason = await promptBox("Cancel subscription", "The subscription ends immediately.", "Reason (optional)"); if (reason === null) return;
                    (await act(c, () => api(`/subscriptions/${c.dataset.cancel}/cancel`, { method: "POST", body: { reason } }), "Subscription canceled")) && run();
                };
            } else {
                const d = await api("/payments?" + qs({ ...st, page_size: 20 }) + "&" + rangeQS()); if (!alive(t)) return;
                body.innerHTML = `<div class="grid cards">${kpi("Revenue in range", moneyMap(d.revenue), "succeeded payments")}</div><div class="card"><div class="toolbar"><select id="pyStatus"><option value="">All</option><option>succeeded</option><option>failed</option><option>refunded</option></select><span class="grow"></span>${can("admin") ? '<button class="btn primary small" data-newpay>+ Record payment</button>' : ""}</div>${d.payments.length ? table([
                    { h: "Date", f: p => dt(p.created_at) }, { h: "User", f: p => esc(p.user) }, { h: "Amount", n: 1, f: p => money(p.amount_cents, p.currency) }, { h: "Status", f: p => pill(p.status) },
                    { h: "Method", f: p => esc(p.method || "") }, { h: "Reference", f: p => esc(p.reference || "") },
                    { h: "", f: p => p.status === "succeeded" && can("admin") ? `<button class="btn small danger" data-refund="${p.id}">Refund</button>` : "" }], d.payments) + pager(d) : empty("No payments in this range.", "💳")}</div>`;
                $("pyStatus").value = st.status; $("pyStatus").onchange = (e) => { st.status = e.target.value; st.page = 1; run(); };
                body.onclick = async (e) => {
                    const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; return run(); }
                    if (e.target.closest("[data-newpay]")) return payModal(run);
                    const r = e.target.closest("[data-refund]"); if (r && await confirmBox("Mark as refunded?", "This removes the payment from revenue.", "Refund"))
                        (await act(r, () => api(`/payments/${r.dataset.refund}/refund`, { method: "POST" }), "Refunded")) && run();
                };
            }
        } catch (e) { body.innerHTML = errState(e.message); body.querySelector("[data-retry]").onclick = run; }
    };
    $("bTabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; tab = b.dataset.t; st.page = 1; st.status = ""; document.querySelectorAll("#bTabs button").forEach(x => x.classList.toggle("on", x === b)); run(); };
    run();
}
function planModal(p, done) {
    modal(`<h3>${p ? "Edit plan" : "New plan"}</h3><form id="pf"><label>Name</label><input name="name" required maxlength="80" value="${esc(p?.name || "")}">
        <div class="row2"><div><label>Price (in minor units, e.g. cents)</label><input name="price_cents" type="number" min="0" required value="${p?.price_cents ?? 0}"></div><div><label>Currency</label><input name="currency" maxlength="3" required value="${esc(p?.currency || "USD")}"></div></div>
        <div class="row2"><div><label>Interval</label><select name="interval"><option value="month">Monthly</option><option value="year">Yearly</option></select></div><div><label>Email limit (blank = unlimited)</label><input name="email_limit" type="number" min="0" value="${p?.email_limit ?? ""}"></div></div>
        <label>Features</label><textarea name="features" rows="3" maxlength="1000">${esc(p?.features || "")}</textarea>
        <label><input type="checkbox" name="is_active" style="width:auto" ${p?.is_active === false ? "" : "checked"}> Active (available to assign)</label>
        <p class="form-error" id="pfErr" hidden></p><div class="modal-actions"><button type="button" class="btn" data-x>Cancel</button><button class="btn primary" type="submit">Save</button></div></form>`);
    const f = $("pf"); f.interval.value = p?.interval || "month";
    $("modalCard").onclick = (e) => e.target.closest("[data-x]") && closeModal();
    f.onsubmit = async (e) => {
        e.preventDefault();
        const body = { name: f.name.value, price_cents: +f.price_cents.value, currency: f.currency.value, interval: f.interval.value, email_limit: f.email_limit.value === "" ? null : +f.email_limit.value, features: f.features.value, is_active: f.is_active.checked };
        try { await api(p ? `/plans/${p.id}` : "/plans", { method: p ? "PUT" : "POST", body }); closeModal(); toast("Plan saved", "ok"); done(); }
        catch (err) { $("pfErr").textContent = err.message; $("pfErr").hidden = false; }
    };
}
async function userOptions() { const d = await api("/users?page_size=100&sort=email&direction=asc"); return d.users.map(u => `<option value="${u.id}">${esc(u.name ? u.name + " — " : "")}${esc(u.email)}</option>`).join(""); }
async function subModal(done) {
    const [uo, pl] = await Promise.all([userOptions(), api("/plans")]);
    const po = pl.plans.filter(p => p.is_active).map(p => `<option value="${p.id}">${esc(p.name)} — ${money(p.price_cents, p.currency)}/${p.interval}</option>`).join("");
    if (!po) return toast("Create an active plan first.", "err");
    modal(`<h3>Assign plan</h3><form id="sf"><label>User</label><select name="u">${uo}</select><label>Plan</label><select name="p">${po}</select><p class="form-error" id="sfErr" hidden></p>
        <div class="modal-actions"><button type="button" class="btn" data-x>Cancel</button><button class="btn primary">Assign</button></div></form>`);
    $("modalCard").onclick = (e) => e.target.closest("[data-x]") && closeModal();
    $("sf").onsubmit = async (e) => { e.preventDefault(); const f = e.target;
        try { await api("/subscriptions", { method: "POST", body: { user_id: +f.u.value, plan_id: +f.p.value } }); closeModal(); toast("Subscription created", "ok"); done(); } catch (err) { $("sfErr").textContent = err.message; $("sfErr").hidden = false; } };
}
async function payModal(done) {
    const uo = await userOptions();
    modal(`<h3>Record payment</h3><form id="yf"><label>User</label><select name="u">${uo}</select><div class="row2"><div><label>Amount (minor units)</label><input name="a" type="number" min="1" required></div><div><label>Currency</label><input name="c" value="USD" maxlength="3"></div></div>
        <div class="row2"><div><label>Status</label><select name="s"><option>succeeded</option><option>failed</option></select></div><div><label>Method</label><input name="m" value="manual" maxlength="30"></div></div>
        <label>Reference</label><input name="r" maxlength="120"><label>Note</label><input name="n" maxlength="300"><p class="form-error" id="yfErr" hidden></p>
        <div class="modal-actions"><button type="button" class="btn" data-x>Cancel</button><button class="btn primary">Record</button></div></form>`);
    $("modalCard").onclick = (e) => e.target.closest("[data-x]") && closeModal();
    $("yf").onsubmit = async (e) => { e.preventDefault(); const f = e.target;
        try { await api("/payments", { method: "POST", body: { user_id: +f.u.value, amount_cents: +f.a.value, currency: f.c.value, status: f.s.value, method: f.m.value, reference: f.r.value, note: f.n.value } }); closeModal(); toast("Payment recorded", "ok"); done(); } catch (err) { $("yfErr").textContent = err.message; $("yfErr").hidden = false; } };
}

/* ---- System */
const SVC = { fastapi: ["FastAPI", "API server"], database: ["Database", "Primary datastore"], scheduler: ["Background scheduler", "APScheduler jobs"], celery: ["Celery", "Task queue"], gmail_api: ["Gmail API", "Google"], gemini_api: ["Gemini API", "Google AI"] };
async function vSystem(el, t) {
    let timer;
    const run = async (first) => {
        if (first) el.innerHTML = skeleton();
        try {
            const d = await api("/system"); if (!alive(t)) return clearInterval(timer);
            const cards = Object.entries(d.services).map(([k, s]) => {
                const meta = [];
                if (s.latency_ms != null) meta.push(`${s.latency_ms} ms`);
                if (k === "fastapi") meta.push(`up ${fmtUp(s.uptime_seconds)}`, `Python ${s.python}`);
                if (k === "database") { meta.push(s.engine); if (s.size_bytes != null) meta.push((s.size_bytes / 1048576).toFixed(1) + " MB"); }
                if (k === "scheduler") { meta.push(`${s.jobs} jobs`); if (s.next_run) meta.push("next " + dt(s.next_run)); }
                if (k === "gemini_api") { meta.push(`${s.requests_24h} calls / 24h`, `${s.errors_24h} errors`); if (s.last_call_at) meta.push("last " + dt(s.last_call_at)); }
                return `<div class="card svc"><h3><span class="led ${esc(s.status)}"></span>${esc(SVC[k][0])} ${pill(s.status)}</h3><p>${esc(s.error || s.detail || SVC[k][1])}</p><div class="meta">${meta.map(m => `<span>${esc(m)}</span>`).join("")}</div>
                    ${k === "gemini_api" && can("admin") ? '<div><button class="btn small" data-gem>Run live check</button></div>' : ""}</div>`;
            }).join("");
            el.innerHTML = `<div class="page-head"><div><p>Live checks · refreshed ${dt(d.checked_at)} · auto-refreshes every 30s</p></div><button class="btn" data-refresh>↻ Refresh</button></div><div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">${cards}</div>`;
        } catch (e) { if (alive(t)) { el.innerHTML = errState(e.message); el.querySelector("[data-retry]").onclick = () => run(true); } }
    };
    el.onclick = async (e) => {
        if (e.target.closest("[data-refresh]")) return run(true);
        const g = e.target.closest("[data-gem]"); if (!g) return;
        g.disabled = true; g.textContent = "Checking…";
        try { const r = await api("/system/gemini-check", { method: "POST" }); toast(`Gemini OK · ${r.result.latency_ms} ms`, "ok"); } catch (err) { toast("Gemini check failed: " + err.message, "err"); }
        run(true);
    };
    timer = setInterval(() => { if (!alive(t)) clearInterval(timer); else if (!document.hidden) run(false); }, 30000);
    run(true);
}
const fmtUp = (s) => s < 3600 ? Math.round(s / 60) + "m" : s < 86400 ? (s / 3600).toFixed(1) + "h" : (s / 86400).toFixed(1) + "d";

/* ---- Logs */
async function vLogs(el, t) {
    let tab = "errors"; const st = { page: 1, level: "", search: "", action: "", admin_email: "" };
    el.innerHTML = `<div class="tabs" id="lTabs"><button data-t="errors" class="on">Error logs</button><button data-t="audit">Audit logs</button></div><div id="lBody"></div>`;
    const run = async () => {
        const body = $("lBody"); body.innerHTML = skeleton();
        try {
            if (tab === "errors") {
                const d = await api("/logs/errors?" + qs({ page: st.page, level: st.level, search: st.search, page_size: 20 }) + "&" + rangeQS()); if (!alive(t)) return;
                body.innerHTML = `<div class="grid cards">${["CRITICAL", "ERROR", "WARNING"].map(l => kpi(l[0] + l.slice(1).toLowerCase(), num(d.by_level[l] || 0), "in range", l !== "WARNING" && d.by_level[l] ? "bad" : "")).join("")}</div>
                    <div class="card"><div class="toolbar"><input class="grow" id="lSearch" placeholder="Search message, source or path…" value="${esc(st.search)}"><select id="lLevel"><option value="">All levels</option><option>CRITICAL</option><option>ERROR</option><option>WARNING</option></select></div>
                    ${d.items.length ? table([{ h: "When", f: r => dt(r.at) }, { h: "Level", f: r => pill(r.level) }, { h: "Source", f: r => esc(r.source || "") },
                        { h: "Message", f: r => `<span class="ellipsis" title="${esc(r.message)}">${esc(r.message)}</span>` }, { h: "Path", f: r => esc(r.path || "") }, { h: "HTTP", n: 1, f: r => r.status_code || "" }], d.items, r => `class="click" data-eid="${r.id}" tabindex="0"`) + pager(d) : empty("No errors in this range. 🎉", "✅")}</div>`;
                $("lLevel").value = st.level; $("lLevel").onchange = (e) => { st.level = e.target.value; st.page = 1; run(); };
                let tm; $("lSearch").oninput = (e) => { clearTimeout(tm); tm = setTimeout(() => { st.search = e.target.value; st.page = 1; run().then(() => { const i = $("lSearch"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 350); };
                body.onclick = async (e) => {
                    const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; return run(); }
                    const r = e.target.closest("[data-eid]"); if (!r) return;
                    try { const x = (await api("/logs/errors/" + r.dataset.eid)).item;
                        modal(`<h3>${pill(x.level)} ${esc(x.source || "")}</h3><dl class="kv" style="margin:12px 0"><dt>When</dt><dd>${dt(x.at)}</dd><dt>Path</dt><dd>${esc(x.path || "—")}</dd><dt>Status</dt><dd>${x.status_code || "—"}</dd><dt>Exception</dt><dd>${esc(x.exc_type || "—")}</dd></dl><p>${esc(x.message)}</p>${x.trace ? `<pre class="trace">${esc(x.trace)}</pre>` : ""}<p class="muted" style="font-size:12px">Emails and tokens are redacted before storage.</p><div class="modal-actions"><button class="btn primary" data-x>Close</button></div>`, true);
                        $("modalCard").onclick = (ev) => ev.target.closest("[data-x]") && closeModal(); } catch (err) { toast(err.message, "err"); }
                };
            } else {
                const d = await api("/logs/audit?" + qs({ page: st.page, action: st.action, admin_email: st.admin_email, page_size: 25 }) + "&" + rangeQS()); if (!alive(t)) return;
                body.innerHTML = `<div class="card"><div class="toolbar"><select id="aAct"><option value="">All actions</option>${d.actions.map(a => `<option ${a === st.action ? "selected" : ""}>${esc(a)}</option>`).join("")}</select><input id="aAdm" placeholder="Filter by admin email" value="${esc(st.admin_email)}"></div>
                    ${d.items.length ? table([{ h: "When", f: r => dt(r.at) }, { h: "Admin", f: r => esc(r.admin || "—") }, { h: "Action", f: r => `<span class="pill">${esc(r.action)}</span>` }, { h: "Target", f: r => r.target_type ? `${esc(r.target_type)} #${esc(r.target_id)}` : "" }, { h: "Detail", f: r => `<span class="ellipsis" title="${esc(r.detail || "")}">${esc(r.detail || "")}</span>` }, { h: "IP", f: r => esc(r.ip || "") }], d.items) + pager(d) : empty("No audit entries in this range.", "🗒️")}</div>`;
                $("aAct").onchange = (e) => { st.action = e.target.value; st.page = 1; run(); };
                let tm; $("aAdm").oninput = (e) => { clearTimeout(tm); tm = setTimeout(() => { st.admin_email = e.target.value; st.page = 1; run(); }, 350); };
                body.onclick = (e) => { const p = e.target.closest("[data-page]"); if (p) { st.page = +p.dataset.page; run(); } };
            }
        } catch (e) { body.innerHTML = errState(e.message); body.querySelector("[data-retry]").onclick = run; }
    };
    $("lTabs").onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; tab = b.dataset.t; st.page = 1; document.querySelectorAll("#lTabs button").forEach(x => x.classList.toggle("on", x === b)); run(); };
    run();
}

/* ---- Notifications */
async function vNotifications(el, t) {
    let page = 1, unreadOnly = false;
    const run = async () => {
        await load(el, t, async () => {
            const d = await api("/notifications?" + qs({ page, page_size: 20, unread: unreadOnly ? "true" : "" })); if (!alive(t)) return;
            el.innerHTML = `<div class="page-head"><div><p>${num(d.unread)} unread · generated from real events (failures, Gmail disconnects, errors, new users)</p></div><div style="display:flex;gap:8px"><button class="btn" data-toggle>${unreadOnly ? "Show all" : "Unread only"}</button><button class="btn" data-all ${d.unread ? "" : "disabled"}>Mark all read</button></div></div>
                <div class="card" style="padding:8px">${d.items.length ? d.items.map(n => `<div class="notif ${n.is_read ? "" : "unread"}"><strong>${pill(n.severity)} ${esc(n.title)}</strong>${n.body ? `<span>${esc(n.body)}</span>` : ""}<small>${dt(n.at)}${n.is_read ? "" : ` · <a href="#" data-read="${n.id}">mark read</a>`}</small></div>`).join("") : empty("No notifications.", "🔔")}${pager(d)}</div>`;
            el.onclick = async (e) => {
                if (e.target.closest("[data-toggle]")) { unreadOnly = !unreadOnly; page = 1; return run(); }
                if (e.target.closest("[data-all]")) { await api("/notifications/read-all", { method: "POST" }); refreshBell(); return run(); }
                const r = e.target.closest("[data-read]"); if (r) { e.preventDefault(); await api(`/notifications/${r.dataset.read}/read`, { method: "POST" }); refreshBell(); return run(); }
                const p = e.target.closest("[data-page]"); if (p) { page = +p.dataset.page; run(); }
            };
        });
    };
    run();
}

/* ---- Admins */
async function vAdmins(el, t) {
    const run = () => load(el, t, async () => {
        const d = await api("/admins"); if (!alive(t)) return;
        el.innerHTML = `<div class="page-head"><div><p>Roles: <b>Super admin</b> manages everything · <b>Admin</b> operates users and billing · <b>Support</b> is read-only.</p></div><button class="btn primary" data-new>+ New admin</button></div>
            <div class="card">${table([{ h: "Admin", f: a => `<strong>${esc(a.name || a.email)}</strong><span class="sub">${esc(a.email)}</span>` }, { h: "Role", f: a => pill(a.role === "super_admin" ? "info" : a.role === "admin" ? "ok" : "unknown", a.role.replace("_", " ")) },
                { h: "Status", f: a => pill(a.is_active ? "active" : "suspended", a.is_active ? "active" : "disabled") }, { h: "Last login", f: a => dt(a.last_login_at) }, { h: "Created", f: a => dt(a.created_at) },
                { h: "", f: a => `<button class="btn small" data-edit="${a.id}">Edit</button>` }], d.admins)}</div>`;
        el.onclick = (e) => { if (e.target.closest("[data-new]")) return adminModal(null, run); const b = e.target.closest("[data-edit]"); b && adminModal(d.admins.find(a => a.id === +b.dataset.edit), run); };
    });
    run();
}
function adminModal(a, done) {
    modal(`<h3>${a ? "Edit admin" : "New admin"}</h3><form id="af">${a ? `<p class="muted">${esc(a.email)}</p>` : `<label>Email</label><input name="email" type="email" required>`}
        <label>Name</label><input name="name" maxlength="120" value="${esc(a?.name || "")}"><label>Role</label><select name="role"><option value="support">Support (read-only)</option><option value="admin">Admin</option><option value="super_admin">Super admin</option></select>
        ${a ? `<label><input type="checkbox" name="active" style="width:auto" ${a.is_active ? "checked" : ""}> Active</label>` : ""}
        <label>${a ? "New password (leave blank to keep)" : "Password"}</label><input name="pw" type="password" autocomplete="new-password" ${a ? "" : "required"} minlength="${a ? 0 : 8}"><small>At least 8 characters.${a ? " Resetting signs the admin out everywhere." : ""}</small>
        <p class="form-error" id="afErr" hidden></p><div class="modal-actions"><button type="button" class="btn" data-x>Cancel</button><button class="btn primary">Save</button></div></form>`);
    const f = $("af"); f.role.value = a?.role || "support";
    $("modalCard").onclick = (e) => e.target.closest("[data-x]") && closeModal();
    f.onsubmit = async (e) => {
        e.preventDefault();
        try {
            if (a) { const body = { name: f.name.value, role: f.role.value, is_active: f.active.checked }; if (f.pw.value) body.password = f.pw.value; await api("/admins/" + a.id, { method: "PATCH", body }); }
            else await api("/admins", { method: "POST", body: { email: f.email.value, name: f.name.value, role: f.role.value, password: f.pw.value } });
            closeModal(); toast("Saved", "ok"); done();
        } catch (err) { $("afErr").textContent = err.message; $("afErr").hidden = false; }
    };
}

/* ---- Settings */
async function vSettings(el, t) {
    const run = () => load(el, t, async () => {
        const d = await api("/settings/gemini-key"); if (!alive(t)) return;
        el.innerHTML = `<div class="page-head"><div><p>Platform secrets. Only super admins can see this page. Keys are never displayed in full.</p></div></div>
            <div class="card card-pad" style="max-width:720px"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap"><h3>Gemini API key</h3>${d.configured ? pill("connected", "configured") : pill("down", "not set")}</div>
                <dl class="kv" style="margin:14px 0">
                    <dt>Current key</dt><dd>${d.configured ? `<code>${esc(d.masked)}</code>` : "None — AI features are disabled until you add one."}</dd>
                    <dt>Stored in</dt><dd><code>${esc(d.env_file)}</code> as <code>GOOGLE_API_KEY</code> ${d.env_writable ? "" : '<span class="pill failed">not writable</span>'}</dd>
                    <dt>Last changed</dt><dd>${d.updated_at ? `${dt(d.updated_at)} by ${esc(d.updated_by || "—")}` : "Not changed from this panel yet"}</dd></dl>
                <form id="kf" autocomplete="off"><label for="kfKey">New key</label>
                    <input id="kfKey" type="password" autocomplete="new-password" spellcheck="false" placeholder="AIza…" required>
                    <small>The key is tested with one tiny live Gemini request, written to <b>${esc(d.env_file)}</b> (other lines are kept), and used immediately — no restart. It is never logged or shown again.</small>
                    <p class="form-error" id="kfErr" hidden></p>
                    <div class="modal-actions" style="justify-content:flex-start"><button class="btn primary" id="kfBtn" type="submit">Test &amp; save key</button></div></form></div>`;
        $("kf").onsubmit = async (e) => {
            e.preventDefault(); const err = $("kfErr"), btn = $("kfBtn"); err.hidden = true;
            if (!await confirmBox("Replace the Gemini API key?", "The new key will be tested, saved to .env and used for all AI features right away.", "Test & save", false)) return;
            btn.disabled = true; btn.textContent = "Testing key…";
            try { await api("/settings/gemini-key", { method: "PUT", body: { key: $("kfKey").value.trim() } }); toast("Gemini key saved and active", "ok"); run(); }
            catch (ex) { err.textContent = ex.message; err.hidden = false; btn.disabled = false; btn.textContent = "Test & save key"; }
        };
    });
    run();
}

/* ------------------------------------------------------------------ shell */
function buildNav() {
    let html = "", group = "";
    VIEWS.filter(v => !v.role || can(v.role)).forEach(v => {
        if (v.group !== group) { group = v.group; html += `<div class="nav-group">${esc(group)}</div>`; }
        html += `<button class="nav-item ${S.view === v.id ? "on" : ""}" data-v="${v.id}"><svg viewBox="0 0 24 24" aria-hidden="true">${ICON[v.id]}</svg>${esc(v.title)}${v.id === "notifications" ? '<span class="dot" id="navDot" hidden></span>' : ""}</button>`;
    });
    $("nav").innerHTML = html;
}
function go(id, push = true) {
    const v = VIEWS.find(x => x.id === id && (!x.role || can(x.role))) || VIEWS[0];
    S.view = v.id; S.token++; closeMenu();
    document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("on", b.dataset.v === v.id));
    $("pageTitle").textContent = v.title; document.title = `${v.title} · AutoMail Admin`;
    $("rangeBar").hidden = !v.ranged; if (!v.ranged) $("customRange").hidden = true; else $("customRange").hidden = S.range !== "custom";
    if (push) history.replaceState(null, "", "#" + v.id);
    const el = $("view"); el.onclick = null; el.onkeydown = null; v.render(el, S.token); window.scrollTo(0, 0);
}
const closeMenu = () => { $("side").classList.remove("open"); $("scrim").classList.remove("show"); };

async function refreshBell() {
    try {
        const d = await api("/notifications?unread=true&page_size=6"); const n = d.unread;
        $("bellBadge").hidden = !n; $("bellBadge").textContent = n > 99 ? "99+" : n;
        const dot = $("navDot"); if (dot) { dot.hidden = !n; dot.textContent = n; }
        $("bellPop").innerHTML = (d.items.length ? d.items.map(x => `<div class="notif unread"><strong>${pill(x.severity)} ${esc(x.title)}</strong>${x.body ? `<span>${esc(x.body)}</span>` : ""}<small>${dt(x.at)}</small></div>`).join("") : empty("You're all caught up.", "✅")) + `<div style="padding:6px"><button class="btn small block" data-allnotifs>View all notifications</button></div>`;
    } catch (e) { /* bell is best-effort */ }
}

function showLogin() { $("app").hidden = true; $("login").hidden = false; $("lgPass").value = ""; $("lgEmail").focus(); }
function showApp() {
    $("login").hidden = true; $("app").hidden = false;
    $("meName").textContent = S.me.name || S.me.email; $("meRole").textContent = S.me.role.replace("_", " ");
    $("meAvatar").textContent = (S.me.name || S.me.email)[0].toUpperCase();
    buildNav(); refreshBell();
    go((location.hash || "").slice(1) || "overview", false);
}

function initEvents() {
    $("loginForm").onsubmit = async (e) => {
        e.preventDefault(); const err = $("lgError"); err.hidden = true; $("lgBtn").disabled = true;
        try { const r = await fetch("/api/admin/auth/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: $("lgEmail").value, password: $("lgPass").value }) });
            const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || "Sign-in failed.");
            S.me = d.admin; showApp();
        } catch (ex) { err.textContent = ex.message; err.hidden = false; } finally { $("lgBtn").disabled = false; }
    };
    $("logoutBtn").onclick = async () => { try { await api("/auth/logout", { method: "POST" }); } catch (e) {} S.me = null; showLogin(); };
    $("nav").onclick = (e) => { const b = e.target.closest("[data-v]"); b && go(b.dataset.v); };
    $("menuBtn").onclick = () => { $("side").classList.add("open"); $("scrim").classList.add("show"); };
    $("scrim").onclick = closeMenu;
    $("rangeBar").onclick = (e) => {
        const b = e.target.closest("[data-r]"); if (!b) return; S.range = b.dataset.r;
        document.querySelectorAll("#rangeBar button").forEach(x => x.classList.toggle("on", x === b));
        $("customRange").hidden = S.range !== "custom";
        if (S.range === "custom") { const iso = (d) => new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); if (!$("rgStart").value) { $("rgStart").value = iso(new Date(Date.now() - 29 * 864e5)); $("rgEnd").value = iso(new Date()); } return; }
        go(S.view, false);
    };
    $("rgApply").onclick = () => { S.start = $("rgStart").value; S.end = $("rgEnd").value; if (!S.start || !S.end) return toast("Pick both dates", "err"); go(S.view, false); };
    $("bellBtn").onclick = (e) => { e.stopPropagation(); $("bellPop").hidden = !$("bellPop").hidden; if (!$("bellPop").hidden) refreshBell(); };
    $("bellPop").onclick = (e) => { if (e.target.closest("[data-allnotifs]")) { $("bellPop").hidden = true; go("notifications"); } };
    document.addEventListener("click", (e) => { if (!e.target.closest(".bell-wrap")) $("bellPop").hidden = true; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closeMenu(); $("bellPop").hidden = true; } });
    $("modal").onclick = (e) => { if (e.target === $("modal")) closeModal(); };
    window.addEventListener("hashchange", () => { if (S.me) go((location.hash || "").slice(1) || "overview", false); });
    setInterval(() => { if (S.me && !document.hidden) refreshBell(); }, 60000);
}

(async function boot() {
    initEvents();
    try { S.me = (await api("/auth/me")).admin; showApp(); } catch (e) { showLogin(); }
})();
})();
