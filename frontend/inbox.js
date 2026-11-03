/* Mailboxes: read Gmail mail (read-only). One factory, used for the Inbox and the Sent screens.
   Filters map to Gmail search operators on the server (see backend/routes/inbox_routes.py). */

function createMailbox(P, cfg) {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => escapeHtml(String(v ?? ""));
    const state = { folder: cfg.folder, threadId: null, next: null, reqId: 0,
        f: { when: "any", after: "", before: "", att: false, unread: false, addr: "" } };

    function utc(s) { return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z"); }
    function when(s) { return s ? utc(s).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
    function firstAddr(v) { return (v || "").split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/)[0].trim(); }
    function nameOf(v) { return (v || "").replace(/<.*>/, "").replace(/"/g, "").trim() || v; }

    async function api(url) {
        const r = await fetch(url);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(extractErrorMessage(data, `Request failed (${r.status}).`));
        return data;
    }

    function itemHtml(t) {
        const who = state.folder === "sent" ? `To: ${nameOf(t.to)}` : nameOf(t.from);
        return `
            <button type="button" class="rp-item ${t.thread_id === state.threadId ? "active" : ""} ${t.unread ? "unread" : ""}" data-id="${esc(t.thread_id)}">
                ${Avatar.html(state.folder === "sent" ? firstAddr(t.to) : t.from, "md")}
                <span class="rp-col">
                    <span class="rp-from">${t.starred ? "★ " : ""}${esc(who)}</span>
                    <span class="rp-when">${esc(when(t.last_at))}${state.folder === "sent" ? Ticks.html(t.tracking) : ""}</span>
                    <span class="rp-subj">${esc(t.subject)}</span>
                    <span class="rp-snip">${esc(t.snippet)}</span>
                    ${t.message_count > 1 ? `<span class="rp-badges"><i class="tag mute">${t.message_count} msgs</i></span>` : ""}
                </span>
            </button>`;
    }

    async function load(append = false) {
        const list = $(P + "List");
        if (!append) { list.innerHTML = '<div class="an-empty">Loading…</div>'; state.next = null; }
        const my = ++state.reqId;
        try {
            const p = new URLSearchParams({ folder: state.folder, limit: 20 });
            const q = $(P + "Search").value.trim();
            if (q) p.set("q", q);
            const f = state.f;
            if (f.when !== "any") p.set("when", f.when);
            if (f.when === "custom") { if (f.after) p.set("after", f.after); if (f.before) p.set("before", f.before); }
            if (f.att) p.set("has_attachment", "true");
            if (f.unread) p.set("unread", "true");
            if (f.addr) p.set("addr", f.addr);
            if (append && state.next) p.set("page_token", state.next);
            const data = await api(`/api/inbox?${p}`);
            if (my !== state.reqId) return;
            state.next = data.next_page_token;
            list.querySelector(".ib-more")?.remove();
            if (!append) list.innerHTML = "";
            if (!data.threads.length && !append) { list.innerHTML = '<div class="an-empty">No messages.</div>'; return; }
            list.insertAdjacentHTML("beforeend", data.threads.map(itemHtml).join("")
                + (state.next ? '<button type="button" class="secondary ib-more">Load more</button>' : ""));
        } catch (e) {
            if (my === state.reqId) list.innerHTML = `<div class="an-empty">${esc(e.message)}</div>`;
        }
    }
    
    async function open(id) {
        const my = ++state.reqId;
        state.threadId = id;
        document.querySelectorAll("#" + P + "List .rp-item").forEach(b => b.classList.toggle("active", b.dataset.id === id));
        const box = $(P + "Detail");
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        box.innerHTML = '<div class="an-empty">Loading message…</div>';
        try {
            const { thread, reply_to } = await api(`/api/replies/thread/${encodeURIComponent(id)}`);
            if (my !== state.reqId) return;
            const msgs = thread.messages.map(m => `
                <article class="rp-msg ${m.is_me ? "me" : ""}">
                    <div class="rp-msg-head">
                        ${Avatar.html(m.from, "md")}
                        <div class="who">
                            <strong>${m.is_me ? "You" : esc(nameOf(m.from))}</strong>
                            <div class="ib-addr">${m.is_me ? "to " + esc(m.to) : esc(m.from)}${m.cc ? ` · cc ${esc(m.cc)}` : ""}</div>
                        </div>
                        <span>${esc(when(m.date))}</span>
                    </div>
                    <div class="rp-body">${esc(m.body)}</div>
                    ${m.attachments.length ? `<div class="ib-att">${m.attachments.map(a => `<span class="tag mute">📎 ${esc(a)}</span>`).join("")}</div>` : ""}
                </article>`).join("");
            box.innerHTML = `
                <div class="ib-head">
                    <h3 class="rp-title">${esc(thread.subject)}</h3>
                    ${reply_to ? `<button type="button" class="send" id="${P}ReplyAI">💬 Reply with AI</button>` : ""}
                </div>
                <div class="rp-thread">${msgs}</div>`;
        } catch (e) {
            box.innerHTML = `<div class="an-empty">${esc(e.message)}</div>`;
        }
    }

    /* ---------- Gmail-style filter bar ---------- */

    const WHEN = [["any", "Any time"], ["1d", "Last 24 hours"], ["7d", "Last 7 days"], ["30d", "Last 30 days"],
                  ["3m", "Last 3 months"], ["1y", "Last year"], ["custom", "Custom range…"]];

    function whenLabel() {
        const f = state.f;
        if (f.when === "custom") return f.after || f.before ? `${f.after || "…"} → ${f.before || "…"}` : "Custom range";
        return (WHEN.find(w => w[0] === f.when) || WHEN[0])[1];
    }

    function advancedUrl() {
        const f = state.f, q = [cfg.folder === "sent" ? "in:sent" : cfg.folder === "inbox" ? "in:inbox" : ""];
        if (f.when !== "any" && f.when !== "custom") q.push("newer_than:" + f.when);
        if (f.when === "custom") { if (f.after) q.push("after:" + f.after.replace(/-/g, "/")); if (f.before) q.push("before:" + f.before.replace(/-/g, "/")); }
        if (f.att) q.push("has:attachment");
        if (f.unread) q.push("is:unread");
        if (f.addr) q.push(`${cfg.addr.toLowerCase()}:(${f.addr.replace(/["()]/g, "")})`);
        const typed = $(P + "Search").value.trim(); if (typed) q.push(typed);
        return "https://mail.google.com/mail/#search/" + encodeURIComponent(q.filter(Boolean).join(" "));
    }

    function renderFilters() {
        const f = state.f, box = $(P + "Filters");
        if (!box) return;
        const dirty = f.when !== "any" || f.att || f.unread || f.addr;
        box.innerHTML = `
            <div class="fb-wrap">
                <button type="button" class="fb-chip ${f.when !== "any" ? "on" : ""}" data-f="when" aria-haspopup="true" aria-expanded="false">${esc(whenLabel())} <i>▾</i></button>
                <div class="fb-pop" data-pop="when" hidden>
                    ${WHEN.map(([v, l]) => `<button type="button" class="fb-opt ${f.when === v ? "sel" : ""}" data-when="${v}">${l}</button>`).join("")}
                    <div class="fb-dates" ${f.when === "custom" ? "" : "hidden"}>
                        <label>From <input type="date" data-date="after" value="${esc(f.after)}"></label>
                        <label>To <input type="date" data-date="before" value="${esc(f.before)}"></label>
                    </div>
                </div>
            </div>
            <button type="button" class="fb-chip ${f.att ? "on" : ""}" data-f="att" aria-pressed="${f.att}">Has attachment</button>
            <div class="fb-wrap">
                <button type="button" class="fb-chip ${f.addr ? "on" : ""}" data-f="addr" aria-haspopup="true" aria-expanded="false">${f.addr ? esc(cfg.addr + ": " + f.addr) : esc(cfg.addr)} <i>▾</i></button>
                <div class="fb-pop fb-addr" data-pop="addr" hidden>
                    <input type="text" data-addr placeholder="${cfg.addr === "To" ? "Recipient email or name" : "Sender email or name"}" value="${esc(f.addr)}" maxlength="100" autocomplete="off">
                    <div class="fb-addr-actions"><button type="button" class="secondary an-btn" data-addr-clear>Clear</button><button type="button" class="send an-btn" data-addr-apply>Apply</button></div>
                </div>
            </div>
            <button type="button" class="fb-chip ${f.unread ? "on" : ""}" data-f="unread" aria-pressed="${f.unread}">Is unread</button>
            <a class="fb-adv" href="${esc(advancedUrl())}" target="_blank" rel="noopener noreferrer" title="Open this search in Gmail">Advanced search</a>
            ${dirty ? '<button type="button" class="fb-clear" data-clear>Clear filters</button>' : ""}`;
    }

    function closePops(except) {
        document.querySelectorAll(`#${P}Filters .fb-pop`).forEach(p => {
            if (p !== except) { p.hidden = true; p.previousElementSibling?.setAttribute("aria-expanded", "false"); }
        });
    }

    function initFilters() {
        const box = $(P + "Filters");
        if (!box) return;
        renderFilters();
        const apply = () => { renderFilters(); load(false); };
        box.addEventListener("click", (e) => {
            const chip = e.target.closest(".fb-chip");
            if (chip) {
                const k = chip.dataset.f;
                if (k === "att") { state.f.att = !state.f.att; return apply(); }
                if (k === "unread") { state.f.unread = !state.f.unread; return apply(); }
                const pop = chip.nextElementSibling, open = pop.hidden;
                closePops(pop); pop.hidden = !open; chip.setAttribute("aria-expanded", String(open));
                if (open) pop.querySelector("input")?.focus();
                return;
            }
            const opt = e.target.closest("[data-when]");
            if (opt) {
                state.f.when = opt.dataset.when;
                if (state.f.when !== "custom") { state.f.after = state.f.before = ""; apply(); }
                else { renderFilters(); box.querySelector('[data-pop="when"]').hidden = false; }
                return;
            }
            if (e.target.closest("[data-addr-apply]")) { state.f.addr = box.querySelector("[data-addr]").value.trim(); return apply(); }
            if (e.target.closest("[data-addr-clear]")) { state.f.addr = ""; return apply(); }
            if (e.target.closest("[data-clear]")) { state.f = { when: "any", after: "", before: "", att: false, unread: false, addr: "" }; apply(); }
        });
        box.addEventListener("change", (e) => {
            const d = e.target.closest("[data-date]");
            if (d) { state.f[d.dataset.date] = d.value; apply(); box.querySelector('[data-pop="when"]').hidden = false; }
        });
        box.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && e.target.matches("[data-addr]")) { state.f.addr = e.target.value.trim(); apply(); }
            if (e.key === "Escape") closePops();
        });
        document.addEventListener("click", (e) => { if (!box.contains(e.target)) closePops(); });
        $(P + "Search").addEventListener("input", () => { const a = box.querySelector(".fb-adv"); if (a) a.href = advancedUrl(); });
    }

    document.addEventListener("DOMContentLoaded", () => {
        initFilters();
        $(P + "List").addEventListener("click", (e) => {
            if (e.target.closest(".ib-more")) return load(true);
            const b = e.target.closest(".rp-item");
            if (b) open(b.dataset.id);
        });
        if ($(P + "Folders")) $(P + "Folders").addEventListener("click", (e) => {
            const b = e.target.closest("[data-folder]");
            if (!b) return;
            document.querySelectorAll("#" + P + "Folders .status-tab-btn").forEach(x => x.classList.toggle("active", x === b));
            state.folder = b.dataset.folder;
            load(false);
        });
        $(P + "Refresh").addEventListener("click", () => load(false));
        let timer;
        $(P + "Search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(false), 400); });
        $(P + "Detail").addEventListener("click", (e) => {
            if (e.target.closest("#" + P + "ReplyAI") && state.threadId) {
                switchTab("replies");
                window.openReplyThread(state.threadId);
            }
        });
    });

    return { load };
}

(function () {
    const inbox = createMailbox("ib", { folder: "inbox", addr: "From" });
    const sent = createMailbox("sn", { folder: "sent", addr: "To" });
    window.loadInbox = () => inbox.load(false);
    window.loadSent = () => sent.load(false);
})();
