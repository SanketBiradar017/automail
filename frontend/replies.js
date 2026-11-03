/* AI Reply Assistant: inbox -> thread -> analyze -> draft -> (explicit) send. */

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => escapeHtml(String(v ?? ""));
    const state = { threadId: null, analysis: null, tone: "professional", reqId: 0 };

    function utc(s) { return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z"); }
    function when(s) { return s ? utc(s).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""; }
    function nameOf(from) { return (from || "").replace(/<.*>/, "").replace(/"/g, "").trim() || from; }

    async function api(url, opts) {
        const r = await fetch(url, opts);
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(extractErrorMessage(data, `Request failed (${r.status}).`));
        return data;
    }

    /* ---------- Inbox ---------- */

    window.loadReplyInbox = async function () {
        const list = $("rpList");
        list.innerHTML = '<div class="an-empty">Loading inbox…</div>';
        try {
            const p = new URLSearchParams({ limit: 25 });
            const q = $("rpSearch").value.trim();
            if (q) p.set("q", q);
            if ($("rpNeedsReply").checked) p.set("needs_reply", "true");
            const { threads } = await api(`/api/replies/inbox?${p}`);
            if (!threads.length) { list.innerHTML = '<div class="an-empty">No conversations found.</div>'; return; }
            list.innerHTML = threads.map(t => `
                <button type="button" class="rp-item ${t.thread_id === state.threadId ? "active" : ""} ${t.unread ? "unread" : ""}" data-id="${esc(t.thread_id)}">
                    ${Avatar.html(t.from, "md")}
                    <span class="rp-col">
                        <span class="rp-from">${esc(nameOf(t.from))}</span>
                        <span class="rp-when">${esc(when(t.last_at))}</span>
                        <span class="rp-subj">${esc(t.subject)}</span>
                        <span class="rp-snip">${esc(t.snippet)}</span>
                        <span class="rp-badges">
                            ${t.awaiting_my_reply ? '<i class="tag">awaiting reply</i>' : ""}
                            ${t.message_count > 1 ? `<i class="tag mute">${t.message_count} msgs</i>` : ""}
                        </span>
                    </span>
                </button>`).join("");
        } catch (e) {
            list.innerHTML = `<div class="an-empty">${esc(e.message)}</div>`;
        }
    };

    /* ---------- Thread ---------- */

    async function openThread(id) {
        const my = ++state.reqId;
        state.threadId = id;
        state.analysis = null;
        document.querySelectorAll(".rp-item").forEach(b => b.classList.toggle("active", b.dataset.id === id));
        const box = $("rpDetail");
        box.scrollIntoView({ behavior: "smooth", block: "nearest" });
        box.innerHTML = '<div class="an-empty">Loading conversation…</div>';
        try {
            const { thread, reply_to } = await api(`/api/replies/thread/${encodeURIComponent(id)}`);
            if (my !== state.reqId) return;   // user clicked another thread meanwhile
            renderThread(thread, reply_to);
        } catch (e) {
            box.innerHTML = `<div class="an-empty">${esc(e.message)}</div>`;
        }
    }

    function renderThread(thread, replyTo) {
        const msgs = thread.messages.map((m, i) => `
            <article class="rp-msg ${m.is_me ? "me" : ""}">
                <div class="rp-msg-head">
                    ${Avatar.html(m.from, "md")}
                    <div class="who"><strong>${m.is_me ? "You" : esc(nameOf(m.from))}</strong></div>
                    <span>${esc(when(m.date))}</span>
                </div>
                <div class="rp-body ${i < thread.messages.length - 1 ? "collapsed" : ""}">${esc(m.body)}</div>
                ${i < thread.messages.length - 1 ? '<button type="button" class="link-toggle rp-more">Show more</button>' : ""}
            </article>`).join("");

        $("rpDetail").innerHTML = `
            <h3 class="rp-title">${esc(thread.subject)}</h3>
            <div class="rp-thread">${msgs}</div>

            <div class="rp-assist">
                <div class="rp-assist-head">
                    <h4>✦ Assistant</h4>
                    <button type="button" class="secondary an-btn" id="rpAnalyze">Summarize &amp; find questions</button>
                </div>
                <div id="rpAnalysis"></div>

                <div class="rp-tones" role="group" aria-label="Reply tone">
                    <button type="button" data-tone="professional" class="tone">Professional</button>
                    <button type="button" data-tone="friendly" class="tone">Friendly</button>
                    <button type="button" data-tone="concise" class="tone">Concise</button>
                </div>
                <input id="rpInstructions" type="text" maxlength="600" autocomplete="off"
                       placeholder="Optional: tell the AI what to say (e.g. “accept, propose Tuesday 3pm”)">
                <button type="button" class="send rp-gen" id="rpGenerate">✦ Draft reply</button>

                <div id="rpDraftBox" style="display:none">
                    <label for="rpDraft">Draft ${replyTo ? `<span class="optional-tag rp-to">will be sent to ${Avatar.html(replyTo, "sm")}${esc(replyTo)}</span>` : ""}</label>
                    <textarea id="rpDraft" rows="10"></textarea>
                    <p class="field-hint">Draft only. Review and edit before sending.</p>
                    <div class="rp-actions">
                        <button type="button" class="secondary" id="rpCopy">Copy</button>
                        <button type="button" class="secondary" id="rpDiscard">Discard</button>
                        <button type="button" class="send" id="rpSend" ${replyTo ? "" : "disabled"}>Send reply</button>
                    </div>
                </div>
            </div>`;
        setTone(state.tone);
    }

    function setTone(t) {
        state.tone = t;
        document.querySelectorAll(".rp-tones .tone").forEach(b => b.classList.toggle("active", b.dataset.tone === t));
    }

    /* ---------- Actions ---------- */

    async function analyze() {
        const btn = $("rpAnalyze"), out = $("rpAnalysis");
        btn.disabled = true; btn.textContent = "Analyzing…";
        const my = state.reqId;
        try {
            const a = await api(`/api/replies/thread/${encodeURIComponent(state.threadId)}/analyze`, { method: "POST" });
            if (my !== state.reqId) return;
            state.analysis = a;
            out.innerHTML = `
                <div class="rp-card">
                    <p>${esc(a.summary)}</p>
                    <div class="rp-meta">
                        <span class="tag ${a.needs_reply ? "" : "mute"}">${a.needs_reply ? "Needs a reply" : "No reply needed"}</span>
                        <span class="tag mute">${esc(a.sentiment)}</span>
                    </div>
                    ${a.questions.length ? `<h5>Questions to answer</h5><ul>${a.questions.map(q => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
                    ${a.action_items.length ? `<h5>Action items</h5><ul>${a.action_items.map(q => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
                </div>`;
        } catch (e) {
            toast(e.message, "error");
        } finally {
            if (btn.isConnected) { btn.disabled = false; btn.textContent = "Summarize & find questions"; }
        }
    }

    async function generate() {
        const btn = $("rpGenerate");
        btn.disabled = true; btn.textContent = "Drafting…";
        const my = state.reqId;
        try {
            const body = {
                tone: state.tone,
                instructions: $("rpInstructions").value,
                questions: state.analysis ? state.analysis.questions : null,
            };
            const { draft } = await api(`/api/replies/thread/${encodeURIComponent(state.threadId)}/draft`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
            });
            if (my !== state.reqId) return;
            $("rpDraftBox").style.display = "";
            $("rpDraft").value = draft;
            $("rpDraft").focus();
        } catch (e) {
            toast(e.message, "error");
        } finally {
            if (btn.isConnected) { btn.disabled = false; btn.textContent = "✦ Draft reply"; }
        }
    }

    async function sendReply() {
        const text = $("rpDraft").value.trim();
        if (!text) return toast("Draft is empty.", "error");
        if (!confirm("Send this reply now?")) return;
        const btn = $("rpSend");
        btn.disabled = true; btn.textContent = "Sending…";
        try {
            await api(`/api/replies/thread/${encodeURIComponent(state.threadId)}/send`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: text }),
            });
            toast("Reply sent.", "success");
            $("rpDraftBox").style.display = "none";
            $("rpDraft").value = "";
            window.loadReplyInbox();
        } catch (e) {
            toast(e.message, "error");
        } finally {
            if (btn.isConnected) { btn.disabled = false; btn.textContent = "Send reply"; }
        }
    }

    // Used by the Inbox tab's "Reply with AI" button.
    window.openReplyThread = openThread;

    /* ---------- Wiring (event delegation) ---------- */

    document.addEventListener("DOMContentLoaded", () => {
        $("rpList").addEventListener("click", (e) => {
            const b = e.target.closest(".rp-item");
            if (b) openThread(b.dataset.id);
        });
        $("rpRefresh").addEventListener("click", () => window.loadReplyInbox());
        $("rpNeedsReply").addEventListener("change", () => window.loadReplyInbox());
        let timer;
        $("rpSearch").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(window.loadReplyInbox, 400); });

        $("rpDetail").addEventListener("click", (e) => {
            const t = e.target;
            if (t.closest(".rp-more")) {
                const btn = t.closest(".rp-more"), body = btn.previousElementSibling;
                body.classList.toggle("collapsed");
                btn.textContent = body.classList.contains("collapsed") ? "Show more" : "Show less";
            } else if (t.closest(".tone")) setTone(t.closest(".tone").dataset.tone);
            else if (t.id === "rpAnalyze") analyze();
            else if (t.id === "rpGenerate") generate();
            else if (t.id === "rpSend") sendReply();
            else if (t.id === "rpDiscard") { $("rpDraftBox").style.display = "none"; $("rpDraft").value = ""; }
            else if (t.id === "rpCopy") navigator.clipboard?.writeText($("rpDraft").value).then(() => toast("Copied.", "success"));
        });
    });
})();
