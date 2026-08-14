/* AmzFlow AI — shared logic for the two creation modules (/create/url and
 * /create/keywords) plus the render-options panel they both include.
 *
 * Sections below are guarded by element presence, so this one file works on
 * either page without a second copy drifting out of sync (the failure mode
 * that made v6's editor TTS dropdown offer three providers it couldn't
 * actually configure).
 */
(function () {
    "use strict";
    const { $, $$, api, toast, modal, escapeHtml, poll, withBusy, debounce, previewVoice: runPreview, playPreview } = window.AF;

    /* ============================================================ settings === */

    let SAVE_TIMER = null;

    function collectSettings() {
        const settings = {};
        $$("input, textarea, select").forEach((el) => {
            if (!el.id || el.dataset.setting === "false") return;
            settings[el.id] = el.type === "checkbox" ? el.checked : el.value;
        });
        return settings;
    }

    async function saveSettings(silent = false) {
        try {
            await api("/save_settings", { body: collectSettings() });
            if (!silent) toast("Saved", "ok", 1400);
            return true;
        } catch (err) {
            // `silent` only suppresses the success toast (used by autosave,
            // which fires on every keystroke/change) -- a failure is always
            // worth surfacing, or it silently stops autosave from ever
            // persisting again with no visible signal why.
            toast(`Save failed: ${err.message}`, "error", 6000);
            saveSettings.lastError = err.message;
            return false;
        }
    }

    const autosave = debounce(() => saveSettings(true), 500);

    async function loadSettings() {
        const data = await api("/get_settings");
        for (const key in data) {
            const el = document.getElementById(key);
            if (!el) continue;
            if (el.type === "checkbox") el.checked = !!data[key];
            else el.value = data[key];
        }
        return data;
    }

    /* ==================================================== render options === */

    let TTS_REGISTRY = [];
    let DIRECTOR_OPTIONS = { styles: [], accents: [] };

    function registryFor(providerId) {
        return TTS_REGISTRY.find((p) => p.id === providerId);
    }

    function renderProviderSelect() {
        const select = $("#tts_service");
        if (!select) return;
        select.innerHTML = TTS_REGISTRY.map(
            (p) => `<option value="${p.id}">${escapeHtml(p.label)}</option>`
        ).join("");
    }

    async function loadVoiceOptions(providerId, { refresh = false } = {}) {
        const select = $("#voice_select");
        const spec = registryFor(providerId);
        if (!select || !spec) return;
        select.disabled = true;
        select.innerHTML = `<option>Loading voices…</option>`;
        try {
            const result = await api(`/api/tts/voices?provider=${encodeURIComponent(providerId)}${refresh ? "&refresh=1" : ""}`);
            const voices = result.voices.items || [];
            const groups = {};
            voices.forEach((voice) => {
                const key = voice.group || "Voices";
                (groups[key] = groups[key] || []).push(voice);
            });
            select.innerHTML = Object.entries(groups)
                .map(
                    ([group, items]) =>
                        `<optgroup label="${escapeHtml(group)}">` +
                        items
                            .map(
                                (voice) =>
                                    `<option value="${escapeHtml(voice.id)}">${escapeHtml(voice.label)}${voice.note ? " · " + escapeHtml(voice.note) : ""}</option>`
                            )
                            .join("") +
                        `</optgroup>`
                )
                .join("");
            const field = spec.voiceField;
            const stored = field ? window.__AF_SETTINGS?.[field] : null;
            if (stored && select.querySelector(`option[value="${CSS.escape(stored)}"]`)) {
                select.value = stored;
            }
            const sourceNote = $("#voiceSourceNote");
            if (sourceNote) {
                sourceNote.textContent =
                    result.voices.source === "live"
                        ? `${voices.length} voices, live from provider`
                        : result.voices.source === "cache"
                        ? `${voices.length} voices (cached)`
                        : `${voices.length} built-in voices${result.voices.error ? " — " + result.voices.error : ""}`;
            }
        } catch (err) {
            select.innerHTML = `<option>Could not load voices</option>`;
            toast(`Voice list failed: ${err.message}`, "warn");
        } finally {
            select.disabled = false;
        }
    }

    function renderDirectorControls(spec) {
        const wrap = $("#directorControls");
        if (!wrap) return;
        wrap.classList.toggle("hidden", !spec?.director);
        if (!spec?.director) return;
        if (wrap.dataset.built === "1") return;
        wrap.dataset.built = "1";
        wrap.innerHTML = `
            <div class="grid grid-cols-2 gap-3">
                <label class="field"><span class="label">Style</span>
                    <select id="gemini_voice_style" onchange="AF_CREATION.autosave()">
                        ${DIRECTOR_OPTIONS.styles.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("")}
                    </select>
                </label>
                <label class="field"><span class="label">Accent</span>
                    <select id="gemini_voice_accent" onchange="AF_CREATION.autosave()">
                        ${DIRECTOR_OPTIONS.accents.map((a) => `<option value="${a.id}">${escapeHtml(a.label)}</option>`).join("")}
                    </select>
                </label>
            </div>
            <div class="grid grid-cols-3 gap-3">
                <label class="field"><span class="label">Pace <b id="gemini_pace_out">50</b></span>
                    <input id="gemini_voice_pace" type="range" min="0" max="100" value="50" oninput="document.getElementById('gemini_pace_out').textContent=this.value" onchange="AF_CREATION.autosave()"></label>
                <label class="field"><span class="label">Energy <b id="gemini_energy_out">45</b></span>
                    <input id="gemini_voice_energy" type="range" min="0" max="100" value="45" oninput="document.getElementById('gemini_energy_out').textContent=this.value" onchange="AF_CREATION.autosave()"></label>
                <label class="field"><span class="label">Warmth <b id="gemini_warmth_out">60</b></span>
                    <input id="gemini_voice_warmth" type="range" min="0" max="100" value="60" oninput="document.getElementById('gemini_warmth_out').textContent=this.value" onchange="AF_CREATION.autosave()"></label>
            </div>
            <label class="field"><span class="label">Director instruction</span>
                <textarea id="gemini_voice_instruction" rows="2" maxlength="500" placeholder="Pause briefly before the final verdict." onchange="AF_CREATION.autosave()"></textarea></label>`;
    }

    function renderRatePitch(spec) {
        const wrap = $("#ratePitchControls");
        if (!wrap) return;
        const show = spec && (spec.supportsRate || spec.supportsPitch);
        wrap.classList.toggle("hidden", !show);
        if (!show || wrap.dataset.built === "1") return;
        wrap.dataset.built = "1";
        wrap.innerHTML = `
            <div class="grid grid-cols-2 gap-3">
                <label class="field"><span class="label">Speed <b id="edge_rate_out">+0%</b></span>
                    <input id="edge_rate_slider" type="range" min="-50" max="50" value="0" step="5"
                        oninput="AF_CREATION.updateEdgeSliders()" onchange="AF_CREATION.autosave()">
                    <input type="hidden" id="edge_rate" value="+0%"></label>
                <label class="field"><span class="label">Pitch <b id="edge_pitch_out">+0Hz</b></span>
                    <input id="edge_pitch_slider" type="range" min="-20" max="20" value="0" step="1"
                        oninput="AF_CREATION.updateEdgeSliders()" onchange="AF_CREATION.autosave()">
                    <input type="hidden" id="edge_pitch" value="+0Hz"></label>
            </div>`;
    }

    function updateEdgeSliders() {
        const rateSlider = $("#edge_rate_slider");
        const pitchSlider = $("#edge_pitch_slider");
        if (rateSlider) {
            const value = `${rateSlider.value >= 0 ? "+" : ""}${rateSlider.value}%`;
            $("#edge_rate").value = value;
            $("#edge_rate_out").textContent = value;
        }
        if (pitchSlider) {
            const value = `${pitchSlider.value >= 0 ? "+" : ""}${pitchSlider.value}Hz`;
            $("#edge_pitch").value = value;
            $("#edge_pitch_out").textContent = value;
        }
    }

    // #voice_select is a UI-only proxy (data-setting=false): its own id isn't
    // a real setting name, since the actual field differs per provider
    // (edge_voice, kokoro_voice, gemini_tts_voice, elevenlabs_voice_id, ...).
    // This mirrors its value into a hidden input whose id is re-pointed at
    // that real field, so collectSettings() actually persists the choice --
    // previously picking a voice here only affected the Preview button and
    // never changed which voice the real render used.
    function syncVoiceHiddenField(spec) {
        const hidden = document.querySelector('[data-role="voice-hidden-field"]');
        if (!hidden) return;
        if (spec?.voiceField) {
            hidden.id = spec.voiceField;
            hidden.value = $("#voice_select")?.value || "";
        } else {
            hidden.removeAttribute("id");
            hidden.value = "";
        }
    }

    async function onProviderChange(refresh = false) {
        const providerId = $("#tts_service")?.value;
        const spec = registryFor(providerId);
        if (!spec) return;
        const keyField = $("#voiceKeyField");
        if (keyField) keyField.classList.toggle("hidden", !spec.needsKey);
        renderRatePitch(spec);
        renderDirectorControls(spec);
        await loadVoiceOptions(providerId, { refresh });
        syncVoiceHiddenField(spec);
        autosave();
    }

    async function initRenderOptions() {
        const panel = $("#renderOptionsPanel");
        if (!panel || !window.__AF_TTS_REGISTRY) return;
        TTS_REGISTRY = window.__AF_TTS_REGISTRY;
        DIRECTOR_OPTIONS = window.__AF_DIRECTOR_OPTIONS || DIRECTOR_OPTIONS;
        renderProviderSelect();
        const select = $("#tts_service");
        const settings = window.__AF_SETTINGS || {};
        if (settings.tts_service) select.value = settings.tts_service;
        select.addEventListener("change", () => onProviderChange(false));
        await onProviderChange(false);

        const speedSlider = $("#video_speed");
        const speedOut = $("#video_speed_out");
        if (speedSlider && speedOut) {
            const sync = () => {
                speedOut.textContent = `${Number(speedSlider.value).toFixed(2)}x`;
            };
            speedSlider.addEventListener("input", sync);
            sync();
        }

        $("#voiceRefreshBtn")?.addEventListener("click", () => onProviderChange(true));
        $("#previewVoiceBtn")?.addEventListener("click", (event) => previewVoice(event.currentTarget));
        $("#voice_select")?.addEventListener("change", () => {
            syncVoiceHiddenField(registryFor($("#tts_service")?.value));
            autosave();
        });

        initPartnerTagPicker();
    }

    /* --------------------------------------------------- partner tag presets --- */

    function renderPartnerTagOptions() {
        const select = $("#partnerTagPreset");
        if (!select) return;
        const tags = window.__AF_PARTNER_TAGS || [];
        select.innerHTML =
            `<option value="">Saved tags…</option>` +
            tags.map((row) => `<option value="${escapeHtml(row.tag)}">${escapeHtml(row.label)}</option>`).join("");
    }

    function initPartnerTagPicker() {
        const select = $("#partnerTagPreset");
        const input = $("#partner_tag");
        const saveBtn = $("#savePartnerTagBtn");
        if (!select || !input) return;
        renderPartnerTagOptions();
        select.addEventListener("change", () => {
            if (select.value) {
                input.value = select.value;
                autosave();
            }
        });
        saveBtn?.addEventListener("click", async () => {
            const tag = input.value.trim();
            if (!tag) {
                toast("Type a tag first", "warn");
                return;
            }
            const tags = window.__AF_PARTNER_TAGS || [];
            if (tags.some((row) => row.tag === tag)) {
                toast("This tag is already saved", "warn");
                return;
            }
            const label = window.prompt("Name for this tag preset (e.g. \"Main Site\")", tag);
            if (label === null) return;
            const updated = [...tags, { id: tag, label: label.trim() || tag, tag }];
            await withBusy(saveBtn, "", async () => {
                try {
                    await api("/save_settings", { body: { partner_tags: updated } });
                    window.__AF_PARTNER_TAGS = updated;
                    renderPartnerTagOptions();
                    select.value = tag;
                    toast("Tag preset saved", "ok");
                } catch (err) {
                    toast(`Save failed: ${err.message}`, "error", 5000);
                }
            });
        });
    }

    /* ------------------------------------------------------- voice preview --- */

    async function previewVoice(button) {
        const providerId = $("#tts_service")?.value;
        const spec = registryFor(providerId);
        if (!spec) return;
        const payload = { service: providerId, text: $("#previewText")?.value || undefined };
        if (spec.voiceField) payload[spec.voiceField] = $("#voice_select")?.value;
        payload.voice = $("#voice_select")?.value;
        if (spec.supportsRate) payload.edge_rate = $("#edge_rate")?.value;
        if (spec.supportsPitch) payload.edge_pitch = $("#edge_pitch")?.value;
        if (spec.director) {
            ["gemini_tts_model", "gemini_voice_style", "gemini_voice_pace", "gemini_voice_energy",
             "gemini_voice_warmth", "gemini_voice_accent", "gemini_voice_instruction"].forEach((id) => {
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

    /* ============================================================ module 1 === */

    let currentContentBatch = null;
    let contentBatchPollTimer = null;
    const selectedJobIds = new Set();

    function updateContentUrlCount() {
        const textarea = $("#content_urls");
        const countEl = $("#contentUrlCount");
        const btn = $("#analyzeContentBtn");
        if (!textarea || !countEl) return;
        const urls = textarea.value.split("\n").map((v) => v.trim()).filter(Boolean);
        countEl.textContent = `${urls.length} / 20`;
        if (btn) btn.disabled = urls.length < 1 || urls.length > 20;
    }

    function showContentBatchError(message = "") {
        const box = $("#contentBatchError");
        if (!box) return;
        box.textContent = message;
        box.classList.toggle("hidden", !message);
    }

    async function analyzeContentUrls() {
        const urls = $("#content_urls").value.split("\n").map((v) => v.trim()).filter(Boolean);
        const button = $("#analyzeContentBtn");
        showContentBatchError();
        await withBusy(button, "Starting", async () => {
            try {
                const result = await api("/api/content-batches", { body: { urls } });
                currentContentBatch = result.data;
                renderContentBatch(currentContentBatch);
                scheduleContentBatchPoll();
            } catch (err) {
                showContentBatchError(err.message);
            }
        });
        updateContentUrlCount();
    }

    async function loadLatestContentBatch() {
        try {
            // active=1: skip batches that are fully generated already, so a
            // completed batch doesn't reappear as "still needs review" on
            // every page load/reload.
            const result = await api("/api/content-batches?limit=1&active=1");
            if (!result.data.length) return;
            currentContentBatch = result.data[0];
            renderContentBatch(currentContentBatch);
            scheduleContentBatchPoll();
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    async function refreshContentBatch() {
        if (!currentContentBatch?.batchId) return;
        try {
            const result = await api(`/api/content-batches/${currentContentBatch.batchId}`);
            currentContentBatch = result.data;
            renderContentBatch(currentContentBatch);
            scheduleContentBatchPoll();
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    function scheduleContentBatchPoll() {
        clearTimeout(contentBatchPollTimer);
        const active = currentContentBatch?.jobs?.some((job) =>
            ["QUEUED", "FETCHING", "EXTRACTING", "VALIDATING"].includes(job.status)
        );
        if (active) contentBatchPollTimer = setTimeout(refreshContentBatch, 1500);
    }

    const STATUS_BADGE = {
        READY: "badge-ok", NEEDS_ATTENTION: "badge-warn", FAILED: "badge-error",
        QUEUED: "badge-neutral", FETCHING: "badge-brand", EXTRACTING: "badge-brand", VALIDATING: "badge-brand",
    };

    function renderContentBatch(batch) {
        const table = $("#contentReviewTable");
        const actions = $("#contentReviewActions");
        const body = $("#contentReviewBody");
        const summary = $("#contentBatchSummary");
        if (!table || !body) return;
        body.replaceChildren();
        table.classList.remove("hidden");
        actions.classList.remove("hidden");

        const jobs = batch.jobs || [];
        const ready = jobs.filter((j) => j.status === "READY").length;
        const approved = jobs.filter((j) => j.isApproved).length;
        const processing = jobs.filter((j) => ["QUEUED", "FETCHING", "EXTRACTING", "VALIDATING"].includes(j.status)).length;
        summary.textContent = `${jobs.length} URLs · ${processing} processing · ${ready} ready · ${approved} approved`;

        // A row that no longer exists in this render can't stay "selected".
        const liveIds = new Set(jobs.map((j) => j.jobId));
        for (const id of [...selectedJobIds]) {
            if (!liveIds.has(id)) selectedJobIds.delete(id);
        }

        jobs.forEach((job) => {
            let host = job.sourceUrl;
            try { host = new URL(job.sourceUrl).hostname; } catch (_) {}
            const statusLabel = job.isApproved ? "APPROVED" : job.status;
            const badgeClass = job.isApproved ? "badge-ok" : STATUS_BADGE[job.status] || "badge-neutral";
            const row = document.createElement("tr");
            // FAILED rows only ever offered a "Retry" button with the actual
            // failure reason (job.error) stored server-side but never shown
            // anywhere -- a bad fetch/parse looked identical to "give up and
            // guess," with no way to tell why. Surface it directly.
            const statusCell = job.status === "FAILED" && job.error
                ? `<span class="badge ${badgeClass}" title="${escapeHtml(job.error)}" style="cursor:help">${escapeHtml(statusLabel)}</span>
                   <div class="text-[11px] mt-1" style="color:var(--danger-600);max-width:220px">${escapeHtml(job.error)}</div>`
                : `<span class="badge ${badgeClass}">${escapeHtml(statusLabel)}</span>`;
            row.innerHTML = `
                <td class="text-center"><input type="checkbox" class="job-select-checkbox" data-job-id="${job.jobId}" ${selectedJobIds.has(job.jobId) ? "checked" : ""} aria-label="Select this row"></td>
                <td class="truncate max-w-[170px]" title="${escapeHtml(host)}">${escapeHtml(host)}</td>
                <td class="font-medium" style="color:var(--text)">${escapeHtml(job.keyword || job.articleTitle || "Analyzing…")}</td>
                <td>${escapeHtml(job.contentType || "")}</td>
                <td class="text-center">${job.products?.length || 0}</td>
                <td class="text-center">${job.confidence || 0}%</td>
                <td>${escapeHtml(job.revenuePotential || "")}</td>
                <td>${statusCell}</td>
                <td class="text-right"></td>`;
            row.querySelector(".job-select-checkbox").addEventListener("change", (e) => {
                if (e.target.checked) selectedJobIds.add(job.jobId);
                else selectedJobIds.delete(job.jobId);
                updateSelectedJobsUI();
            });
            const actionCell = row.lastElementChild;
            const reviewBtn = document.createElement("button");
            reviewBtn.type = "button";
            reviewBtn.className = "btn btn-sm";
            reviewBtn.textContent = job.status === "FAILED" ? "Retry" : "Review";
            reviewBtn.disabled = !["READY", "NEEDS_ATTENTION", "FAILED"].includes(job.status);
            reviewBtn.addEventListener("click", () =>
                job.status === "FAILED" ? retryContentJob(job.jobId) : toggleContentReview(job.jobId)
            );
            actionCell.appendChild(reviewBtn);
            body.appendChild(row);

            const detailRow = document.createElement("tr");
            detailRow.id = `content-review-${job.jobId}`;
            detailRow.className = "hidden";
            detailRow.style.background = "var(--surface-2)";
            const detailCell = document.createElement("td");
            detailCell.colSpan = 9;
            detailCell.className = "p-4";
            detailCell.appendChild(buildContentReviewEditor(job));
            detailRow.appendChild(detailCell);
            body.appendChild(detailRow);
        });

        updateSelectedJobsUI();
        const selectAll = $("#selectAllJobsCheckbox");
        if (selectAll) {
            selectAll.checked = jobs.length > 0 && selectedJobIds.size === jobs.length;
            selectAll.indeterminate = selectedJobIds.size > 0 && selectedJobIds.size < jobs.length;
        }
    }

    function updateSelectedJobsUI() {
        const btn = $("#deleteSelectedJobsBtn");
        const count = $("#selectedJobsCount");
        if (btn) btn.disabled = selectedJobIds.size === 0;
        if (count) count.textContent = selectedJobIds.size ? `${selectedJobIds.size} selected` : "";
    }

    async function deleteSelectedJobs() {
        if (!selectedJobIds.size) return;
        const ids = [...selectedJobIds];
        const confirmed = await modal({
            title: "Delete selected rows?",
            message: `This removes ${ids.length} row(s) from the queue. Rows already generated into a video are not affected -- only the queue entry is deleted.`,
            kind: "warn",
            confirmText: "Delete",
            cancelText: "Cancel",
            danger: true,
        });
        if (!confirmed) return;
        try {
            await api("/api/content-jobs/bulk-delete", { body: { jobIds: ids } });
            selectedJobIds.clear();
            await refreshContentBatch();
            toast(`Deleted ${ids.length} row(s)`, "ok", 3000);
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    function buildContentReviewEditor(job) {
        const wrap = document.createElement("div");
        wrap.dataset.jobId = job.jobId;
        wrap.className = "grid grid-cols-1 lg:grid-cols-3 gap-4";
        wrap.innerHTML = `
            <div class="space-y-3">
                <p class="text-[12px]" style="color:var(--text-faint)">${escapeHtml(job.articleTitle || job.sourceUrl)}</p>
                <label class="field"><span class="label">Video keyword</span>
                    <input class="review-keyword" value="${escapeHtml(job.keyword || "")}" maxlength="120"></label>
                <label class="field"><span class="label">Video type</span>
                    <select class="review-content-type">
                        <option value="ROUNDUP" ${job.contentType === "ROUNDUP" ? "selected" : ""}>Roundup</option>
                        <option value="SINGLE" ${job.contentType === "SINGLE" ? "selected" : ""}>Single review</option>
                    </select></label>
            </div>
            <div class="lg:col-span-2 space-y-2">
                <p class="section-label">Products in this video</p>
                <div class="product-rows space-y-2"></div>
                ${job.error ? `<p class="text-[12px]" style="color:var(--warn-700)">${escapeHtml(job.error)}</p>` : ""}
                <div class="flex flex-wrap justify-end gap-2 pt-1">
                    <button type="button" class="btn btn-sm" data-action="save">Save Review</button>
                    ${job.status === "READY" ? `<button type="button" class="btn btn-ok btn-sm" data-action="approve">${job.isApproved ? "Unapprove" : "Approve"}</button>` : ""}
                </div>
            </div>`;
        const rows = wrap.querySelector(".product-rows");
        (job.products || []).forEach((product, index) => {
            const row = document.createElement("div");
            row.className = "grid gap-2 p-2 border rounded-lg items-start";
            row.style.cssText = "border-color:var(--border);grid-template-columns:auto 52px minmax(0,1fr)";
            const dupNote = product.duplicateAcrossBatch ? ` · repeated in ${product.batchOccurrenceCount} sources` : "";
            row.innerHTML = `
                <input type="checkbox" class="review-product mt-2" data-i="${index}" ${product.isIncluded !== false ? "checked" : ""} title="Include">
                <input type="number" class="review-product-rank text-[12px]" data-i="${index}" min="1" max="${job.products.length}" value="${index + 1}" title="Order">
                <div class="grid gap-2" style="grid-template-columns:150px minmax(0,1fr)">
                    <input type="text" class="review-product-asin text-[12px] uppercase mono" data-i="${index}" maxlength="10" value="${escapeHtml(product.asin || "")}" aria-label="ASIN for product ${index + 1}">
                    <div class="min-w-0 py-1">
                        <span class="block text-[12px]" style="color:var(--text)">${escapeHtml(product.name || product.asin || "")}</span>
                        <span class="block text-[11px] ${product.duplicateAcrossBatch ? "" : ""}" style="color:${product.duplicateAcrossBatch ? "var(--warn-700)" : "var(--text-faint)"}">${escapeHtml(product.validationStatus || "UNVERIFIED")} · ${escapeHtml(product.availability || "UNKNOWN")}${escapeHtml(dupNote)}</span>
                    </div>
                </div>`;
            rows.appendChild(row);
        });
        wrap.querySelector('[data-action="save"]').addEventListener("click", () => saveContentReview(job.jobId, false));
        wrap.querySelector('[data-action="approve"]')?.addEventListener("click", () => saveContentReview(job.jobId, !job.isApproved));
        return wrap;
    }

    function toggleContentReview(jobId) {
        document.getElementById(`content-review-${jobId}`)?.classList.toggle("hidden");
    }

    async function saveContentReview(jobId, isApproved) {
        const editor = document.querySelector(`[data-job-id="${jobId}"]`);
        const job = currentContentBatch.jobs.find((j) => j.jobId === jobId);
        if (!editor || !job) return;
        const products = job.products
            .map((product, index) => ({
                ...product,
                asin: editor.querySelector(`.review-product-asin[data-i="${index}"]`)?.value.trim().toUpperCase(),
                isIncluded: editor.querySelector(`.review-product[data-i="${index}"]`)?.checked !== false,
                reviewRank: Number(editor.querySelector(`.review-product-rank[data-i="${index}"]`)?.value || index + 1),
            }))
            .sort((a, b) => a.reviewRank - b.reviewRank)
            .map(({ reviewRank, ...product }) => product);
        try {
            const result = await api(`/api/content-jobs/${jobId}`, {
                method: "PATCH",
                body: {
                    keyword: editor.querySelector(".review-keyword").value,
                    contentType: editor.querySelector(".review-content-type").value,
                    products, isApproved,
                },
            });
            currentContentBatch.jobs = currentContentBatch.jobs.map((j) => (j.jobId === jobId ? result.data : j));
            renderContentBatch(currentContentBatch);
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    async function approveAllReadyJobs() {
        const ready = currentContentBatch?.jobs?.filter((j) => j.status === "READY" && !j.isApproved) || [];
        try {
            for (const job of ready) {
                await api(`/api/content-jobs/${job.jobId}`, { method: "PATCH", body: { isApproved: true } });
            }
            await refreshContentBatch();
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    async function retryContentJob(jobId) {
        try {
            await api(`/api/content-jobs/${jobId}/retry`, { method: "POST", body: {} });
            await refreshContentBatch();
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    async function generateApprovedBatch() {
        const approved = currentContentBatch?.jobs?.filter((j) => j.isApproved) || [];
        if (!approved.length) {
            showContentBatchError("Approve at least one ready video before generation.");
            return;
        }
        try {
            await api(`/api/content-batches/${currentContentBatch.batchId}/prepare`, { method: "POST", body: {} });
            await startGeneration({ videoCount: approved.length });
            loadContentHistory();
        } catch (err) {
            showContentBatchError(err.message);
        }
    }

    const HISTORY_STATUS_BADGE = { PROCESSING: "badge-brand", DONE: "badge-ok", FAILED: "badge-error" };
    const HISTORY_STATUS_LABEL = { PROCESSING: "PROCESSING", DONE: "DONE", FAILED: "FAILED", "": "PENDING" };

    async function loadContentHistory() {
        const body = $("#contentHistoryBody");
        if (!body) return;
        try {
            const result = await api("/api/content-batches/history?limit=30");
            const rows = result.data || [];
            if (!rows.length) {
                body.innerHTML = `<tr><td colspan="5" class="text-center" style="color:var(--text-faint)">No videos generated yet.</td></tr>`;
                return;
            }
            body.innerHTML = rows.map((row) => {
                let host = row.sourceUrl;
                try { host = new URL(row.sourceUrl).hostname; } catch (_) {}
                const when = row.generatedAt ? new Date(row.generatedAt).toLocaleString() : "";
                // A finished video FILE is the strongest signal there is --
                // if it exists, Watch shows regardless of what the status
                // badge says (a render that succeeded after a slow finish
                // must never be hidden behind a stale "PROCESSING" badge).
                const watchCell = row.hasVideo
                    ? `<a class="btn btn-sm" href="/video/${encodeURIComponent(row.projectId)}" target="_blank" rel="noopener">▶ Watch</a>`
                    : row.renderStatus === "FAILED"
                        ? `<button type="button" class="btn btn-sm" data-history-action="retry" data-job-id="${row.jobId}">↻ Retry</button>`
                        : `<span class="hint">${row.renderStatus === "PROCESSING" ? "Rendering…" : "Not found"}</span>`;
                const statusBadge = HISTORY_STATUS_BADGE[row.renderStatus] || "badge-neutral";
                const statusLabel = HISTORY_STATUS_LABEL[row.renderStatus] ?? row.renderStatus;
                return `<tr data-job-id="${row.jobId}">
                    <td class="font-medium" style="color:var(--text)">${escapeHtml(row.keyword)}</td>
                    <td class="truncate max-w-[170px]" title="${escapeHtml(host)}">${escapeHtml(host)}</td>
                    <td>${escapeHtml(when)}</td>
                    <td class="text-center"><span class="badge ${statusBadge}">${escapeHtml(statusLabel)}</span></td>
                    <td class="text-right">
                        <div class="flex justify-end gap-2">
                            ${watchCell}
                            <button type="button" class="btn btn-icon btn-ghost" data-history-action="delete" data-job-id="${row.jobId}" title="Delete this row" aria-label="Delete this row">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path stroke-linecap="round" stroke-linejoin="round" d="M6 7h12M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-7 0v12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V7"/></svg>
                            </button>
                        </div>
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            body.innerHTML = `<tr><td colspan="5" class="text-center" style="color:var(--danger-600)">${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function deleteHistoryRow(jobId) {
        const confirmed = await modal({
            title: "Delete this history row?",
            message: "This removes it from History. The generated video file on disk, if any, is not deleted.",
            kind: "warn", confirmText: "Delete", cancelText: "Cancel", danger: true,
        });
        if (!confirmed) return;
        try {
            await api(`/api/content-jobs/${jobId}`, { method: "DELETE" });
            await loadContentHistory();
        } catch (err) {
            toast(`Delete failed: ${err.message}`, "error", 5000);
        }
    }

    async function retryHistoryRow(jobId) {
        try {
            await api(`/api/content-jobs/${jobId}/regenerate`, { body: {} });
            await loadContentHistory();
            // Reuses the exact same generation stream Generate Approved
            // uses -- amazon_video_maker.py already resumes an interrupted
            // keyword from its existing project folder instead of starting
            // over (the `resuming` check where it builds base_dir), so
            // re-queuing this one keyword IS "retry from where it stopped".
            await startGeneration({ videoCount: 1 });
        } catch (err) {
            toast(`Retry failed: ${err.message}`, "error", 5000);
        }
    }

    /* ============================================================ module 2 === */

    function parseKeywordAsinText() {
        const text = $("#keywords_asin")?.value || "";
        return text.split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
            const [keyword, ...asins] = line.split(",").map((p) => p.trim());
            return { keyword, asins: asins.filter(Boolean) };
        });
    }

    function updateASINStats() {
        const rows = parseKeywordAsinText();
        const countEl = $("#total_kw_count");
        const container = $("#asin_stats_container");
        if (countEl) countEl.textContent = rows.length;
        const videoCountDisplay = $("#videoCountDisplay");
        if (videoCountDisplay) videoCountDisplay.textContent = `0/${rows.length}`;
        if (!container) return;
        container.classList.toggle("hidden", rows.length === 0);
        container.innerHTML = rows
            .map(
                (row) => `
            <div class="card card-pad flex items-center justify-between">
                <span class="text-[12px] font-semibold uppercase truncate pr-2" title="${escapeHtml(row.keyword)}">${escapeHtml(row.keyword)}</span>
                <span class="badge badge-brand">${row.asins.length} ASINs</span>
            </div>`
            )
            .join("");
    }

    async function validateAsins() {
        const rows = parseKeywordAsinText().filter((r) => r.asins.length);
        const resultsBox = $("#asinValidationResults");
        const button = $("#validateAsinsBtn");
        if (!rows.length || !resultsBox) return;
        await withBusy(button, "Checking Amazon…", async () => {
            try {
                const result = await api("/api/asins/validate", { body: { rows } });
                renderAsinValidation(result);
            } catch (err) {
                resultsBox.innerHTML = `<p class="text-[12px]" style="color:var(--danger-600)">${escapeHtml(err.message)}</p>`;
                resultsBox.classList.remove("hidden");
            }
        });
    }

    const VALIDATION_BADGE = {
        VERIFIED: "badge-ok", SCRAPED: "badge-ok", NOT_FOUND: "badge-error",
        VALIDATION_FAILED: "badge-error", MANUAL_REVIEW: "badge-warn",
    };
    const VALIDATION_LABEL = { SCRAPED: "FOUND", VERIFIED: "VERIFIED" };

    function renderAsinValidation(result) {
        const box = $("#asinValidationResults");
        if (!box) return;
        box.classList.remove("hidden");
        const notice = result.configured
            ? ""
            : `<p class="hint mb-2">Showing live data scraped from each product's Amazon page (no Amazon Creators API configured). Add credentials in Settings → Amazon for official catalog data instead.</p>`;
        box.innerHTML =
            notice +
            result.data
                .map(
                    (row) => `
            <div class="card card-pad mb-3">
                <p class="section-label mb-2">${escapeHtml(row.keyword || "(no keyword)")}</p>
                <div class="grid gap-2" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr))">
                    ${row.products
                        .map((product) => {
                            const badge = VALIDATION_BADGE[product.validationStatus] || "badge-neutral";
                            return `
                        <div class="flex gap-2 p-2 border rounded-lg" style="border-color:var(--border)">
                            ${product.imageUrl ? `<img src="${escapeHtml(product.imageUrl)}" alt="" class="rounded" style="width:44px;height:44px;object-fit:contain;background:var(--surface-2)">` : `<div style="width:44px;height:44px" class="rounded skeleton"></div>`}
                            <div class="min-w-0 flex-1">
                                <span class="block text-[12px] font-semibold truncate" title="${escapeHtml(product.name || product.asin)}">${escapeHtml(product.name || product.asin)}</span>
                                <span class="block text-[11px] mono" style="color:var(--text-faint)">${escapeHtml(product.asin)} ${product.price ? "· " + escapeHtml(product.price) : ""}</span>
                                <span class="badge ${badge} mt-1">${escapeHtml(VALIDATION_LABEL[product.validationStatus] || product.validationStatus || "UNVERIFIED")}</span>
                            </div>
                        </div>`;
                        })
                        .join("")}
                </div>
            </div>`
                )
                .join("");
    }

    /* ==================================================== generation (SSE) === */

    let currentProg = 0;

    function setProgress(pct, text) {
        currentProg = pct;
        const bar = $("#progressBar");
        const pctEl = $("#progressPercent");
        const statusEl = $("#progressStatus");
        if (bar) bar.style.width = pct + "%";
        if (pctEl) pctEl.textContent = pct + "%";
        if (text && statusEl) statusEl.textContent = text;
    }

    // Substring-matched against amazon_video_maker.py's log lines. Fragile by
    // nature (a log reword breaks it) -- kept centralized here as the single
    // place that assumption lives, instead of duplicated per page.
    const PROGRESS_RULES = [
        [/Fetching page for ASIN/, () => setProgress(Math.min(20, currentProg + 2), "Searching Amazon…")],
        [/Successfully fetched page/, () => setProgress(Math.max(currentProg, 14), "Reading Product Page…")],
        [/Downloading Video/, () => setProgress(Math.max(currentProg, 16), "Downloading Product Media…")],
        [/Downloaded.*images/, () => setProgress(Math.max(currentProg, 18), "Product Images Ready…")],
        [/Rewriting content/, () => setProgress(Math.max(currentProg, 20), "Writing Review Script…")],
        [/AI (Title|Description) Generated/, () => setProgress(Math.max(currentProg, 23), "Review Script Ready…")],
        [/Loading Kokoro TTS model/, () => setProgress(Math.max(currentProg, 24), "Loading Voice Model…")],
        [/\[AUDIO\]\[START\]/, () => setProgress(Math.max(currentProg, 26), "Generating Voice…")],
        [/\[AUDIO\]\[(OK|CACHE)\]/, () => setProgress(Math.min(34, Math.max(currentProg + 1, 27)))],
        [/ASIN.*ready/, () => setProgress(Math.min(34, Math.max(currentProg + 2, 30)), "Product Assets Ready…")],
        [/Generating YouTube Metadata/, () => setProgress(Math.max(currentProg, 35), "Generating YouTube SEO…")],
        [/Rendering segments with FFmpeg/, () => setProgress(Math.max(currentProg, 40), "Starting Render…")],
        [/Creating segment for Product/, () => setProgress(Math.min(65, currentProg + 5), "Rendering Segments…")],
        [/Concatenating segments/, () => setProgress(70, "Concatenating Clips…")],
        [/Adjusting playback speed/, () => setProgress(78, "Adjusting Speed…")],
        [/Adding animated branding/, () => setProgress(85, "Overlaying Branding…")],
        [/Saving YouTube Metadata/, () => setProgress(95, "Finalizing Assets…")],
    ];

    function updateProgressUI(line) {
        for (const [pattern, action] of PROGRESS_RULES) {
            if (pattern.test(line)) { action(); return; }
        }
    }

    async function startGeneration({ videoCount } = {}) {
        const logArea = $("#logArea");
        const startBtn = $("#startBtn");
        const videoCountDisplay = $("#videoCountDisplay");
        if (!logArea || !startBtn) return;

        const saved = await saveSettings(true).catch((err) => {
            saveSettings.lastError = err.message;
            return false;
        });
        if (!saved) {
            const reason = saveSettings.lastError || "Unknown error";
            logArea.textContent = `Settings save failed: ${reason}\n`;
            $("#progressStatus") && ($("#progressStatus").textContent = "Settings Save Failed");
            await modal({ title: "Save failed", message: `Settings could not be saved, so generation was not started. ${reason}`, kind: "error", confirmText: "OK" });
            return;
        }

        const total = videoCount ?? parseKeywordAsinText().length ?? 0;
        if (videoCountDisplay) videoCountDisplay.textContent = `0/${total}`;

        logArea.textContent = "Initializing pipeline...\n";
        const startBtnOriginal = startBtn.innerHTML;
        startBtn.disabled = true;
        startBtn.innerHTML = `<span class="spinner"></span> Processing…`;
        currentProg = 0;
        let sessionVideoCount = 0;
        let failedKeywords = [];
        setProgress(0, "Starting…");

        const source = new EventSource(`/run_process?csrf_token=${encodeURIComponent(window.CSRF_TOKEN)}`);
        const finish = () => {
            source.close();
            startBtn.disabled = false;
            startBtn.innerHTML = startBtnOriginal;
        };

        source.onmessage = (event) => {
            const line = event.data;
            if (line === "__DONE__") {
                finish();
                logArea.textContent += "\n--- PROCESS COMPLETED ---";
                setProgress(100, "Completed");
                // The Watch button (and Status badge) used to only appear
                // after a manual page reload. The server has already
                // resolved every job's render outcome by the time __DONE__
                // is sent (record_generation_results runs before that yield).
                loadContentHistory();
                if (logArea.textContent.includes("[QUOTA REACHED]")) {
                    modal({ title: "Quota exceeded", message: `Process stopped because you hit your limit. Videos created this session: ${sessionVideoCount}.`, kind: "error", confirmText: "OK" });
                } else if (failedKeywords.length && sessionVideoCount > 0) {
                    // Partial success: some keywords produced a video, at
                    // least one didn't. This used to show the same plain
                    // "completed" toast as a clean run -- the failure was
                    // only visible if you scrolled back through the log.
                    toast(`${sessionVideoCount} video(s) created, ${failedKeywords.length} failed: ${failedKeywords.join(", ")}`, "warn", 9000);
                } else if (failedKeywords.length) {
                    toast(`All ${failedKeywords.length} keyword(s) failed: ${failedKeywords.join(", ")} -- check the log for the reason.`, "error", 9000);
                } else if (sessionVideoCount > 0) {
                    toast(`Video generation completed — ${sessionVideoCount} video(s) created`, "ok", 6000);
                } else {
                    toast("Process finished. Check the log for details.", "warn", 6000);
                }
            } else if (line.startsWith("__SYNC_QUOTA__:")) {
                const used = parseInt(line.split(":")[1], 10);
                document.dispatchEvent(new CustomEvent("af:quota-sync", { detail: { used } }));
            } else if (line.startsWith("__SESSION_COUNT__:")) {
                sessionVideoCount = parseInt(line.split(":")[1], 10);
                if (videoCountDisplay) videoCountDisplay.textContent = `${sessionVideoCount}/${total}`;
                // Fires once per video that actually finished -- refresh
                // History now instead of making the user wait for the
                // whole batch (or a reload) to see this one's Watch link.
                loadContentHistory();
            } else if (line.startsWith("__FAILED_KEYWORDS__:")) {
                failedKeywords = line.slice("__FAILED_KEYWORDS__:".length).split(",").filter(Boolean);
            } else {
                logArea.textContent += line + "\n";
                logArea.scrollTop = logArea.scrollHeight;
                updateProgressUI(line);
            }
        };

        source.onerror = () => {
            finish();
            $("#progressStatus") && ($("#progressStatus").textContent = "Error Occurred");
            logArea.textContent += "\n[ERROR] Could not connect to the generation stream. Refresh and try again.\n";
            toast("Connection to the generation stream was lost.", "error", 6000);
        };
    }

    /* -------------------------------------------------------------- quota --- */

    document.addEventListener("af:quota-sync", (event) => {
        const usedDisplay = $("#used_quota_display");
        const quotaEl = $("#total_quota_val");
        const progBar = $("#dashboard_quota_bar");
        const pctText = $("#quota_percent_text");
        const container = $("#total_quota_container");
        const used = event.detail.used;
        const quota = parseInt(quotaEl?.textContent || "0", 10) || 0;
        if (usedDisplay) usedDisplay.textContent = used;
        if (quota > 0) {
            const pct = Math.min(100, Math.round((used / quota) * 100));
            if (progBar) progBar.style.width = pct + "%";
            if (pctText) pctText.textContent = pct + "% Used";
            if (container && used >= quota) {
                container.classList.add("badge-error");
                container.classList.remove("badge-brand");
            }
        }
    });

    /* ---------------------------------------------------------------- boot --- */

    document.addEventListener("DOMContentLoaded", async () => {
        window.__AF_SETTINGS = await loadSettings().catch(() => ({}));
        await initRenderOptions();
        updateASINStats();
        updateContentUrlCount();
        loadLatestContentBatch();
        loadContentHistory();
        $("#contentHistoryBody")?.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-history-action]");
            if (!btn) return;
            const jobId = btn.dataset.jobId;
            if (btn.dataset.historyAction === "delete") deleteHistoryRow(jobId);
            else if (btn.dataset.historyAction === "retry") retryHistoryRow(jobId);
        });

        $$("input, textarea, select").forEach((el) => {
            if (!el.id || el.dataset.setting === "false" || el.dataset.noAutosave === "1") return;
            el.addEventListener("change", autosave);
        });

        $("#content_urls")?.addEventListener("input", updateContentUrlCount);
        $("#analyzeContentBtn")?.addEventListener("click", analyzeContentUrls);
        $("#restoreLatestBtn")?.addEventListener("click", loadLatestContentBatch);
        $("#approveAllBtn")?.addEventListener("click", approveAllReadyJobs);
        $("#refreshBatchBtn")?.addEventListener("click", refreshContentBatch);
        $("#generateApprovedBtn")?.addEventListener("click", generateApprovedBatch);
        $("#deleteSelectedJobsBtn")?.addEventListener("click", deleteSelectedJobs);
        $("#selectAllJobsCheckbox")?.addEventListener("change", (e) => {
            selectedJobIds.clear();
            if (e.target.checked) {
                (currentContentBatch?.jobs || []).forEach((j) => selectedJobIds.add(j.jobId));
            }
            renderContentBatch(currentContentBatch);
        });

        $("#keywords_asin")?.addEventListener("input", updateASINStats);
        $("#validateAsinsBtn")?.addEventListener("click", validateAsins);

        // The bottom "Start AI Video Creation" button is shared by both
        // modules via _render_options.html. On the URL-to-Video page it must
        // NOT call startGeneration() directly: that only re-runs whatever is
        // already sitting in the server's keyword/ASIN file, which -- on
        // this page -- was never written by anything, so it silently reused
        // stale content left over from a previous Keywords/ASINs session
        // instead of this page's approved URL batch. Route it through the
        // same approve-and-prepare path as "Generate Approved" instead.
        $("#startBtn")?.addEventListener("click", () => {
            if ($("#content_urls")) {
                generateApprovedBatch();
            } else {
                startGeneration();
            }
        });

        $("#browseOutputRootBtn")?.addEventListener("click", async () => {
            const input = $("#output_root");
            const chosen = await AF.browseFolder(input?.value || "");
            if (chosen && input) {
                input.value = chosen;
                autosave();
            }
        });
    });

    window.AF_CREATION = { autosave, updateEdgeSliders, saveSettings, startGeneration };
})();
