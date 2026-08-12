/* AmzFlow AI — Settings page: dynamic AI-provider and TTS-provider panels.
 *
 * v6 hardcoded five LLM provider panels and six TTS panels directly in the
 * template, each a copy-pasted set of fields. Here both panels are built at
 * runtime from the registries the server exposes (model_catalog.py,
 * tts_catalog.py), so adding a provider is a Python dict edit, not a template
 * edit, and voice/model lists come from the provider's live API instead of a
 * baked-in <option> list.
 */
(function () {
    "use strict";
    const { $, $$, api, toast, escapeHtml, withBusy, previewVoice: runPreview, playPreview } = window.AF;

    let LLM_REGISTRY = [];
    let TTS_REGISTRY = [];
    let DIRECTOR_OPTIONS = { styles: [], accents: [] };
    let SETTINGS = {};

    /* ============================================================ settings === */

    function collectSettings() {
        const settings = {};
        $$("input, textarea, select").forEach((el) => {
            if (!el.id || el.dataset.setting === "false") return;
            settings[el.id] = el.type === "checkbox" ? el.checked : el.value;
        });
        return settings;
    }

    async function loadSettings() {
        SETTINGS = await api("/get_settings");
        for (const key in SETTINGS) {
            const el = document.getElementById(key);
            if (!el) continue;
            if (el.type === "checkbox") el.checked = !!SETTINGS[key];
            else el.value = SETTINGS[key];
        }
        return SETTINGS;
    }

    async function saveAllSettings() {
        const button = $("#saveChangesBtn");
        await withBusy(button, "Saving", async () => {
            try {
                await api("/save_settings", { body: collectSettings() });
                toast("Settings saved", "ok");
            } catch (err) {
                toast(`Save failed: ${err.message}`, "error", 6000);
            }
        });
    }

    /* ============================================================ LLM panel === */

    function llmSpec(id) {
        return LLM_REGISTRY.find((p) => p.id === id);
    }

    function renderLlmProviderSelect() {
        const select = $("#llm_service");
        if (!select) return;
        select.innerHTML = LLM_REGISTRY.map((p) => `<option value="${p.id}">${escapeHtml(p.label)}</option>`).join("");
    }

    async function loadModelOptions(providerId, datalistId, refresh = false) {
        const list = document.getElementById(datalistId);
        if (!list) return;
        try {
            const result = await api(`/api/llm/models?provider=${encodeURIComponent(providerId)}${refresh ? "&refresh=1" : ""}`);
            list.innerHTML = result.items.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}${m.note ? " · " + escapeHtml(m.note) : ""}</option>`).join("");
            const note = document.getElementById(`${datalistId}_note`);
            if (note) {
                note.textContent =
                    result.source === "live" ? `${result.items.length} models, live from provider`
                    : result.source === "cache" ? `${result.items.length} models (cached)`
                    : `${result.items.length} built-in models${result.error ? " — " + result.error : ""}`;
            }
        } catch (err) {
            const note = document.getElementById(`${datalistId}_note`);
            if (note) note.textContent = `Could not load models: ${err.message}`;
        }
    }

    function llmPanelHtml(spec) {
        const datalistId = `${spec.id}_model_options`;
        const extraFields = spec.extraFields || [];
        return `
        <div class="space-y-3">
            <label class="field"><span class="label">${escapeHtml(spec.label)} API Keys (one per line)</span>
                <textarea id="${spec.keyField}" rows="3" class="mono" placeholder="One key per line"></textarea></label>
            ${extraFields.length ? `<div class="grid grid-cols-${Math.min(extraFields.length, 2)} gap-3">
                ${extraFields.map((f) => `<label class="field"><span class="label">${escapeHtml(f.label)}</span>
                    <input type="text" id="${f.field}" placeholder="${escapeHtml(f.placeholder || "")}"></label>`).join("")}
            </div>` : ""}
            <div class="grid ${spec.endpointField ? "grid-cols-2" : "grid-cols-1"} gap-3">
                <label class="field"><span class="label">Model</span>
                    <input type="text" id="${spec.modelField}" list="${datalistId}" placeholder="${escapeHtml(spec.defaultModel)}">
                    <datalist id="${datalistId}"></datalist>
                    <span class="hint" id="${datalistId}_note"></span>
                </label>
                ${spec.endpointField ? `<label class="field"><span class="label">Endpoint</span>
                    <input type="text" id="${spec.endpointField}" placeholder="${escapeHtml(spec.defaultEndpoint)}"></label>` : ""}
            </div>
            <div class="flex justify-between items-center">
                <span class="hint">${spec.consoleUrl ? `<a href="${escapeHtml(spec.consoleUrl)}" target="_blank" rel="noopener">${extraFields.length ? "Open Google Cloud Console" : "Get an API key"} →</a>` : ""}</span>
                <button type="button" class="btn btn-sm" data-refresh-models="${spec.id}">Refresh models</button>
            </div>
        </div>`;
    }

    async function renderLlmPanel() {
        const providerId = $("#llm_service")?.value;
        const spec = llmSpec(providerId);
        const panel = $("#llmProviderPanel");
        if (!spec || !panel) return;
        panel.innerHTML = llmPanelHtml(spec);
        // Restore whatever was already loaded from settings for this provider.
        [spec.keyField, spec.modelField, spec.endpointField, ...(spec.extraFields || []).map((f) => f.field)].filter(Boolean).forEach((field) => {
            if (SETTINGS[field] !== undefined) {
                const el = document.getElementById(field);
                if (el) el.value = SETTINGS[field];
            }
        });
        panel.querySelector("[data-refresh-models]")?.addEventListener("click", (event) => {
            loadModelOptions(providerId, `${spec.id}_model_options`, true);
        });
        loadModelOptions(providerId, `${spec.id}_model_options`, false);
    }

    /* --------------------------------------------------- fallback chain UI --- */

    function parseChain(text) {
        return String(text || "")
            .split("\n")
            .map((line) => line.trim())
            .filter(Boolean)
            .map((line) => {
                const [provider, model] = line.split("|");
                return { provider: (provider || "").trim(), model: (model || "").trim() };
            });
    }

    function serializeChain(rows) {
        return rows.filter((r) => r.provider).map((r) => `${r.provider}|${r.model || ""}`).join("\n");
    }

    function syncChainHidden(rows) {
        const hidden = $("#llm_chain");
        if (hidden) hidden.value = serializeChain(rows);
    }

    function renderChainRows(rows) {
        const box = $("#llmChainRows");
        if (!box) return;
        box.innerHTML = rows
            .map(
                (row, index) => `
            <div class="flex gap-2 items-center chain-row" data-index="${index}">
                <select class="chain-provider" style="max-width:180px">
                    ${LLM_REGISTRY.map((p) => `<option value="${p.id}" ${p.id === row.provider ? "selected" : ""}>${escapeHtml(p.label)}</option>`).join("")}
                </select>
                <input type="text" class="chain-model flex-1" placeholder="model (blank = provider default)" value="${escapeHtml(row.model || "")}">
                <button type="button" class="btn btn-icon btn-ghost chain-remove" aria-label="Remove"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg></button>
            </div>`
            )
            .join("");
        box.querySelectorAll(".chain-row").forEach((rowEl) => {
            const index = Number(rowEl.dataset.index);
            rowEl.querySelector(".chain-provider").addEventListener("change", (e) => { rows[index].provider = e.target.value; syncChainHidden(rows); });
            rowEl.querySelector(".chain-model").addEventListener("input", (e) => { rows[index].model = e.target.value; syncChainHidden(rows); });
            rowEl.querySelector(".chain-remove").addEventListener("click", () => {
                rows.splice(index, 1);
                renderChainRows(rows);
                syncChainHidden(rows);
            });
        });
        syncChainHidden(rows);
    }

    function initChainBuilder() {
        const hidden = $("#llm_chain");
        if (!hidden) return;
        const rows = parseChain(hidden.value || SETTINGS.llm_chain);
        renderChainRows(rows);
        $("#llmChainAddBtn")?.addEventListener("click", () => {
            rows.push({ provider: LLM_REGISTRY[0]?.id || "", model: "" });
            renderChainRows(rows);
        });
    }

    async function testLLM() {
        const button = $("#testLlmBtn");
        const resultBox = $("#llm_test_result");
        await withBusy(button, "Testing", async () => {
            try {
                const payload = collectSettings();
                const result = await api("/test_llm", { body: payload });
                resultBox.innerHTML = (result.results || [])
                    .map(
                        (r) => `<div class="flex items-center justify-between gap-2 py-1">
                        <span class="badge ${r.ok ? "badge-ok" : "badge-error"}">${r.ok ? "OK" : "FAIL"}</span>
                        <span class="flex-1 text-[12px] truncate" style="color:var(--text)">${escapeHtml(r.provider)} · ${escapeHtml(r.model)}</span>
                        <span class="text-[11px]" style="color:var(--text-faint)">${r.ok ? r.ms + "ms" : escapeHtml(r.detail)}</span>
                    </div>`
                    )
                    .join("") || `<p class="hint">No provider configured.</p>`;
            } catch (err) {
                resultBox.innerHTML = `<p style="color:var(--danger-600)">${escapeHtml(err.message)}</p>`;
            }
        });
    }

    /* ============================================================ TTS panel === */

    function ttsSpec(id) {
        return TTS_REGISTRY.find((p) => p.id === id);
    }

    function renderTtsProviderSelect() {
        const select = $("#tts_service");
        if (!select) return;
        select.innerHTML = TTS_REGISTRY.map((p) => `<option value="${p.id}">${escapeHtml(p.label)}</option>`).join("");
    }

    function ttsPanelHtml(spec) {
        if (spec.custom) {
            const providerId = customProviderId(spec.id);
            const row = CUSTOM_TTS.find((p) => p.id === providerId);
            return `
                <p class="hint mb-2">This provider is configured below under "Custom TTS Providers". Endpoint: <span class="mono">${escapeHtml(row?.endpoint || "not set")}</span></p>
                <div class="flex gap-2">
                    <button type="button" class="btn flex-1" id="ttsTestBtn">Test Connection</button>
                    <button type="button" class="btn btn-primary flex-1" id="ttsPreviewBtn">Preview Voice</button>
                </div>
                <div id="ttsTestResult" class="mt-2"></div>`;
        }
        const parts = [`<p class="hint mb-2">${escapeHtml(spec.blurb)}</p>`];
        if (spec.needsKey) {
            parts.push(`<label class="field"><span class="label">${escapeHtml(spec.label)} API Key(s)</span>
                <textarea id="${spec.keyField}" rows="2" class="mono" placeholder="One key per line"></textarea></label>`);
        }
        if ((spec.extraFields || []).length) {
            parts.push(`<div class="grid grid-cols-${Math.min(spec.extraFields.length, 2)} gap-3">
                ${spec.extraFields.map((f) => `<label class="field"><span class="label">${escapeHtml(f.label)}</span>
                    <input type="text" id="${f.field}" placeholder="${escapeHtml(f.placeholder || "")}"></label>`).join("")}
            </div>`);
        }
        parts.push(`<label class="field"><span class="label">Voice</span>
            <div class="flex gap-2">
                <select id="${spec.voiceField}" class="flex-1"></select>
                <button type="button" class="btn btn-icon" data-refresh-voices title="Refresh voices" aria-label="Refresh voices">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="15" height="15"><path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"/></svg>
                </button>
            </div>
            <span class="hint" id="ttsVoiceNote"></span></label>`);
        if (spec.hasModelCatalog) {
            // Free-text input + datalist, not a locked <select>: the static/
            // live catalog is a set of suggestions, not the only valid value
            // -- a provider (Gemini in particular) may support a preview or
            // region-limited model that isn't in either list yet.
            parts.push(`<label class="field"><span class="label">Model</span>
                <input type="text" id="${spec.modelField}" list="${spec.modelField}_options" placeholder="Type or pick a model id" autocomplete="off">
                <datalist id="${spec.modelField}_options"></datalist>
                <span class="hint" id="ttsModelNote"></span></label>`);
        }
        if (spec.supportsRate || spec.supportsPitch) {
            parts.push(`<div class="grid grid-cols-2 gap-3">
                ${spec.supportsRate ? `<label class="field"><span class="label">Speed <b id="rate_val">+0%</b></span>
                    <input type="range" id="edge_rate_slider" min="-50" max="50" value="0" step="5" oninput="AF_SETTINGS.updateEdgeSliders()">
                    <input type="hidden" id="edge_rate" value="+0%"></label>` : ""}
                ${spec.supportsPitch ? `<label class="field"><span class="label">Pitch <b id="pitch_val">+0Hz</b></span>
                    <input type="range" id="edge_pitch_slider" min="-20" max="20" value="0" step="1" oninput="AF_SETTINGS.updateEdgeSliders()">
                    <input type="hidden" id="edge_pitch" value="+0Hz"></label>` : ""}
            </div>`);
        }
        if (spec.director) {
            parts.push(`
            <div class="grid grid-cols-2 gap-3">
                <label class="field"><span class="label">Style</span><select id="gemini_voice_style">
                    ${DIRECTOR_OPTIONS.styles.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("")}
                </select></label>
                <label class="field"><span class="label">Accent</span><select id="gemini_voice_accent">
                    ${DIRECTOR_OPTIONS.accents.map((a) => `<option value="${a.id}">${escapeHtml(a.label)}</option>`).join("")}
                </select></label>
            </div>
            <div class="grid grid-cols-3 gap-3">
                <label class="field"><span class="label">Pace <b id="gemini_pace_value">50</b></span>
                    <input id="gemini_voice_pace" type="range" min="0" max="100" value="50" oninput="document.getElementById('gemini_pace_value').textContent=this.value"></label>
                <label class="field"><span class="label">Energy <b id="gemini_energy_value">45</b></span>
                    <input id="gemini_voice_energy" type="range" min="0" max="100" value="45" oninput="document.getElementById('gemini_energy_value').textContent=this.value"></label>
                <label class="field"><span class="label">Warmth <b id="gemini_warmth_value">60</b></span>
                    <input id="gemini_voice_warmth" type="range" min="0" max="100" value="60" oninput="document.getElementById('gemini_warmth_value').textContent=this.value"></label>
            </div>
            <label class="field"><span class="label">Director Instruction</span>
                <textarea id="gemini_voice_instruction" rows="2" maxlength="500" placeholder="Pause briefly before the final verdict."></textarea></label>
            <label class="field"><span class="label">Pronunciation Dictionary</span>
                <textarea id="gemini_pronunciations" rows="3" placeholder="LiDAR=lie-dar&#10;ASIN=A-sin"></textarea>
                <span class="hint">One entry per line using term=pronunciation.</span></label>`);
        }
        parts.push(`<div class="flex gap-2 mt-2">
            ${spec.needsKey ? `<button type="button" class="btn flex-1" id="ttsTestBtn">Test Connection</button>` : ""}
            <button type="button" class="btn btn-primary flex-1" id="ttsPreviewBtn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.348a1.125 1.125 0 0 1 0 1.971l-11.54 6.347a1.125 1.125 0 0 1-1.667-.985V5.653Z"/></svg>
                Preview Voice</button>
        </div>
        <div id="ttsTestResult" class="mt-2"></div>`);
        return parts.join("");
    }

    async function loadTtsVoiceAndModel(providerId, refresh = false) {
        const spec = ttsSpec(providerId);
        if (!spec) return;
        const voiceSelect = document.getElementById(spec.voiceField);
        const modelSelect = spec.modelField ? document.getElementById(spec.modelField) : null;
        const note = document.getElementById("ttsVoiceNote");
        if (voiceSelect) voiceSelect.innerHTML = `<option>Loading…</option>`;
        try {
            const result = await api(`/api/tts/voices?provider=${encodeURIComponent(providerId)}${refresh ? "&refresh=1" : ""}`);
            if (voiceSelect) {
                const groups = {};
                (result.voices.items || []).forEach((v) => (groups[v.group || "Voices"] = groups[v.group || "Voices"] || []).push(v));
                voiceSelect.innerHTML = Object.entries(groups)
                    .map(([g, items]) => `<optgroup label="${escapeHtml(g)}">${items.map((v) => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.label)}${v.note ? " · " + escapeHtml(v.note) : ""}</option>`).join("")}</optgroup>`)
                    .join("");
                const stored = SETTINGS[spec.voiceField];
                if (stored && voiceSelect.querySelector(`option[value="${CSS.escape(stored)}"]`)) voiceSelect.value = stored;
            }
            if (note) {
                const n = result.voices.items?.length || 0;
                note.textContent = result.voices.source === "live" ? `${n} voices, live from provider` : result.voices.source === "cache" ? `${n} voices (cached)` : `${n} built-in voices${result.voices.error ? " — " + result.voices.error : ""}`;
            }
            if (modelSelect) {
                const datalist = document.getElementById(`${spec.modelField}_options`);
                if (datalist) {
                    datalist.innerHTML = (result.models.items || [])
                        .map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`)
                        .join("");
                }
                const storedModel = SETTINGS[spec.modelField];
                if (storedModel) modelSelect.value = storedModel;
                else if (!modelSelect.value && result.models.items?.length) modelSelect.value = result.models.items[0].id;
                const modelNote = document.getElementById("ttsModelNote");
                if (modelNote) {
                    const n = result.models.items?.length || 0;
                    modelNote.textContent = `${n} suggested models -- you can also type any model id this provider supports.`;
                }
            }
        } catch (err) {
            if (voiceSelect) voiceSelect.innerHTML = `<option>Could not load voices</option>`;
            toast(`Voice list failed: ${err.message}`, "warn");
        }
    }

    async function renderTtsPanel() {
        const providerId = $("#tts_service")?.value;
        const spec = ttsSpec(providerId);
        const panel = $("#ttsProviderPanel");
        if (!spec || !panel) return;
        panel.innerHTML = ttsPanelHtml(spec);
        [spec.keyField, spec.modelField, ...(spec.extraFields || []).map((f) => f.field)].filter(Boolean).forEach((field) => {
            if (SETTINGS[field] !== undefined) {
                const el = document.getElementById(field);
                if (el) el.value = SETTINGS[field];
            }
        });
        ["gemini_voice_style", "gemini_voice_accent", "gemini_voice_pace", "gemini_voice_energy",
         "gemini_voice_warmth", "gemini_voice_instruction", "gemini_pronunciations",
         "edge_rate_slider", "edge_pitch_slider"].forEach((id) => {
            const el = document.getElementById(id);
            if (el && SETTINGS[id.replace("_slider", "")] !== undefined) el.value = SETTINGS[id.replace("_slider", "")];
        });
        window.AF_SETTINGS.updateEdgeSliders();
        panel.querySelector("[data-refresh-voices]")?.addEventListener("click", () => loadTtsVoiceAndModel(providerId, true));
        panel.querySelector("#ttsPreviewBtn")?.addEventListener("click", (e) => previewTts(e.currentTarget, spec));
        panel.querySelector("#ttsTestBtn")?.addEventListener("click", (e) => testTtsConnection(e.currentTarget, spec));
        if (!spec.custom) await loadTtsVoiceAndModel(providerId, false);
    }

    function updateEdgeSliders() {
        const rateSlider = document.getElementById("edge_rate_slider");
        const pitchSlider = document.getElementById("edge_pitch_slider");
        if (rateSlider) {
            const value = `${rateSlider.value >= 0 ? "+" : ""}${rateSlider.value}%`;
            document.getElementById("edge_rate").value = value;
            document.getElementById("rate_val").textContent = value;
        }
        if (pitchSlider) {
            const value = `${pitchSlider.value >= 0 ? "+" : ""}${pitchSlider.value}Hz`;
            document.getElementById("edge_pitch").value = value;
            document.getElementById("pitch_val").textContent = value;
        }
    }

    async function previewTts(button, spec) {
        const payload = { service: spec.id };
        if (spec.keyField) payload[spec.keyField] = document.getElementById(spec.keyField)?.value;
        if (spec.voiceField) {
            payload[spec.voiceField] = document.getElementById(spec.voiceField)?.value;
            payload.voice = payload[spec.voiceField];
        }
        if (spec.modelField) payload[spec.modelField] = document.getElementById(spec.modelField)?.value;
        (spec.extraFields || []).forEach((f) => { payload[f.field] = document.getElementById(f.field)?.value; });
        if (spec.supportsRate) payload.edge_rate = document.getElementById("edge_rate")?.value;
        if (spec.supportsPitch) payload.edge_pitch = document.getElementById("edge_pitch")?.value;
        if (spec.director) {
            ["gemini_voice_style", "gemini_voice_pace", "gemini_voice_energy", "gemini_voice_warmth",
             "gemini_voice_accent", "gemini_voice_instruction", "gemini_pronunciations"].forEach((id) => {
                const el = document.getElementById(id);
                if (el) payload[id] = el.value;
            });
        }
        await withBusy(button, "Synthesizing", async () => {
            try {
                playPreview(await runPreview(payload));
            } catch (err) {
                toast(`Preview error: ${err.message}`, "error", 5000);
            }
        });
    }

    async function testTtsConnection(button, spec) {
        const payload = { service: spec.id };
        if (spec.custom) {
            // Saved custom provider -- the server resolves the spec (with
            // its stored key) from settings by id.
        } else {
            if (spec.keyField) payload[spec.keyField] = document.getElementById(spec.keyField)?.value;
            if (spec.modelField) payload[spec.modelField] = document.getElementById(spec.modelField)?.value;
            (spec.extraFields || []).forEach((f) => { payload[f.field] = document.getElementById(f.field)?.value; });
            if (spec.director) {
                ["gemini_voice_style", "gemini_voice_pace", "gemini_voice_energy", "gemini_voice_warmth",
                 "gemini_voice_accent", "gemini_voice_instruction", "gemini_pronunciations"].forEach((id) => {
                    const el = document.getElementById(id);
                    if (el) payload[id] = el.value;
                });
            }
        }
        const resultBox = document.getElementById("ttsTestResult");
        await withBusy(button, "Testing", async () => {
            try {
                const result = await api("/api/tts/test", { body: payload });
                if (resultBox) {
                    resultBox.innerHTML = `<div class="flex items-center gap-2">
                        <span class="badge ${result.success ? "badge-ok" : "badge-error"}">${result.success ? "CONNECTED" : "FAILED"}</span>
                        <span class="text-[12px]" style="color:var(--text-faint)">${result.success ? `${result.ms}ms` : escapeHtml(result.error || "Unknown error")}</span>
                    </div>`;
                }
                toast(result.success ? `Connected (${result.ms}ms)` : `Test failed: ${result.error}`, result.success ? "ok" : "error", 5000);
            } catch (err) {
                if (resultBox) resultBox.innerHTML = `<p style="color:var(--danger-600)">${escapeHtml(err.message)}</p>`;
                toast(`Test failed: ${err.message}`, "error", 5000);
            }
        });
    }

    /* ------------------------------------------------------- custom providers --- */

    let CUSTOM_TTS = [];

    function customProviderId(specId) {
        return String(specId || "").startsWith("custom:") ? specId.slice(7) : null;
    }

    function slugify(text) {
        return String(text || "")
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/(^-|-$)/g, "")
            .slice(0, 40) || `provider-${Date.now().toString(36)}`;
    }

    function renderCustomTtsRows() {
        const box = $("#customTtsRows");
        if (!box) return;
        box.innerHTML = CUSTOM_TTS.map(
            (row, index) => `
            <details class="card" data-index="${index}" ${row._open ? "open" : ""}>
                <summary class="card-pad" style="cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:8px">
                    <span class="text-[13px] font-semibold" style="color:var(--text)">${escapeHtml(row.label || row.id || "New provider")}</span>
                    <span class="hint">${escapeHtml(row.endpoint || "no endpoint set")}</span>
                </summary>
                <div class="card-pad pt-0 space-y-2">
                    <div class="grid grid-cols-2 gap-2">
                        <label class="field" style="margin:0"><span class="label">Name</span>
                            <input type="text" class="ct-label" value="${escapeHtml(row.label || "")}" placeholder="My TTS API"></label>
                        <label class="field" style="margin:0"><span class="label">API Key</span>
                            <input type="password" class="ct-api_key" value="" placeholder="${row._hasKey ? "Saved (leave blank to keep)" : "API key"}"></label>
                    </div>
                    <label class="field" style="margin:0"><span class="label">Endpoint URL</span>
                        <input type="text" class="ct-endpoint" value="${escapeHtml(row.endpoint || "")}" placeholder="https://api.example.com/v1/tts"></label>
                    <div class="grid grid-cols-3 gap-2">
                        <label class="field" style="margin:0"><span class="label">Auth Header</span>
                            <input type="text" class="ct-auth_header" value="${escapeHtml(row.auth_header || "Authorization")}"></label>
                        <label class="field" style="margin:0"><span class="label">Auth Scheme</span>
                            <input type="text" class="ct-auth_scheme" value="${escapeHtml(row.auth_scheme ?? "Bearer")}" placeholder="Bearer (blank = none)"></label>
                        <label class="field" style="margin:0"><span class="label">JSON Text Field</span>
                            <input type="text" class="ct-text_field" value="${escapeHtml(row.text_field || "text")}"></label>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <label class="field" style="margin:0"><span class="label">Voice ID</span>
                            <input type="text" class="ct-voice_id" value="${escapeHtml(row.voice_id || "")}"></label>
                        <label class="field" style="margin:0"><span class="label">Model ID (optional)</span>
                            <input type="text" class="ct-model_id" value="${escapeHtml(row.model_id || "")}"></label>
                    </div>
                    <div class="flex gap-2 justify-between items-center pt-1">
                        <div id="ct-test-result-${index}" class="text-[12px]"></div>
                        <div class="flex gap-2">
                            <button type="button" class="btn btn-sm" data-ct-test="${index}">Test Connection</button>
                            <button type="button" class="btn btn-danger btn-sm" data-ct-remove="${index}">Remove</button>
                        </div>
                    </div>
                </div>
            </details>`
        ).join("") || `<p class="hint">No custom providers yet.</p>`;

        box.querySelectorAll("[data-ct-remove]").forEach((btn) =>
            btn.addEventListener("click", () => {
                CUSTOM_TTS.splice(Number(btn.dataset.ctRemove), 1);
                renderCustomTtsRows();
            })
        );
        box.querySelectorAll("[data-ct-test]").forEach((btn) =>
            btn.addEventListener("click", () => testCustomProviderDraft(btn, Number(btn.dataset.ctTest)))
        );
        box.querySelectorAll("details").forEach((details) => {
            const index = Number(details.dataset.index);
            details.querySelectorAll("input").forEach((input) => {
                const field = [...input.classList].find((c) => c.startsWith("ct-"))?.slice(3);
                if (!field) return;
                input.addEventListener("input", () => {
                    CUSTOM_TTS[index][field] = input.value;
                    if (field === "api_key" && input.value) CUSTOM_TTS[index]._hasKey = true;
                    if (field === "label") {
                        details.querySelector("summary span").textContent = input.value || "New provider";
                    }
                });
            });
        });
    }

    function collectCustomTtsRow(index) {
        const row = CUSTOM_TTS[index];
        return {
            id: row.id || slugify(row.label),
            label: row.label || row.id,
            endpoint: row.endpoint || "",
            api_key: row.api_key || "",
            auth_header: row.auth_header || "Authorization",
            auth_scheme: row.auth_scheme ?? "Bearer",
            voice_id: row.voice_id || "",
            model_id: row.model_id || "",
            text_field: row.text_field || "text",
        };
    }

    async function testCustomProviderDraft(button, index) {
        const spec = collectCustomTtsRow(index);
        const resultBox = document.getElementById(`ct-test-result-${index}`);
        await withBusy(button, "Testing", async () => {
            try {
                const result = await api("/api/tts/test", { body: { custom_spec: spec } });
                if (resultBox) {
                    resultBox.innerHTML = `<span class="badge ${result.success ? "badge-ok" : "badge-error"}">${result.success ? `CONNECTED · ${result.ms}ms` : "FAILED"}</span>`;
                }
                if (!result.success) toast(`Test failed: ${result.error}`, "error", 5000);
            } catch (err) {
                if (resultBox) resultBox.innerHTML = `<span class="badge badge-error">FAILED</span>`;
                toast(`Test failed: ${err.message}`, "error", 5000);
            }
        });
    }

    function customTtsSavePayload() {
        return CUSTOM_TTS.map((_, index) => collectCustomTtsRow(index)).filter((row) => row.id);
    }

    /* ------------------------------------------------------- partner tags --- */

    let PARTNER_TAGS = [];

    function renderPartnerTagRows() {
        const box = $("#partnerTagRows");
        if (!box) return;
        box.innerHTML = PARTNER_TAGS.map(
            (row, index) => `
            <div class="flex gap-2 items-center" data-index="${index}">
                <input type="text" class="pt-label" style="max-width:40%" value="${escapeHtml(row.label || "")}" placeholder="Label (e.g. Main Site)">
                <input type="text" class="pt-tag flex-1 mono" value="${escapeHtml(row.tag || "")}" placeholder="your-tag-20">
                <button type="button" class="btn btn-sm" data-apply-tag="${index}">Use</button>
                <button type="button" class="btn btn-danger btn-icon" data-remove-tag="${index}" aria-label="Remove">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg>
                </button>
            </div>`
        ).join("") || `<p class="hint">No saved tags yet.</p>`;

        box.querySelectorAll("[data-index]").forEach((row) => {
            const index = Number(row.dataset.index);
            row.querySelector(".pt-label").addEventListener("input", (e) => { PARTNER_TAGS[index].label = e.target.value; });
            row.querySelector(".pt-tag").addEventListener("input", (e) => { PARTNER_TAGS[index].tag = e.target.value; });
        });
        box.querySelectorAll("[data-apply-tag]").forEach((btn) =>
            btn.addEventListener("click", () => {
                const el = $("#partner_tag");
                if (el) el.value = PARTNER_TAGS[Number(btn.dataset.applyTag)].tag;
                toast("Applied -- remember to Save Changes", "ok");
            })
        );
        box.querySelectorAll("[data-remove-tag]").forEach((btn) =>
            btn.addEventListener("click", () => {
                PARTNER_TAGS.splice(Number(btn.dataset.removeTag), 1);
                renderPartnerTagRows();
            })
        );
    }

    function partnerTagsSavePayload() {
        return PARTNER_TAGS.map((row) => ({
            id: row.id || row.tag,
            label: row.label || row.tag,
            tag: row.tag,
        })).filter((row) => row.tag);
    }

    /* --------------------------------------------------- llm model presets --- */

    let LLM_MODEL_PRESETS = [];

    function renderLlmModelPresetRows() {
        const box = $("#llmModelPresetRows");
        if (!box) return;
        box.innerHTML = LLM_MODEL_PRESETS.map(
            (row, index) => `
            <div class="flex gap-2 items-center" data-index="${index}">
                <input type="text" class="mp-label" style="max-width:34%" value="${escapeHtml(row.label || "")}" placeholder="Label (e.g. Fast/Cheap)">
                <select class="mp-provider" style="max-width:28%">
                    ${LLM_REGISTRY.map((p) => `<option value="${p.id}" ${p.id === row.provider ? "selected" : ""}>${escapeHtml(p.label)}</option>`).join("")}
                </select>
                <input type="text" class="mp-model flex-1 mono" value="${escapeHtml(row.model || "")}" placeholder="model id">
                <button type="button" class="btn btn-sm" data-apply-preset="${index}">Use</button>
                <button type="button" class="btn btn-danger btn-icon" data-remove-preset="${index}" aria-label="Remove">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg>
                </button>
            </div>`
        ).join("") || `<p class="hint">No saved presets yet.</p>`;

        box.querySelectorAll("[data-index]").forEach((row) => {
            const index = Number(row.dataset.index);
            row.querySelector(".mp-label").addEventListener("input", (e) => { LLM_MODEL_PRESETS[index].label = e.target.value; });
            row.querySelector(".mp-provider").addEventListener("change", (e) => { LLM_MODEL_PRESETS[index].provider = e.target.value; });
            row.querySelector(".mp-model").addEventListener("input", (e) => { LLM_MODEL_PRESETS[index].model = e.target.value; });
        });
        box.querySelectorAll("[data-apply-preset]").forEach((btn) =>
            btn.addEventListener("click", async () => {
                const preset = LLM_MODEL_PRESETS[Number(btn.dataset.applyPreset)];
                const providerSelect = $("#llm_service");
                if (providerSelect) {
                    providerSelect.value = preset.provider;
                    await renderLlmPanel();
                    const modelField = llmSpec(preset.provider)?.modelField;
                    if (modelField) {
                        const modelEl = document.getElementById(modelField);
                        if (modelEl) modelEl.value = preset.model;
                    }
                }
                toast("Applied -- remember to Save Changes", "ok");
            })
        );
        box.querySelectorAll("[data-remove-preset]").forEach((btn) =>
            btn.addEventListener("click", () => {
                LLM_MODEL_PRESETS.splice(Number(btn.dataset.removePreset), 1);
                renderLlmModelPresetRows();
            })
        );
    }

    function llmModelPresetsSavePayload() {
        return LLM_MODEL_PRESETS.map((row) => ({
            id: row.id || `${row.provider}:${row.model}`,
            label: row.label || `${row.provider} / ${row.model}`,
            provider: row.provider,
            model: row.model,
        })).filter((row) => row.provider && row.model);
    }

    /* --------------------------------------------------------- transitions --- */

    function renderTransitions() {
        const container = $("#transitions_container");
        const list = window.__AF_TRANSITIONS || [];
        if (!container) return;
        const active = new Set((SETTINGS.active_transitions || list).map(String));
        container.innerHTML = list
            .map(
                (t) => `<label class="badge badge-neutral" style="cursor:pointer">
                <input type="checkbox" class="transition-cb sr-only" value="${t}" ${active.has(t) ? "checked" : ""}>
                ${escapeHtml(t)}
            </label>`
            )
            .join("");
        container.querySelectorAll(".transition-cb").forEach((cb) => {
            const sync = () => cb.closest("label").classList.toggle("badge-brand", cb.checked);
            cb.addEventListener("change", sync);
            sync();
        });
    }

    function selectedTransitions() {
        return $$(".transition-cb").filter((cb) => cb.checked).map((cb) => cb.value);
    }

    function selectTransitions(mode) {
        const boxes = $$(".transition-cb");
        if (mode === "all") boxes.forEach((cb) => (cb.checked = true));
        else if (mode === "none") boxes.forEach((cb) => (cb.checked = false));
        else if (mode === "random") {
            const shuffled = [...boxes].sort(() => Math.random() - 0.5);
            boxes.forEach((cb) => (cb.checked = false));
            shuffled.slice(0, 10).forEach((cb) => (cb.checked = true));
        }
        boxes.forEach((cb) => cb.dispatchEvent(new Event("change")));
    }

    /* ---------------------------------------------------------------- boot --- */

    document.addEventListener("DOMContentLoaded", async () => {
        LLM_REGISTRY = window.__AF_LLM_PROVIDERS || [];
        TTS_REGISTRY = window.__AF_TTS_REGISTRY || [];
        DIRECTOR_OPTIONS = window.__AF_DIRECTOR_OPTIONS || DIRECTOR_OPTIONS;
        CUSTOM_TTS = (window.__AF_CUSTOM_TTS_PROVIDERS || []).map((row) => ({
            ...row,
            _hasKey: !!row.has_api_key,
            api_key: "",
        }));
        renderCustomTtsRows();
        $("#customTtsAddBtn")?.addEventListener("click", () => {
            CUSTOM_TTS.push({ id: "", label: "", endpoint: "", api_key: "", auth_header: "Authorization", auth_scheme: "Bearer", voice_id: "", model_id: "", text_field: "text", _open: true });
            renderCustomTtsRows();
        });

        try {
            await loadSettings();
        } catch (err) {
            // A transient /get_settings failure must not leave the whole page
            // inert -- render every panel with empty defaults and surface the
            // problem instead of silently stopping the boot sequence here.
            toast(`Could not load settings: ${err.message}`, "error", 6000);
        }

        renderLlmProviderSelect();
        const llmSelect = $("#llm_service");
        if (SETTINGS.llm_service) llmSelect.value = SETTINGS.llm_service;
        llmSelect.addEventListener("change", renderLlmPanel);
        await renderLlmPanel();
        initChainBuilder();

        LLM_MODEL_PRESETS = window.__AF_LLM_MODEL_PRESETS || [];
        renderLlmModelPresetRows();
        $("#llmModelPresetAddBtn")?.addEventListener("click", () => {
            const spec = llmSpec($("#llm_service")?.value);
            const model = spec?.modelField ? document.getElementById(spec.modelField)?.value : "";
            if (!spec || !model) {
                toast("Pick a provider and model above first", "warn");
                return;
            }
            LLM_MODEL_PRESETS.push({ id: "", label: "", provider: spec.id, model });
            renderLlmModelPresetRows();
        });

        renderTtsProviderSelect();
        const ttsSelect = $("#tts_service");
        if (SETTINGS.tts_service) ttsSelect.value = SETTINGS.tts_service;
        ttsSelect.addEventListener("change", renderTtsPanel);
        await renderTtsPanel();

        PARTNER_TAGS = window.__AF_PARTNER_TAGS || [];
        renderPartnerTagRows();
        $("#partnerTagAddBtn")?.addEventListener("click", () => {
            const tag = $("#partner_tag")?.value.trim();
            if (!tag) {
                toast("Type a partner tag above first", "warn");
                return;
            }
            if (PARTNER_TAGS.some((row) => row.tag === tag)) {
                toast("This tag is already saved", "warn");
                return;
            }
            PARTNER_TAGS.push({ id: "", label: "", tag });
            renderPartnerTagRows();
        });

        renderTransitions();

        $("#saveChangesBtn")?.addEventListener("click", async () => {
            const rows = $$(".chain-row").map((row) => ({
                provider: row.querySelector(".chain-provider").value,
                model: row.querySelector(".chain-model").value,
            }));
            syncChainHidden(rows);
            const activeTransitions = selectedTransitions();
            const customProviders = customTtsSavePayload();
            const partnerTags = partnerTagsSavePayload();
            const modelPresets = llmModelPresetsSavePayload();
            try {
                await api("/save_settings", {
                    body: {
                        ...collectSettings(),
                        active_transitions: activeTransitions,
                        custom_tts_providers: customProviders,
                        partner_tags: partnerTags,
                        llm_model_presets: modelPresets,
                    },
                });
                toast("Settings saved", "ok");
                // Stamp real ids back onto any brand-new rows and refresh the
                // TTS provider dropdown so a newly added custom provider is
                // selectable immediately, no reload needed.
                CUSTOM_TTS = customProviders.map((row) => ({ ...row, _hasKey: !!row.api_key, api_key: "" }));
                renderCustomTtsRows();
                const currentProvider = $("#tts_service")?.value;
                TTS_REGISTRY = TTS_REGISTRY.filter((p) => !p.custom).concat(
                    customProviders.map((row) => ({
                        id: `custom:${row.id}`, label: `Custom: ${row.label || row.id}`,
                        blurb: row.endpoint, needsKey: true, keyField: null, voiceField: null,
                        modelField: null, hasVoiceCatalog: false, hasModelCatalog: false,
                        supportsRate: false, supportsPitch: false, director: false, paid: true, custom: true,
                    }))
                );
                renderTtsProviderSelect();
                if (currentProvider) $("#tts_service").value = currentProvider;
                PARTNER_TAGS = partnerTags;
                renderPartnerTagRows();
                LLM_MODEL_PRESETS = modelPresets;
                renderLlmModelPresetRows();
                window.__AF_PARTNER_TAGS = partnerTags;
            } catch (err) {
                toast(`Save failed: ${err.message}`, "error", 6000);
            }
        });
        $("#testLlmBtn")?.addEventListener("click", testLLM);
        $$("[data-select-transitions]").forEach((btn) => btn.addEventListener("click", () => selectTransitions(btn.dataset.selectTransitions)));
    });

    window.AF_SETTINGS = { updateEdgeSliders };
})();
