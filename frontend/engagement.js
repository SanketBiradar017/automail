/* Email Engagement Analytics: cards, funnel, rates, trend chart, breakdown,
   drill-down table. All numbers come from /api/analytics/engagement*. */

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => escapeHtml(String(v ?? ""));
    const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const S = { range: "30d", gran: "daily", metric: null, page: 1, data: null, loading: false, stamp: null, hidden: new Set() };
    const SERIES = [["sent", "Sent", "--eg-sent"], ["delivered", "Delivered", "--eg-delivered"], ["opened", "Opened", "--eg-opened"], ["replied", "Replied", "--eg-replied"]];
    const CARDS = [
        ["sent", "Total sent", "single"], ["delivered", "Delivered", "double"], ["opened", "Opened", "double"],
        ["replied", "Replied", "double"], ["bounced", "Bounced", "x"], ["failed", "Failed", "x"],
    ];
    const BREAK = { sent: "Sent", delivered: "Delivered", opened: "Opened", replied: "Replied", bounced: "Bounced", failed: "Failed" };
    const BREAK_TIP = {
        sent: "Single grey tick: Gmail accepted it, delivery not confirmed.", delivered: "Double grey tick: delivery confirmed.",
        opened: "Blue double tick: opened (read receipt or opt-in pixel).", replied: "Green double tick: the recipient replied.",
        bounced: "Delivery-failure notice received.", failed: "Gmail rejected the send.",
    };

    const fmtPct = (v) => (v === null || v === undefined ? "—" : `${v}%`);
    const dt = (iso) => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
    const dayLabel = (iso) => new Date(iso + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric" });

    function qs(extra = {}) {
        const p = new URLSearchParams({ range: S.range, tz: TZ, ...extra });
        if (S.range === "custom") { p.set("start", $("egStart").value); p.set("end", $("egEnd").value); }
        return p;
    }

    async function getJson(url) {
        const r = await fetch(url);
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(extractErrorMessage(d, `Request failed (${r.status}).`));
        return d;
    }

    /* ---------- load ---------- */

    async function load() {
        if (S.loading) return;
        if (S.range === "custom" && (!$("egStart").value || !$("egEnd").value)) return;
        S.loading = true;
        try {
            const d = await getJson(`/api/analytics/engagement?${qs()}`);
            S.data = d; S.stamp = d.as_of;
            renderAll(d);
            $("egNote").textContent = `· ${dayLabel(d.range.start)}${d.range.start !== d.range.end ? " – " + dayLabel(d.range.end) : ""} · ${d.range.tz}`;
            if (S.metric) await loadTable(false); else renderTable(d.recent, d.recent.length, false);
        } catch (e) {
            $("egNote").textContent = "";
            toast(e.message, "error");
        } finally { S.loading = false; }
    }

    async function loadTable(append) {
        const p = qs({ metric: S.metric, page: S.page, limit: 25 });
        const d = await getJson(`/api/analytics/engagement/emails?${p}`);
        renderTable(d.emails, d.total, append);
    }

    /* ---------- render ---------- */

    function renderAll(d) {
        const c = d.counts;
        $("egCards").innerHTML = CARDS.map(([k, label, tick]) => `
            <button type="button" class="eg-card ${S.metric === k ? "active" : ""} ${k === "bounced" || k === "failed" ? "bad-card" : ""}" data-metric="${k}"
                    title="Show these emails">
                <span class="kpi-label">${Ticks.html({ state: k, tick: tick, label: label, tip: BREAK_TIP[k] }).replace(/ title="[^"]*"/, "").replace(/<em>.*?<\/em>/, "")}${label}</span>
                <strong class="kpi-value">${c[k]}</strong>
            </button>`).join("");

        const max = Math.max(1, c.sent);
        $("egFunnel").innerHTML = d.funnel.map((f, i) => {
            const prev = i ? d.funnel[i - 1].count : null;
            const of = c.sent ? Math.round(100 * f.count / c.sent) : null;
            const step = prev && i > 1 ? `${Math.round(100 * f.count / prev)}% of ${d.funnel[i - 1].label.toLowerCase()}` : "";
            return `<button type="button" class="eg-fun" data-metric="${f.key}" title="Show these emails">
                <span class="eg-fun-label">${f.label}</span>
                <span class="eg-fun-track"><span class="eg-fun-fill f-${f.key}" style="width:${(f.count / max) * 100}%"></span></span>
                <span class="eg-fun-num"><b>${f.count}</b><small>${of === null ? "—" : of + "% of sent"}${step ? " · " + step : ""}</small></span>
            </button>`;
        }).join("");

        const r = d.rates;
        $("egRates").innerHTML = `
            <div><span>Delivery rate</span><b>${fmtPct(r.delivery_rate)}</b><small>confirmed delivered ÷ sent</small></div>
            <div><span>Open rate</span><b>${fmtPct(r.open_rate)}</b><small>${r.open_rate_base ? `of ${r.open_rate_base} with tracking on` : "no emails with tracking on"}</small></div>
            <div><span>Reply rate</span><b>${fmtPct(r.reply_rate)}</b><small>${r.reply_rate_base ? `of ${r.reply_rate_base} not bounced` : "no emails sent"}</small></div>`;

        const total = Object.values(d.breakdown).reduce((a, b) => a + b, 0);
        const tickFor = { sent: "sent", delivered: "delivered", opened: "opened", replied: "replied", bounced: "bounced", failed: "failed" };
        const tk = { sent: "single", delivered: "double", opened: "double", replied: "double", bounced: "x", failed: "x" };
        $("egBreakdown").innerHTML = Object.entries(d.breakdown).map(([k, n]) => `
            <button type="button" class="eg-brk ${S.metric === k ? "active" : ""}" data-metric="${k}" title="${esc(BREAK_TIP[k])}">
                ${Ticks.html({ state: tickFor[k], tick: tk[k], label: "", tip: BREAK_TIP[k] }, {})}
                <span class="eg-brk-name">${BREAK[k]}</span>
                <span class="eg-brk-bar"><i class="f-${k}" style="width:${total ? (100 * n / total) : 0}%"></i></span>
                <b>${n}</b><small>${total ? Math.round(100 * n / total) : 0}%</small>
            </button>`).join("");

        renderChart();
        $("egLegend").innerHTML = SERIES.map(([k, label, v]) =>
            `<button type="button" class="eg-leg ${S.hidden.has(k) ? "off" : ""}" data-s="${k}" aria-pressed="${!S.hidden.has(k)}"><i style="background:var(${v})"></i>${label}</button>`).join("");
    }

    function renderChart() {
        const d = S.data; if (!d) return;
        const rows = S.gran === "weekly" ? d.weekly : d.daily;
        const el = $("egChart");
        const vis = SERIES.filter(([k]) => !S.hidden.has(k));
        const max = Math.max(1, ...rows.flatMap(r => vis.map(([k]) => r[k])));
        if (!rows.length || !d.counts.sent && !d.counts.failed) { el.innerHTML = '<div class="an-empty">No emails in this range.</div>'; return; }

        const W = 760, H = 220, L = 34, R = 10, T = 12, B = 26;
        const iw = W - L - R, ih = H - T - B;
        const x = (i) => L + (rows.length === 1 ? iw / 2 : (i / (rows.length - 1)) * iw);
        const y = (v) => T + ih - (v / max) * ih;
        const ticks = [0, 0.5, 1].map(f => Math.round(max * f));
        const grid = [...new Set(ticks)].map(v => `<line class="eg-grid-l" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="tick-t" x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v}</text>`).join("");
        const step = Math.max(1, Math.ceil(rows.length / 7));
        const xl = rows.map((r, i) => i % step === 0 ? `<text class="tick-t" x="${x(i)}" y="${H - 8}" text-anchor="middle">${dayLabel(r.date)}</text>` : "").join("");
        const lines = vis.map(([k, , v]) => {
            const pts = rows.map((r, i) => `${x(i)},${y(r[k])}`).join(" ");
            const dots = rows.length <= 40 ? rows.map((r, i) => `<circle cx="${x(i)}" cy="${y(r[k])}" r="2.6" style="fill:var(${v})"/>`).join("") : "";
            return `<polyline class="eg-line" points="${pts}" style="stroke:var(${v})"/>${dots}`;
        }).join("");
        el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Engagement ${S.gran} trend">${grid}${xl}${lines}
            <line class="eg-cross" id="egCross" y1="${T}" y2="${T + ih}" x1="0" x2="0" style="display:none"/></svg><div class="eg-tip" id="egTip" hidden></div>`;

        const svg = el.querySelector("svg"), tip = $("egTip"), cross = $("egCross");
        svg.addEventListener("pointermove", (ev) => {
            const b = svg.getBoundingClientRect();
            const px = ((ev.clientX - b.left) / b.width) * W;
            let i = rows.length === 1 ? 0 : Math.round(((px - L) / iw) * (rows.length - 1));
            i = Math.max(0, Math.min(rows.length - 1, i));
            const r = rows[i];
            cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.style.display = "";
            tip.hidden = false;
            tip.innerHTML = `<b>${S.gran === "weekly" ? "Week of " : ""}${dayLabel(r.date)}</b>` + SERIES.map(([k, l, v]) => `<div><i style="background:var(${v})"></i>${l} <b>${r[k]}</b></div>`).join("");
            const left = (x(i) / W) * b.width;
            tip.style.left = Math.min(Math.max(left + 12, 4), b.width - 150) + "px";
        });
        svg.addEventListener("pointerleave", () => { tip.hidden = true; cross.style.display = "none"; });
    }

    function renderTable(rows, total, append) {
        const t = $("egTable");
        $("egTableTitle").textContent = S.metric ? `${S.metric === "all" ? "All" : BREAK[S.metric] || S.metric} emails (${total})` : "Recent email activity";
        $("egClear").style.display = S.metric ? "" : "none";
        $("egMore").style.display = S.metric && S.page * 25 < total ? "" : "none";
        if (!rows.length && !append) { t.innerHTML = `<tbody><tr><td><div class="an-empty">No emails.</div></td></tr></tbody>`; return; }
        const body = rows.map(e => `
            <tr>
                <td class="name" title="${esc(e.recipient)}"><span class="eg-rcpt">${Avatar.html(e.recipient, "sm", e.name)}<span>${esc(e.recipient)}</span></span></td>
                <td class="name" title="${esc(e.subject)}">${esc(e.subject)}</td>
                <td>${dt(e.sent_at)}</td>
                <td>${Ticks.html(e, { label: true })}</td>
                <td>${e.opened_at ? dt(e.opened_at) : (e.open_tracking ? '<span class="muted">not yet</span>' : '<span class="muted" title="Open tracking was not enabled for this email">not tracked</span>')}</td>
                <td>${e.replied_at ? `<span class="tick tick-replied">${dt(e.replied_at)}</span>` : "—"}</td>
            </tr>`).join("");
        if (append) t.querySelector("tbody").insertAdjacentHTML("beforeend", body);
        else t.innerHTML = `<thead><tr><th>Recipient</th><th>Subject</th><th>Sent</th><th>Status</th><th>Opened</th><th>Reply</th></tr></thead><tbody>${body}</tbody>`;
    }

    /* ---------- interactions ---------- */

    function selectMetric(k) {
        S.metric = S.metric === k ? null : k; S.page = 1;
        if (S.data) renderAll(S.data);
        S.metric ? loadTable(false).catch(e => toast(e.message, "error")) : renderTable(S.data.recent, S.data.recent.length, false);
        if (S.metric) $("egTable").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function init() {
        $("egRange").addEventListener("click", (e) => {
            const b = e.target.closest("[data-r]"); if (!b) return;
            S.range = b.dataset.r;
            $("egRange").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
            $("egCustom").style.display = S.range === "custom" ? "flex" : "none";
            if (S.range === "custom") {
                const today = new Date(), from = new Date(Date.now() - 29 * 864e5);
                const iso = (d) => new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
                if (!$("egStart").value) { $("egStart").value = iso(from); $("egEnd").value = iso(today); }
            }
            S.metric = null; S.page = 1; load();
        });
        $("egApply").addEventListener("click", () => { S.metric = null; S.page = 1; load(); });
        $("egGran").addEventListener("click", (e) => {
            const b = e.target.closest("[data-g]"); if (!b) return;
            S.gran = b.dataset.g;
            $("egGran").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
            renderChart();
        });
        $("egLegend").addEventListener("click", (e) => {
            const b = e.target.closest("[data-s]"); if (!b) return;
            S.hidden.has(b.dataset.s) ? S.hidden.delete(b.dataset.s) : S.hidden.add(b.dataset.s);
            if (S.hidden.size === SERIES.length) S.hidden.clear();
            renderAll(S.data);
        });
        $("egSection").addEventListener("click", (e) => {
            const m = e.target.closest("[data-metric]"); if (m) selectMetric(m.dataset.metric);
        });
        $("egClear").addEventListener("click", () => { if (S.metric) selectMetric(S.metric); });
        $("egMore").addEventListener("click", () => { S.page++; loadTable(true).catch(e => toast(e.message, "error")); });

        // Auto-update: cheap poll; reload only when a new status event was recorded.
        setInterval(async () => {
            const screen = $("screenAnalytics");
            if (document.hidden || !screen || !screen.classList.contains("active") || S.loading) return;
            try {
                const s = await getJson("/api/analytics/engagement/stamp");
                if (S.stamp !== undefined && s.as_of !== S.stamp) load();
            } catch (e) { /* transient; try again next tick */ }
        }, 30000);
    }

    // Wrap at script load (before app.js opens a hash tab) so every analytics
    // load - tab open, Refresh, filter change - also refreshes engagement.
    const orig = window.loadAnalytics;
    if (orig) window.loadAnalytics = async function (o) { try { return await orig.call(this, o); } finally { load(); } };

    document.addEventListener("DOMContentLoaded", init);
})();
