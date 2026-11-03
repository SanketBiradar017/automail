/* AI-Powered Template Library. Reuses /api/email/send (Gmail) for sending. */

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (v) => escapeHtml(String(v ?? ""));
    const VAR_RE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

    const state = {
        templates: [], categories: [], tones: [],
        filter: { type: "all", value: "" },   // all | favorites | mine | category
        q: "",
        draft: null,          // the template currently in the editor
        values: {},           // variable values, kept across templates
        tone: "professional", instructions: "",
        mode: "edit",         // edit | preview
        result: null,         // { to, subject, body }
        busy: false,
    };

    async function api(url, method = "GET", body) {
        const r = await fetch(url, {
            method,
            headers: body ? { "Content-Type": "application/json" } : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(extractErrorMessage(data, `Request failed (${r.status}).`));
        return data;
    }

    const varsOf = (...texts) => {
        const out = [];
        texts.forEach(t => { for (const m of (t || "").matchAll(VAR_RE)) if (!out.includes(m[1])) out.push(m[1]); });
        return out;
    };
    const label = (v) => v.replace(/_/g, " ");

    /* ---------- Load + list ---------- */

    window.loadTemplates = async function () {
        try {
            const p = new URLSearchParams();
            const data = await api(`/api/templates?${p}`);
            state.templates = data.templates;
            state.categories = data.categories;
            state.tones = data.tones;
            renderCats();
            renderGrid();
            if (state.draft && state.draft.ref) {
                const fresh = state.templates.find(t => t.ref === state.draft.ref);
                if (fresh) state.draft.is_favorite = fresh.is_favorite;
            }
        } catch (e) {
            $("tlGrid").innerHTML = `<div class="an-empty">${esc(e.message)}</div>`;
        }
    };

    function visible() {
        const { type, value } = state.filter;
        const q = state.q.trim().toLowerCase();
        return state.templates.filter(t => {
            if (type === "favorites" && !t.is_favorite) return false;
            if (type === "mine" && t.is_builtin) return false;
            if (type === "category" && t.category !== value) return false;
            if (q && !`${t.name} ${t.subject} ${t.body} ${t.description} ${t.category}`.toLowerCase().includes(q)) return false;
            return true;
        });
    }

    function renderCats() {
        const count = (fn) => state.templates.filter(fn).length;
        const item = (type, value, text, n) => {
            const active = state.filter.type === type && state.filter.value === value;
            return `<button type="button" class="tl-cat ${active ? "active" : ""}" data-type="${type}" data-value="${esc(value)}">
                        <span>${esc(text)}</span><em>${n}</em></button>`;
        };
        $("tlCats").innerHTML =
            item("all", "", "All templates", state.templates.length) +
            item("favorites", "", "★ Favorites", count(t => t.is_favorite)) +
            item("mine", "", "My templates", count(t => !t.is_builtin)) +
            '<div class="tl-cat-sep">Categories</div>' +
            state.categories.map(c => item("category", c, c, count(t => t.category === c))).join("");
    }

    const CAT_META = {
        "Career": { cls: "career", icon: "💼", cta: "Reply" },
        "Business": { cls: "business", icon: "🤝", cta: "Book a call" },
        "Workplace": { cls: "workplace", icon: "🏢", cta: "Confirm" },
        "Marketing": { cls: "marketing", icon: "📣", cta: "Learn more" },
        "Customer Support": { cls: "support", icon: "🎧", cta: "View ticket" },
        "Other": { cls: "other", icon: "✉️", cta: "Open" },
    };

    // Escapes, then turns {{variables}} into small chips for the thumbnail.
    function chipify(text) {
        return esc(text).replace(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g,
            (_, n) => `<mark class="tl-chip">${n.replace(/_/g, " ")}</mark>`);
    }

    function thumbHtml(t) {
        const m = CAT_META[t.category] || CAT_META.Other;
        const body = t.body.replace(/\n{2,}/g, "\n").slice(0, 230);
        return `
            <div class="tl-thumb c-${m.cls}">
                <div class="tl-paper">
                    <div class="tl-banner"><span>${m.icon}</span><b>${esc(t.category)}</b></div>
                    <div class="tl-subj">${chipify(t.subject || t.name)}</div>
                    <div class="tl-text">${chipify(body)}</div>
                    <span class="tl-cta">${m.cta}</span>
                </div>
                <button type="button" class="tl-star ${t.is_favorite ? "on" : ""}" data-fav="${esc(t.ref)}" aria-label="Toggle favorite" title="Favorite">${t.is_favorite ? "★" : "☆"}</button>
                <div class="tl-hover"><span>Use template</span></div>
            </div>`;
    }

    function renderGrid() {
        const list = visible();
        $("tlGrid").innerHTML = list.length ? list.map(t => `
            <article class="tl-card ${state.draft && state.draft.ref === t.ref ? "active" : ""}" data-ref="${esc(t.ref)}" tabindex="0">
                ${thumbHtml(t)}
                <div class="tl-meta">
                    <h4>${esc(t.name)}</h4>
                    <p>${esc(t.description || (t.is_builtin ? "" : "Your template"))}</p>
                    <div class="tl-card-foot">
                        <span>${t.variables.length} variable${t.variables.length === 1 ? "" : "s"}</span>
                        <span class="tl-origin ${t.is_builtin ? "" : "mine"}">${t.is_builtin ? "Built-in" : "Yours"}</span>
                    </div>
                </div>
            </article>`).join("")
            : '<div class="an-empty">No templates match. Try another filter, or create your own.</div>';
    }

    /* ---------- Editor ---------- */

    function selectTemplate(t) {
        state.draft = t ? { ref: t.ref, id: t.id, is_builtin: t.is_builtin, name: t.name, category: t.category,
                            subject: t.subject, body: t.body, is_favorite: t.is_favorite }
                        : { ref: null, id: null, is_builtin: false, name: "", category: "Other",
                            subject: "", body: "", is_favorite: false };
        state.mode = "edit";
        state.result = null;
        renderGrid();
        renderEditor();
        if (window.matchMedia("(max-width: 1200px)").matches) $("tlEditor").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function readDraftFields() {
        const d = state.draft;
        if (!d || state.mode !== "edit") return;
        d.name = $("tlName").value; d.category = $("tlCategory").value;
        d.subject = $("tlSubject").value; d.body = $("tlBody").value;
        state.tone = $("tlTone").value; state.instructions = $("tlInstr").value;
    }

    function renderEditor() {
        const d = state.draft;
        if (!d) { $("tlEditor").innerHTML = '<div class="an-empty">Select a template to edit and generate an email.</div>'; return; }
        state.mode === "preview" ? renderPreview() : renderEdit();
    }

    function renderEdit() {
        const d = state.draft;
        const catOpts = state.categories.map(c => `<option ${c === d.category ? "selected" : ""}>${esc(c)}</option>`).join("");
        const toneOpts = state.tones.map(t => `<option value="${esc(t)}" ${t === state.tone ? "selected" : ""}>${esc(t[0].toUpperCase() + t.slice(1))}</option>`).join("");
        $("tlEditor").innerHTML = `
            <div class="tl-ed-head">
                <span class="tag ${d.is_builtin ? "mute" : ""}">${d.is_builtin ? "Built-in" : d.id ? "My template" : "New template"}</span>
                ${d.ref ? `<button type="button" class="tl-star ${d.is_favorite ? "on" : ""}" id="tlEdFav" title="Favorite">${d.is_favorite ? "★" : "☆"}</button>` : ""}
            </div>
            <label for="tlName">Template name</label>
            <input id="tlName" type="text" maxlength="120" autocomplete="off">
            <label for="tlCategory">Category</label>
            <select id="tlCategory">${catOpts}</select>
            <label for="tlSubject">Subject</label>
            <input id="tlSubject" type="text" maxlength="300" autocomplete="off">
            <label for="tlBody">Body</label>
            <textarea id="tlBody" rows="10" maxlength="8000"></textarea>

            <div id="tlVars"></div>

            <label for="tlTone">AI tone</label>
            <select id="tlTone">${toneOpts}</select>
            <label for="tlInstr">Additional instructions <span class="optional-tag">(optional)</span></label>
            <textarea id="tlInstr" rows="2" maxlength="800" placeholder="e.g. mention I met them at the conference"></textarea>

            <button type="button" class="send tl-primary" id="tlGenerate">✦ Generate email</button>
            <div class="tl-actions">
                <button type="button" class="secondary" id="tlSave">${d.id ? "Save changes" : "Save as my template"}</button>
                ${d.ref ? '<button type="button" class="secondary" id="tlDup">Duplicate</button>' : ""}
                ${d.id ? '<button type="button" class="secondary tl-danger" id="tlDelete">Delete</button>' : ""}
            </div>`;
        $("tlName").value = d.name; $("tlSubject").value = d.subject;
        $("tlBody").value = d.body; $("tlInstr").value = state.instructions;
        renderVars();
    }

    function renderVars() {
        const d = state.draft;
        const names = varsOf(d.subject, d.body);
        const box = $("tlVars");
        if (!names.length) { box.innerHTML = ""; return; }
        box.innerHTML = `<div class="tl-vars"><h5>Fill in variables</h5>` + names.map(n => `
            <label class="tl-var"><span>${esc(label(n))}</span>
                <input type="text" data-var="${esc(n)}" maxlength="300" autocomplete="off" placeholder="{{${esc(n)}}}"></label>`).join("") + "</div>";
        box.querySelectorAll("input[data-var]").forEach(i => { i.value = state.values[i.dataset.var] || ""; });
    }

    /* ---------- Generate / preview ---------- */

    async function generate(keepRecipient) {
        readDraftFields();
        const d = state.draft;
        if (!d.body.trim()) return toast("Template body is empty.", "error");
        const prevTo = keepRecipient && state.result ? state.result.to : "";
        setBusy(true);
        try {
            const r = await api("/api/templates/generate", "POST", {
                subject: d.subject, body: d.body, tone: state.tone,
                instructions: state.instructions, variables: state.values,
            });
            state.result = { to: prevTo, subject: r.subject, body: r.body };
            state.mode = "preview";
            renderEditor();
        } catch (e) {
            toast(e.message, "error");
        } finally { setBusy(false); }
    }

    function setBusy(b) {
        state.busy = b;
        ["tlGenerate", "tlRegen", "tlSend"].forEach(id => {
            const el = $(id); if (!el) return;
            el.disabled = b;
            if (id === "tlGenerate") el.textContent = b ? "Generating…" : "✦ Generate email";
            if (id === "tlRegen") el.textContent = b ? "Generating…" : "↻ Regenerate";
        });
    }

    function renderPreview() {
        const r = state.result;
        $("tlEditor").innerHTML = `
            <button type="button" class="link-toggle" id="tlBack">← Edit template</button>
            <h4 class="tl-pv-title">Generated email</h4>
            <label for="tlTo">To</label>
            <input id="tlTo" type="email" placeholder="recipient@example.com" autocomplete="off">
            <label for="tlPvSubject">Subject</label>
            <input id="tlPvSubject" type="text" maxlength="500" autocomplete="off">
            <label for="tlPvBody">Message</label>
            <textarea id="tlPvBody" rows="14"></textarea>
            <p class="field-hint">Editable. Nothing is sent until you click Send and confirm.</p>
            <div class="tl-actions">
                <button type="button" class="secondary" id="tlRegen">↻ Regenerate</button>
                <button type="button" class="secondary" id="tlCopy">Copy</button>
                <button type="button" class="send" id="tlSend">Send</button>
            </div>`;
        $("tlTo").value = r.to; $("tlPvSubject").value = r.subject; $("tlPvBody").value = r.body;
    }

    function readPreview() {
        if (state.mode !== "preview" || !state.result) return;
        state.result = { to: $("tlTo").value.trim(), subject: $("tlPvSubject").value, body: $("tlPvBody").value };
    }

    async function send() {
        readPreview();
        const r = state.result;
        if (!EMAIL_REGEX.test(r.to)) return toast("Enter a valid recipient email.", "error");
        if (!r.subject.trim() || !r.body.trim()) return toast("Subject and message are required.", "error");
        if (/\[[^\]\n]{2,40}\]/.test(r.body) &&
            !confirm("The email still contains [bracketed placeholders]. Send anyway?")) return;
        if (!confirm(`Send this email to ${r.to}?`)) return;

        setBusy(true);
        try {
            await api("/api/email/send", "POST", {
                recipient: r.to, recipient_name: state.values.recipient_name || "",
                subject: r.subject, body: r.body,
            });
            toast("Email sent.", "success");
        } catch (e) {
            toast(e.message, "error");
        } finally { setBusy(false); }
    }

    /* ---------- Save / duplicate / delete / favorite ---------- */

    function payload() {
        readDraftFields();
        const d = state.draft;
        return { name: d.name.trim(), category: d.category, subject: d.subject, body: d.body };
    }

    async function save() {
        const p = payload();
        if (!p.name) return toast("Give the template a name.", "error");
        if (!p.body.trim()) return toast("Template body is empty.", "error");
        try {
            const d = state.draft;
            const res = d.id ? await api(`/api/templates/${d.id}`, "PUT", p) : await api("/api/templates", "POST", p);
            toast(d.id ? "Template saved." : "Saved to My templates.", "success");
            await window.loadTemplates();
            selectTemplate(state.templates.find(t => t.ref === res.template.ref));
        } catch (e) { toast(e.message, "error"); }
    }

    async function duplicate() {
        try {
            const res = await api("/api/templates/duplicate", "POST", { ref: state.draft.ref });
            toast("Duplicated to My templates.", "success");
            await window.loadTemplates();
            selectTemplate(state.templates.find(t => t.ref === res.template.ref));
        } catch (e) { toast(e.message, "error"); }
    }

    async function remove() {
        const d = state.draft;
        if (!confirm(`Delete template "${d.name}"? This cannot be undone.`)) return;
        try {
            await api(`/api/templates/${d.id}`, "DELETE");
            toast("Template deleted.", "success");
            state.draft = null;
            await window.loadTemplates();
            renderEditor();
        } catch (e) { toast(e.message, "error"); }
    }

    async function toggleFav(ref) {
        const t = state.templates.find(x => x.ref === ref);
        if (!t) return;
        try {
            await api("/api/templates/favorite", "POST", { ref, favorite: !t.is_favorite });
            t.is_favorite = !t.is_favorite;
            if (state.draft && state.draft.ref === ref) state.draft.is_favorite = t.is_favorite;
            renderCats(); renderGrid();
            if (state.draft && state.mode === "edit") { readDraftFields(); renderEditor(); }
        } catch (e) { toast(e.message, "error"); }
    }

    /* ---------- Wiring ---------- */

    document.addEventListener("DOMContentLoaded", () => {
        $("tlNew").addEventListener("click", () => selectTemplate(null));

        let timer;
        $("tlSearch").addEventListener("input", (e) => {
            state.q = e.target.value; clearTimeout(timer); timer = setTimeout(renderGrid, 150);
        });

        $("tlCats").addEventListener("click", (e) => {
            const b = e.target.closest(".tl-cat"); if (!b) return;
            state.filter = { type: b.dataset.type, value: b.dataset.value };
            renderCats(); renderGrid();
        });

        $("tlGrid").addEventListener("click", (e) => {
            const star = e.target.closest("[data-fav]");
            if (star) return toggleFav(star.dataset.fav);
            const card = e.target.closest(".tl-card");
            if (card) selectTemplate(state.templates.find(t => t.ref === card.dataset.ref));
        });
        $("tlGrid").addEventListener("keydown", (e) => {
            if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("tl-card")) {
                e.preventDefault();
                selectTemplate(state.templates.find(t => t.ref === e.target.dataset.ref));
            }
        });

        const ed = $("tlEditor");
        ed.addEventListener("input", (e) => {
            const t = e.target;
            if (t.dataset.var !== undefined) state.values[t.dataset.var] = t.value;
            else if (t.id === "tlSubject" || t.id === "tlBody") {
                clearTimeout(timer);
                timer = setTimeout(() => { readDraftFields(); renderVars(); }, 250);
            }
        });
        ed.addEventListener("click", (e) => {
            const id = e.target.closest("button")?.id;
            if (id === "tlGenerate") generate(false);
            else if (id === "tlRegen") { readPreview(); generate(true); }
            else if (id === "tlBack") { readPreview(); state.mode = "edit"; renderEditor(); }
            else if (id === "tlSend") send();
            else if (id === "tlSave") save();
            else if (id === "tlDup") duplicate();
            else if (id === "tlDelete") remove();
            else if (id === "tlEdFav") toggleFav(state.draft.ref);
            else if (id === "tlCopy") {
                readPreview();
                navigator.clipboard?.writeText(`Subject: ${state.result.subject}\n\n${state.result.body}`)
                    .then(() => toast("Copied.", "success"));
            }
        });
    });
})();
