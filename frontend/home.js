/* Home page, theme toggle, motion. */

(function () {
    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* ---------- Theme ---------- */

    function currentTheme() {
        return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("automail-theme", theme); } catch (e) { /* private mode */ }
    }

    document.getElementById("themeToggle")?.addEventListener("click", () => {
        applyTheme(currentTheme() === "light" ? "dark" : "light");
    });


    /* ---------- Home navigation shortcuts ---------- */

    document.addEventListener("click", (e) => {
        const el = e.target.closest("[data-goto]");
        if (el && typeof switchTab === "function") switchTab(el.dataset.goto);
    });


    /* ---------- Typing demo ---------- */

    const typedEl = document.getElementById("mockTyped");
    const MOCK_TEXT =
        "Hi Alex,\n\nI've been following your team's work and think there's a " +
        "natural fit between what we do. Would you be open to a 15-minute " +
        "chat next week?\n\nBest,\nSam";

    function runTyping() {
        if (!typedEl) return;
        if (reduceMotion) { typedEl.textContent = MOCK_TEXT; return; }

        let i = 0;
        const tick = () => {
            if (!document.getElementById("screenHome")?.classList.contains("active") || document.hidden) {
                return setTimeout(tick, 600);   // pause off-screen
            }
            typedEl.textContent = MOCK_TEXT.slice(0, i);
            if (i < MOCK_TEXT.length) { i++; setTimeout(tick, 22 + Math.random() * 28); }
            else { i = 0; setTimeout(tick, 3500); }   // hold, then loop
        };
        setTimeout(tick, 1200);
    }


    /* ---------- Scroll reveal ---------- */

    function initReveal() {
        const items = document.querySelectorAll(".scroll-reveal");
        if (reduceMotion || !("IntersectionObserver" in window)) {
            items.forEach(i => i.classList.add("in"));
            return;
        }
        const io = new IntersectionObserver((entries) => {
            entries.forEach(en => {
                if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
            });
        }, { threshold: 0.15 });
        items.forEach(i => io.observe(i));
    }


    /* ---------- Count-up stats (real numbers) ---------- */

    function countTo(el, target, suffix, decimals) {
        const fmt = (v) => (decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString()) + suffix;
        if (reduceMotion) { el.textContent = fmt(target); return; }
        const from = parseFloat(el.dataset.current || "0");
        const start = performance.now();
        const dur = 1100;
        const step = (now) => {
            const t = Math.min(1, (now - start) / dur);
            const eased = 1 - Math.pow(1 - t, 3);
            el.textContent = fmt(from + (target - from) * eased);
            if (t < 1) requestAnimationFrame(step);
            else el.dataset.current = String(target);
        };
        requestAnimationFrame(step);
    }

    window.refreshHomeStats = function () {};   // home stats removed

    document.addEventListener("DOMContentLoaded", () => {
        runTyping();
        initReveal();
        window.refreshHomeStats();

        const tab = location.hash.slice(1);
        if (tab && document.querySelector(`.nav-btn[data-tab="${tab}"]`)) switchTab(tab);
    });
})();
