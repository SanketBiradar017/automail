/* WhatsApp-style delivery status ticks.
   Ticks.html(tracking, {label}) renders from the backend's evidence-based
   status payload (backend/email_status.py). Nothing is inferred client-side. */

const Ticks = (function () {
    const PATHS = {
        single: '<path d="M2 7.2l3.4 3.4L12 3.2"/>',
        double: '<path d="M1.5 7.2l3.4 3.4L11.4 3.2"/><path d="M7 9.6l.9.9L14.5 3.2"/>',
        blue: '<path d="M1.5 7.2l3.4 3.4L11.4 3.2"/><path d="M7 9.6l.9.9L14.5 3.2"/>',
        green: '<path d="M1.5 7.2l3.4 3.4L11.4 3.2"/><path d="M7 9.6l.9.9L14.5 3.2"/>',
        x: '<path d="M4 3.5l7 7M11 3.5l-7 7"/>',
        none: '<circle cx="8" cy="7" r="2.2"/>',
    };
    const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const SOURCES = {
        dsn: "delivery receipt from the recipient's server", reply: "their reply (it must have been read)",
        auto_reply: "an automatic reply", read_receipt: "a read receipt", open_pixel: "the opt-in tracking pixel",
    };

    function when(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        return isNaN(d) ? "" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }

    function tooltip(t) {
        const lines = [t.tip];
        if (t.sent_at && t.state !== "failed") lines.push(`Sent ${when(t.sent_at)}`);
        if (t.delivered_at && t.state !== "bounced") lines.push(`Delivered ${when(t.delivered_at)}${SOURCES[t.delivered_source] ? " — via " + SOURCES[t.delivered_source] : ""}`);
        if (t.opened_at) lines.push(`Opened ${when(t.opened_at)}${SOURCES[t.opened_source] ? " — via " + SOURCES[t.opened_source] : ""}`);
        if (t.replied_at) lines.push(`Replied ${when(t.replied_at)}`);
        if (t.bounced_at) lines.push(`Bounced ${when(t.bounced_at)}${t.bounce_reason ? ": " + t.bounce_reason.slice(0, 120) : ""}`);
        if (t.state === "failed" && t.error) lines.push(String(t.error).slice(0, 160));
        if (t.state === "sent" || t.state === "delivered") {
            lines.push(t.open_tracking ? "Opens: tracking requested; not opened yet." : "Opens: not tracked for this email.");
        }
        return lines.join("\n");
    }

    function html(t, opts = {}) {
        if (!t) return "";
        const tip = tooltip(t);
        const svg = `<svg viewBox="0 0 16 14" width="16" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[t.tick] || PATHS.none}</svg>`;
        return `<span class="tick tick-${esc(t.state)}" role="img" tabindex="0" title="${esc(tip)}" aria-label="${esc(t.label + ". " + tip.replace(/\n/g, " "))}">${svg}${opts.label ? `<em>${esc(t.label)}</em>` : ""}</span>`;
    }

    return { html, tooltip };
})();
