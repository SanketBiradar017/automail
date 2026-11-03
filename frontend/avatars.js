/* Avatars: circular profile photos with an initials fallback.
   Markup is rendered synchronously as initials (never an <img> that can break);
   hydrate() then asks the backend which addresses have a photo and layers the
   image on top only once it has actually loaded. */

const Avatar = (function () {
    const PALETTE = [210, 262, 330, 24, 150, 190, 292, 48];   // hues
    const known = new Map();      // email -> true/false (resolved)
    const inflight = new Map();   // email -> Promise
    let photosOk = true;

    function parse(raw) {
        const s = String(raw || "").trim();
        const m = s.match(/^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$/);
        const email = (m ? m[2] : s.includes("@") ? s : "").trim().toLowerCase();
        const name = m ? m[1].trim() : "";
        return { email, name };
    }

    function hue(key) {
        let h = 0;
        for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
        return PALETTE[h % PALETTE.length];
    }

    function initials(name, email) {
        const source = (name && name.replace(/[^\p{L}\p{N} ]/gu, "").trim()) ||
                       (email ? email.split("@")[0].replace(/[._\-+0-9]+/g, " ").trim() : "");
        const parts = source.split(/\s+/).filter(Boolean);
        if (!parts.length) return "?";
        const first = Array.from(parts[0])[0];
        const last = parts.length > 1 ? Array.from(parts[parts.length - 1])[0] : "";
        return (first + last).toUpperCase();
    }

    const esc = (v) => String(v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

    /* size: sm (28) | md (38) | lg (44) */
    function html(raw, size = "md", nameOverride = "") {
        const { email, name } = parse(raw);
        const label = nameOverride || name;
        const key = email || label || "?";
        return `<span class="av av-${size}" data-email="${esc(email)}" aria-hidden="true" style="--av-h:${hue(key)}">` +
               `<b>${esc(initials(label, email))}</b></span>`;
    }

    async function resolve(emails) {
        const need = emails.filter(e => !known.has(e));
        if (need.length) {
            const pending = need.filter(e => !inflight.has(e));
            if (pending.length) {
                const p = fetch("/api/avatars/resolve", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ emails: pending })
                }).then(r => r.ok ? r.json() : { photos: {} })
                  .catch(() => ({ photos: {} }))
                  .then(d => pending.forEach(e => { known.set(e, !!(d.photos || {})[e]); inflight.delete(e); }));
                pending.forEach(e => inflight.set(e, p));
            }
            await Promise.all(need.map(e => inflight.get(e)).filter(Boolean));
        }
    }

    function attach(el) {
        const email = el.dataset.email;
        if (!email || el.querySelector("img")) return;
        const img = new Image();
        img.alt = "";
        img.decoding = "async";
        img.referrerPolicy = "no-referrer";
        img.onload = () => { el.appendChild(img); el.classList.add("has-photo"); };
        img.onerror = () => { known.set(email, false); };   // stay on initials, no broken icon
        img.src = `/api/avatars/photo?email=${encodeURIComponent(email)}`;
    }

    async function hydrate(root = document) {
        const els = Array.from(root.querySelectorAll(".av[data-email]:not([data-h])"));
        els.forEach(el => el.setAttribute("data-h", "1"));
        const emails = [...new Set(els.map(el => el.dataset.email).filter(Boolean))];
        if (!emails.length) return;
        await resolve(emails);
        els.forEach(el => { if (known.get(el.dataset.email)) attach(el); });
    }

    return { html, hydrate, parse, initials };
})();


/* Hydrate any avatar added to the page by any renderer (debounced). */
(function () {
    let t = null;
    const run = () => { t = null; Avatar.hydrate(document); };
    new MutationObserver(() => { if (!t) t = setTimeout(run, 60); })
        .observe(document.body, { childList: true, subtree: true });
    run();
})();
