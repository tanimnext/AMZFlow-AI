/* AmzFlow AI — shared browser runtime.
 *
 * Replaces the copy-pasted CSRF wrapper (5x), escapeHtml (2x), three separate
 * modal implementations, and three ad-hoc polling loops that the v6 templates
 * each carried their own drifting version of.
 */
(function () {
    "use strict";

    /* ------------------------------------------------------------- CSRF --- */

    const meta = document.querySelector('meta[name="csrf-token"]');
    const CSRF = meta ? meta.content : "";
    window.CSRF_TOKEN = CSRF;

    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const method = String(init.method || "GET").toUpperCase();
        if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
            const headers = new Headers(init.headers || {});
            headers.set("X-CSRF-Token", CSRF);
            init = Object.assign({}, init, { headers });
        }
        return nativeFetch(input, init);
    };

    /* -------------------------------------------------------------- HTTP --- */

    /** JSON fetch that turns a non-JSON body (an HTML error page, a 500) into a
     *  readable Error instead of the v6 behaviour of throwing a raw SyntaxError
     *  and surfacing "restart run_gui.bat" to the user. */
    async function api(url, options = {}) {
        const init = Object.assign({}, options);
        if (init.body !== undefined && typeof init.body !== "string" && !(init.body instanceof FormData)) {
            init.body = JSON.stringify(init.body);
            init.headers = Object.assign({ "Content-Type": "application/json" }, init.headers || {});
            init.method = init.method || "POST";
        }
        const resp = await fetch(url, init);
        const text = await resp.text();
        let data = null;
        try {
            data = text ? JSON.parse(text) : null;
        } catch (err) {
            throw new Error(
                `${resp.status} ${resp.statusText || "error"} from ${url} ` +
                `(server returned ${text.trim().slice(0, 60) || "an empty body"})`
            );
        }
        if (!resp.ok) {
            throw new Error((data && (data.error || data.message)) || `HTTP ${resp.status}`);
        }
        return data;
    }

    /* --------------------------------------------------------------- DOM --- */

    function escapeHtml(value) {
        const div = document.createElement("div");
        div.textContent = value == null ? "" : String(value);
        return div.innerHTML;
    }

    function el(tag, attrs = {}, children = []) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs)) {
            if (value == null || value === false) continue;
            if (key === "class") node.className = value;
            else if (key === "text") node.textContent = value;
            else if (key === "html") node.innerHTML = value;
            else if (key.startsWith("on") && typeof value === "function") {
                node.addEventListener(key.slice(2).toLowerCase(), value);
            } else node.setAttribute(key, value === true ? "" : value);
        }
        for (const child of [].concat(children)) {
            if (child == null) continue;
            node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
        }
        return node;
    }

    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    /* ------------------------------------------------------------- toast --- */

    function toast(message, kind = "ok", ms = 3200) {
        let host = document.getElementById("toastHost");
        if (!host) {
            host = el("div", { id: "toastHost" });
            document.body.appendChild(host);
        }
        const node = el("div", { class: `toast toast-${kind}`, role: "status", "aria-live": "polite" }, [
            String(message),
        ]);
        host.appendChild(node);
        setTimeout(() => {
            node.style.opacity = "0";
            node.style.transition = "opacity .2s ease";
            setTimeout(() => node.remove(), 220);
        }, ms);
        return node;
    }

    /* ------------------------------------------------------------- modal --- */

    const ICONS = {
        ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"/></svg>',
        warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-2.994-1.5-3.86 0L2.697 16.126ZM12 15.75h.008v.008H12v-.008Z"/></svg>',
        question:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22"><path stroke-linecap="round" stroke-linejoin="round" d="M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 5.25h.008v.008H12v-.008Z"/></svg>',
    };
    const TONE = { ok: "var(--ok-600)", warn: "var(--warn-700)", error: "var(--danger-600)", question: "var(--brand-600)" };

    let openModal = null;

    /** Accessible dialog. Returns a Promise<boolean>: true = confirmed.
     *  Handles focus trap, Escape, backdrop click, and focus restore -- none of
     *  which any of the five v6 modals did. */
    function modal({ title, message, html, kind = "question", confirmText = "OK", cancelText = null, danger = false }) {
        return new Promise((resolve) => {
            const previouslyFocused = document.activeElement;
            const titleId = "modalTitle" + Date.now();

            const body = html
                ? el("div", { html })
                : el("p", { class: "text-[13px] leading-relaxed", style: "color:var(--text-muted)", text: message || "" });

            const confirmBtn = el("button", {
                type: "button",
                class: "btn " + (danger ? "btn-danger" : "btn-primary"),
                text: confirmText,
            });
            const buttons = [confirmBtn];
            if (cancelText) {
                buttons.unshift(el("button", { type: "button", class: "btn btn-ghost", text: cancelText }));
            }

            const panel = el("div", { class: "modal-panel", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId }, [
                el("div", { class: "card-pad" }, [
                    el("div", { class: "flex items-start gap-3 mb-3" }, [
                        el("div", { style: `color:${TONE[kind] || TONE.question};flex:none`, html: ICONS[kind] || ICONS.question }),
                        el("h2", { id: titleId, class: "card-title", style: "margin-top:1px", text: title || "" }),
                    ]),
                    body,
                    el("div", { class: "flex justify-end gap-2 mt-5" }, buttons),
                ]),
            ]);
            const backdrop = el("div", { class: "modal-backdrop" }, [panel]);

            function close(result) {
                document.removeEventListener("keydown", onKey, true);
                backdrop.remove();
                openModal = null;
                if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
                resolve(result);
            }

            function onKey(event) {
                if (event.key === "Escape") {
                    event.preventDefault();
                    close(false);
                    return;
                }
                if (event.key !== "Tab") return;
                const focusable = $$(
                    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
                    panel
                ).filter((node) => !node.disabled && node.offsetParent !== null);
                if (!focusable.length) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }

            confirmBtn.addEventListener("click", () => close(true));
            if (cancelText) buttons[0].addEventListener("click", () => close(false));
            backdrop.addEventListener("mousedown", (event) => {
                if (event.target === backdrop) close(false);
            });
            document.addEventListener("keydown", onKey, true);

            document.body.appendChild(backdrop);
            openModal = backdrop;
            confirmBtn.focus();
        });
    }

    const alertModal = (title, message, kind = "warn") => modal({ title, message, kind, confirmText: "Got it" });
    const confirmModal = (title, message, opts = {}) =>
        modal(Object.assign({ title, message, kind: "question", confirmText: "Confirm", cancelText: "Cancel" }, opts));

    /* -------------------------------------------------------- folder browse --- */

    /** Opens the shared server-backed folder picker (see /api/browse-folders)
     *  and resolves to the chosen absolute path, or null if cancelled. A
     *  plain <input type=file webkitdirectory> can only return a file LIST,
     *  never a path Flask can reuse server-side, so a local desktop app needs
     *  its own folder browser instead. */
    function browseFolder(startPath) {
        const backdrop = document.getElementById("folderBrowserBackdrop");
        if (!backdrop) return Promise.resolve(null);
        const pathEl = document.getElementById("folderBrowserPath");
        const listEl = document.getElementById("folderBrowserList");
        const errorEl = document.getElementById("folderBrowserError");
        const selectBtn = document.getElementById("folderBrowserSelect");
        let current = startPath || "";

        return new Promise((resolve) => {
            let settled = false;
            const close = (result) => {
                if (settled) return;
                settled = true;
                backdrop.hidden = true;
                document.removeEventListener("keydown", onKey, true);
                resolve(result);
            };
            const onKey = (event) => {
                if (event.key === "Escape") close(null);
            };

            async function load(path) {
                errorEl.classList.add("hidden");
                listEl.innerHTML = '<p class="hint" style="padding:12px">Loading…</p>';
                try {
                    const query = path ? `?path=${encodeURIComponent(path)}` : "";
                    const data = await api(`/api/browse-folders${query}`);
                    current = data.path;
                    pathEl.textContent = data.path;
                    selectBtn.disabled = !data.writable;
                    const rows = [];
                    if (data.parent) {
                        rows.push(`<button type="button" class="w-full text-left px-3 py-2 text-[13px]" data-nav="${escapeHtml(data.parent)}" style="border-bottom:1px solid var(--border)">.. (up one level)</button>`);
                    }
                    if (!data.entries.length && !data.parent) {
                        rows.push('<p class="hint" style="padding:12px">No subfolders here.</p>');
                    }
                    data.entries.forEach((entry) => {
                        rows.push(`<button type="button" class="w-full text-left px-3 py-2 text-[13px] flex items-center gap-2" data-nav="${escapeHtml(entry.path)}" style="border-bottom:1px solid var(--border)">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14" style="color:var(--brand-500);flex:none"><path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 0 1 4.5 9.75h15A2.25 2.25 0 0 1 21.75 12v.75m-19.5 0v6a2.25 2.25 0 0 0 2.25 2.25h15a2.25 2.25 0 0 0 2.25-2.25v-6m-19.5 0V6a2.25 2.25 0 0 1 2.25-2.25h5.379a1.5 1.5 0 0 1 1.06.44l2.122 2.12a1.5 1.5 0 0 0 1.06.44H19.5A2.25 2.25 0 0 1 21.75 9v3.75"/></svg>
                            ${escapeHtml(entry.name)}
                        </button>`);
                    });
                    listEl.innerHTML = rows.join("") || '<p class="hint" style="padding:12px">Empty folder.</p>';
                    listEl.querySelectorAll("[data-nav]").forEach((btn) =>
                        btn.addEventListener("click", () => load(btn.dataset.nav))
                    );
                    if (!data.writable) {
                        errorEl.textContent = "This folder is not writable -- pick another one.";
                        errorEl.classList.remove("hidden");
                    }
                } catch (err) {
                    listEl.innerHTML = "";
                    errorEl.textContent = err.message;
                    errorEl.classList.remove("hidden");
                }
            }

            document.getElementById("folderBrowserClose").onclick = () => close(null);
            document.getElementById("folderBrowserCancel").onclick = () => close(null);
            selectBtn.onclick = () => close(current);
            backdrop.onmousedown = (event) => {
                if (event.target === backdrop) close(null);
            };
            document.addEventListener("keydown", onKey, true);

            backdrop.hidden = false;
            load(current);
        });
    }

    /* ------------------------------------------------------------ polling --- */

    /** Repeatedly runs `task` until `shouldStop(result)` is true.
     *  Returns a handle with .stop(); never leaves an interval running after a
     *  navigation or an exception (the v6 upload page leaked one per upload). */
    function poll(task, { intervalMs = 1500, shouldStop = () => false, onError = null, immediate = true } = {}) {
        let timer = null;
        let stopped = false;

        async function tick() {
            if (stopped) return;
            try {
                const result = await task();
                if (stopped) return;
                if (shouldStop(result)) {
                    stopped = true;
                    return;
                }
            } catch (err) {
                if (onError) onError(err);
                else console.error("[poll]", err);
            }
            if (!stopped) timer = setTimeout(tick, intervalMs);
        }

        if (immediate) tick();
        else timer = setTimeout(tick, intervalMs);

        const handle = {
            stop() {
                stopped = true;
                if (timer) clearTimeout(timer);
            },
        };
        window.addEventListener("beforeunload", handle.stop, { once: true });
        return handle;
    }

    /** Puts a button into a spinner/disabled state for the duration of `work`. */
    async function withBusy(button, label, work) {
        if (!button) return work();
        const original = button.innerHTML;
        const wasDisabled = button.disabled;
        button.disabled = true;
        button.innerHTML = "";
        button.appendChild(el("span", { class: "spinner" }));
        button.appendChild(document.createTextNode(" " + (label || "Working…")));
        try {
            return await work();
        } finally {
            button.disabled = wasDisabled;
            button.innerHTML = original;
        }
    }

    /* ------------------------------------------------------------ helpers --- */

    function formatDuration(seconds) {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        const m = Math.floor(total / 60);
        const s = total % 60;
        return `${m}:${String(s).padStart(2, "0")}`;
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value < 1024) return `${value} B`;
        const units = ["KB", "MB", "GB"];
        let n = value / 1024;
        let i = 0;
        while (n >= 1024 && i < units.length - 1) {
            n /= 1024;
            i += 1;
        }
        return `${n.toFixed(n < 10 ? 1 : 0)} ${units[i]}`;
    }

    function debounce(fn, ms = 250) {
        let timer = null;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), ms);
        };
    }

    /* ------------------------------------------------------ voice preview --- */

    /** Starts (or resolves from cache) a TTS preview and waits for it to
     *  finish, polling only if it wasn't already cached. Shared by every page
     *  that offers a "Preview voice" button. */
    async function previewVoice(payload) {
        let job = await api("/preview_tts", { body: payload });
        if (!job.success && job.status !== "running") {
            throw new Error(job.error || "Preview failed");
        }
        if (job.status === "running") {
            job = await new Promise((resolve, reject) => {
                poll(() => api(`/preview_tts/${job.jobId}`), {
                    intervalMs: 900,
                    shouldStop: (result) => {
                        if (result.status === "done") { resolve(result); return true; }
                        if (result.status === "error") { reject(new Error(result.error || "Preview failed")); return true; }
                        return false;
                    },
                    onError: reject,
                });
            });
        }
        return job;
    }

    /** Plays a finished preview job in the shared bottom-right audio dock. */
    function playPreview(job) {
        const dock = document.getElementById("audioDock");
        const player = document.getElementById("audioDockPlayer");
        const label = document.getElementById("audioDockLabel");
        const meta = document.getElementById("audioDockMeta");
        if (!dock || !player) return;
        player.src = job.audioUrl + (job.cached ? "" : `?t=${Date.now()}`);
        if (label) label.textContent = `Voice preview · ${job.provider || ""}`;
        if (meta) meta.textContent = job.cached ? "From cache" : `Synthesized in ${job.seconds ?? "?"}s`;
        dock.hidden = false;
        player.play().catch(() => {});
        const closeBtn = document.getElementById("audioDockClose");
        if (closeBtn) {
            closeBtn.onclick = () => { dock.hidden = true; player.pause(); };
        }
    }

    /* -------------------------------------------------------------- boot --- */

    document.addEventListener("DOMContentLoaded", () => {
        const bar = document.querySelector(".appbar");
        if (bar) {
            const onScroll = () => bar.classList.toggle("appbar-scrolled", window.scrollY > 8);
            window.addEventListener("scroll", onScroll, { passive: true });
            onScroll();
        }
        $$("[data-current-year]").forEach((node) => {
            node.textContent = String(new Date().getFullYear());
        });
    });

    window.AF = {
        api,
        escapeHtml,
        el,
        $,
        $$,
        toast,
        modal,
        alert: alertModal,
        confirm: confirmModal,
        poll,
        withBusy,
        formatDuration,
        formatBytes,
        debounce,
        previewVoice,
        playPreview,
        browseFolder,
    };
})();
