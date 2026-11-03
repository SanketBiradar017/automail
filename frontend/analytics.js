/* Email Analytics & Insights screen. */

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => (typeof escapeHtml === "function" ? escapeHtml(String(v ?? "")) : String(v ?? ""));
    let loading = false;

    function params() {
        return new URLSearchParams({ days: $("anRange").value, kind: $("anKind").value });
    }

    function utc(s) {   // server timestamps are naive UTC
        if (!s) return null;
        return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z");
    }

    const pct = (v) => `${(+v).toFixed(1)}%`;

    /* ---------- Load ---------- */

    window.loadAnalytics = async function (opts = {}) {
        if (loading) return;
        loading = true;
        const sync = opts.sync === false ? "false" : "true";
        const note = $("anSyncNote");
        note.textContent = "· checking Gmail for replies…";
        try {
            const p = params(); p.set("sync", sync);
            const r = await fetch(`/api/analytics/overview?${p}`);
            const data = await r.json();
            if (!r.ok) throw new Error(extractErrorMessage(data, "Failed to load analytics."));
            render(data);
            const s = data.sync;
            note.textContent = s && s.skipped ? `· Gmail sync skipped (${s.skipped})`
                : s ? `· updated ${new Date().toLocaleTimeString()}` : "";
        } catch (e) {
            note.textContent = "";
            toast(e.message, "error");
        } finally {
            loading = false;
        }
    };

    /* ---------- Render ---------- */

    function render(d) {
        renderKpis(d.totals);
        renderTimeline(d.timeline);
        renderCampaigns(d.campaigns);
        renderSubjects(d.subjects);
        renderFollowups(d.followups);
    }

    function renderKpis(t) {
        const cards = [
            ["Sent", t.sent, `${t.failed} failed`],
            ["Delivered", t.delivered, "sent, no bounce"],
            ["Bounced", t.bounced, pct(t.bounce_rate) + " of sent", t.bounced ? "bad" : ""],
            ["Replied", t.replied, "inbound replies"],
            ["Reply rate", pct(t.reply_rate), "of delivered", "accent"],
        ];
        $("anKpis").innerHTML = cards.map(([label, value, sub, cls]) => `
            <div class="kpi ${cls || ""}">
                <span class="kpi-label">${label}</span>
                <strong class="kpi-value">${value}</strong>
                <span class="kpi-sub">${sub}</span>
            </div>`).join("");
    }

    function renderTimeline(rows) {
        const el = $("anTimeline");
        if (!rows.length) { el.innerHTML = empty("No sends in this range yet."); return; }

        const max = Math.max(1, ...rows.map(r => Math.max(r.sent, r.replied)));
        const W = 720, H = 180, pad = 24;
        const slot = (W - pad * 2) / rows.length;
        const bw = Math.max(3, Math.min(28, slot * 0.6));
        const y = (v) => H - pad - (v / max) * (H - pad * 2);

        const bars = rows.map((r, i) => {
            const x = pad + i * slot + (slot - bw) / 2;
            const h = H - pad - y(r.sent);
            return `<rect class="bar" x="${x}" y="${y(r.sent)}" width="${bw}" height="${h}" rx="3">
                <title>${r.date}: ${r.sent} sent, ${r.replied} replies</title></rect>`;
        }).join("");

        const pts = rows.map((r, i) => `${pad + i * slot + slot / 2},${y(r.replied)}`).join(" ");
        const dots = rows.map((r, i) =>
            `<circle class="dot" cx="${pad + i * slot + slot / 2}" cy="${y(r.replied)}" r="3.5"><title>${r.date}: ${r.replied} replies</title></circle>`
        ).join("");

        const first = rows[0].date, last = rows[rows.length - 1].date;
        el.innerHTML = `
            <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Sent and replies per day">
                <line class="axis" x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}"/>
                <text class="tick" x="${pad}" y="${H - 6}">${first}</text>
                <text class="tick" x="${W - pad}" y="${H - 6}" text-anchor="end">${last}</text>
                <text class="tick" x="${pad}" y="12">max ${max}</text>
                ${bars}
                <polyline class="line" points="${pts}"/>
                ${dots}
            </svg>
            <div class="legend"><span><i class="sw bar"></i>Sent</span><span><i class="sw line"></i>Replies</span></div>`;
    }

    function renderCampaigns(rows) {
        const t = $("anCampaigns");
        if (!rows.length) { t.innerHTML = `<tbody><tr><td>${empty("No campaigns in this range.")}</td></tr></tbody>`; return; }
        t.innerHTML = `
            <thead><tr>
                <th>Campaign</th><th>Type</th><th class="n">Sent</th><th class="n">Bounced</th>
                <th class="n">Replied</th><th class="n">Reply rate</th><th>Date</th>
            </tr></thead>
            <tbody>${rows.map(c => `
                <tr>
                    <td class="name" title="${esc(c.name)}">${esc(c.name.replace(/^(Single|Scheduled): /, ""))}</td>
                    <td><span class="tag">${esc(c.kind)}</span></td>
                    <td class="n">${c.sent}${c.failed ? ` <small class="bad">(${c.failed} failed)</small>` : ""}</td>
                    <td class="n ${c.bounced ? "bad" : ""}">${c.bounced}</td>
                    <td class="n">${c.replied}</td>
                    <td class="n"><strong>${pct(c.reply_rate)}</strong></td>
                    <td>${c.created_at ? utc(c.created_at).toLocaleDateString() : ""}</td>
                </tr>`).join("")}
            </tbody>`;
    }

    function renderSubjects(rows) {
        const el = $("anSubjects");
        if (!rows.length) { el.innerHTML = empty("No subject data yet."); return; }
        const max = Math.max(1, ...rows.map(r => r.reply_rate));
        el.innerHTML = rows.map((s, i) => `
            <div class="subj">
                <div class="subj-top">
                    <span class="subj-name" title="${esc(s.subject)}">${i === 0 && s.replied ? "🏆 " : ""}${esc(s.subject)}</span>
                    <span class="subj-num"><strong>${pct(s.reply_rate)}</strong>
                        <small>${s.replied}/${s.delivered} replied${s.delivered < 5 ? " · small sample" : ""}</small></span>
                </div>
                <div class="subj-track"><div class="subj-fill" style="width:${(s.reply_rate / max) * 100}%"></div></div>
            </div>`).join("");
    }

    function renderFollowups(f) {
        const el = $("anFollowups");
        if (!f.sequences) { el.innerHTML = empty("No follow-up sequences in this range. Enable follow-ups when sending to compare."); return; }
        const replied = f.replied_before_followup + f.replied_after_followup;
        const lift = f.followup_reply_rate - f.no_followup_reply_rate;
        el.innerHTML = `
            <div class="kpi-grid small">
                <div class="kpi"><span class="kpi-label">Sequences</span><strong class="kpi-value">${f.sequences}</strong><span class="kpi-sub">${f.sequences_with_followup_sent} sent ≥1 follow-up</span></div>
                <div class="kpi"><span class="kpi-label">Follow-ups sent</span><strong class="kpi-value">${f.followups_sent}</strong><span class="kpi-sub">total messages</span></div>
                <div class="kpi accent"><span class="kpi-label">Replied after follow-up</span><strong class="kpi-value">${f.replied_after_followup}</strong><span class="kpi-sub">${f.replied_before_followup} replied before one</span></div>
                <div class="kpi"><span class="kpi-label">Reply rate</span><strong class="kpi-value">${pct(f.followup_reply_rate)}</strong><span class="kpi-sub">vs ${pct(f.no_followup_reply_rate)} without</span></div>
            </div>
            <p class="an-hint">${
                f.no_followup_delivered < 5 || f.sequences_with_followup_sent < 5
                    ? "Not enough data for a reliable comparison yet."
                    : lift > 0 ? `Follow-ups lift reply rate by ${lift.toFixed(1)} points.`
                    : "Follow-ups show no reply-rate lift so far."
            } ${replied ? "" : "No replies recorded yet."}</p>`;
    }

    function empty(msg) { return `<div class="an-empty">${msg}</div>`; }

    /* ---------- Wiring ---------- */

    document.addEventListener("DOMContentLoaded", () => {
        const reload = () => window.loadAnalytics({ sync: false });
        $("anRange").addEventListener("change", reload);
        $("anKind").addEventListener("change", reload);
        $("anRefresh").addEventListener("click", () => window.loadAnalytics());
        $("anExportCampaigns").addEventListener("click", () => {
            location.href = `/api/analytics/export.csv?report=campaigns&${params()}`;
        });
        $("anExportEmails").addEventListener("click", () => {
            location.href = `/api/analytics/export.csv?report=emails&${params()}`;
        });
    });
})();
