(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const MODE_LABEL = { "text-to-text": "Text → Text", "text-to-image": "Text → Image", "image-to-text": "Image → Text", "image-to-image": "Image → Image" };
  const STYLE_SWATCH = {
    studio_white: "linear-gradient(#fafafc,#e8eaf0)",
    lifestyle: "linear-gradient(135deg,#ffecd2,#c8e1d2)",
    luxury_dark: "radial-gradient(circle at 50% 40%,#3a3440,#08080a)",
    minimal_pastel: "linear-gradient(135deg,#ece4fa,#d6eeec)",
    vibrant_pop: "linear-gradient(135deg,#ff5a78,#ffbe3c)",
  };
  const ICON_WARN = '<svg viewBox="0 0 24 24"><path d="M12 9v4m0 4h.01M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>';
  const ICON_OK = '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5"/></svg>';

  const state = {
    meta: null, mode: "text-to-text", tone: "professional", style: "studio_white", image: null,
    result: null, rating: 0, imgRating: 0, shownAt: 0, exportFmt: "plain", historyMode: "",
  };

  function toast(msg, kind = "") {
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.textContent = msg;
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 3800);
  }

  async function api(path, opts = {}) {
    let res;
    try { res = await fetch(path, opts); }
    catch { throw { code: "network", message: "Cannot reach the server. Is the backend running?" }; }
    const type = res.headers.get("content-type") || "";
    const body = type.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw (body && body.error) || { code: "error", message: `Request failed (${res.status}).` };
    return body;
  }

  function store(key, val) {
    try { if (val === undefined) return localStorage.getItem(key); localStorage.setItem(key, val); } catch { return null; }
  }

  function applyTheme(t) { document.documentElement.dataset.theme = t; }
  applyTheme(store("theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  $("#theme-toggle").onclick = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(t); store("theme", t);
  };

  function showPage(p) {
    $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.page === p));
    $$(".page").forEach((s) => s.classList.toggle("active", s.id === `page-${p}`));
    if (p === "history") loadHistory();
    if (p === "evaluation") loadMetrics();
    if (p === "voices") loadVoices();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  $$(".tab").forEach((b) => (b.onclick = () => showPage(b.dataset.page)));

  function setStep(n) {
    $$("#stepper li").forEach((li) => {
      const s = +li.dataset.step;
      li.classList.toggle("done", s < n);
      li.classList.toggle("on", s <= n);
    });
  }

  function setMode(m) {
    state.mode = m;
    $$(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
    $$("#gen-form [data-modes]").forEach((el) => (el.hidden = !el.dataset.modes.split(" ").includes(m)));
    $$(".req-tt").forEach((e) => (e.hidden = m === "image-to-text"));
    $("#form-title").textContent = { "text-to-text": "Product facts", "text-to-image": "Describe the visual", "image-to-text": "Photo + optional facts", "image-to-image": "Photo to restyle" }[m];
    $("#generate-btn .btn-label").textContent = { "text-to-text": "Generate listing", "text-to-image": "Generate image", "image-to-text": "Describe photo", "image-to-image": "Restyle photo" }[m];
    $("#load-example").hidden = m === "image-to-image";
    clearErrors();
    resetResult();
  }
  $$(".mode").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));

  function resetResult() {
    $("#empty-state").hidden = false;
    $("#skeleton").hidden = true;
    $("#text-result").hidden = true;
    $("#image-result").hidden = true;
    state.result = null;
    setStep(1);
  }

  async function init() {
    try {
      const m = await api("/api/meta");
      state.meta = m;
      $("#max-mb").textContent = m.max_image_mb;
      $("#tone-pills").innerHTML = m.tones.map((t) => `<button type="button" class="pill" data-tone="${esc(t)}">${esc(t)}</button>`).join("");
      $$("#tone-pills .pill").forEach((p) => (p.onclick = () => setTone(p.dataset.tone)));
      setTone("professional", true);
      $("#v-tone").innerHTML = m.tones.map((t) => `<option>${esc(t)}</option>`).join("");
      $("#f-format").innerHTML = m.formats.map((f) => `<option value="${f}">${f.toUpperCase()}</option>`).join("");
      $("#export-format").innerHTML = m.formats.map((f) => `<button type="button" data-f="${f}">${f}</button>`).join("");
      $$("#export-format button").forEach((b) => (b.onclick = () => setExportFmt(b.dataset.f)));
      setExportFmt("plain");
      $("#style-tiles").innerHTML = Object.keys(m.styles).map((k) =>
        `<button type="button" class="style-tile" data-style="${k}" title="${esc(m.styles[k])}"><div class="sw" style="background:${STYLE_SWATCH[k] || "#ccc"}"><i></i></div><small>${esc(k.replace(/_/g, " "))}</small></button>`).join("");
      $$("#style-tiles .style-tile").forEach((t) => (t.onclick = () => setStyle(t.dataset.style)));
      setStyle("studio_white");
      const opts = [["auto", `Auto (${m.default_provider})`]].concat(
        Object.entries(m.providers).map(([k, ok]) => [k, `${k === "mock" ? "Offline (no key)" : k[0].toUpperCase() + k.slice(1)}${ok ? "" : " — not configured"}`]));
      $("#f-provider").innerHTML = opts.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
      const p = $("#chip-provider");
      p.classList.add(m.default_provider === "mock" ? "warn" : "ok");
      $("span", p).textContent = m.default_provider === "mock" ? "Offline AI" : `AI: ${m.default_provider}`;
      p.title = m.default_provider === "mock" ? "No API key configured - using the offline template provider" : "AI provider configured";
      const h = await api("/api/health");
      const d = $("#chip-db");
      const mongo = h.db === "mongodb";
      d.classList.add(mongo ? "ok" : "warn");
      $("span", d).textContent = mongo ? "MongoDB" : "In-memory DB";
      d.title = mongo ? "Connected to MongoDB" : "MongoDB not reachable - using in-memory fallback (data is lost on restart)";
      loadVoiceOptions();
    } catch (e) {
      toast(e.message || "Could not load settings.", "bad");
    }
  }

  function setTone(t, silent = false) {
    state.tone = t;
    $$("#tone-pills .pill").forEach((p) => p.classList.toggle("on", p.dataset.tone === t));
    if (!silent && !state.result) setStep(2);
  }
  function setStyle(s) {
    state.style = s;
    $$("#style-tiles .style-tile").forEach((p) => p.classList.toggle("on", p.dataset.style === s));
  }
  function setExportFmt(f) {
    state.exportFmt = f;
    $$("#export-format button").forEach((b) => b.classList.toggle("on", b.dataset.f === f));
  }
  function currentStep() { return $$("#stepper li.on").length || 1; }

  const dz = $("#dropzone");
  const input = $("#image-input");
  function takeFile(f) {
    if (!f) return;
    if (!/^image\/(png|jpeg|webp)$/.test(f.type)) return toast("Please use a PNG, JPG or WEBP image.", "bad");
    const max = (state.meta?.max_image_mb || 8) * 1024 * 1024;
    if (f.size > max) return toast("That image is too large.", "bad");
    state.image = f;
    const url = URL.createObjectURL(f);
    $("#dz-preview").src = url;
    $("#dz-preview").hidden = false;
    $(".dz-empty", dz).hidden = true;
    setFieldError("image", "");
  }
  input.onchange = () => takeFile(input.files[0]);
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => takeFile(e.dataTransfer.files[0]));

  const val = (id) => $(id).value.trim();
  function facts() {
    return {
      product_name: val("#f-name"), category: val("#f-category"), materials: val("#f-materials"), size: val("#f-size"),
      features: $("#f-features").value.split("\n").map((s) => s.trim()).filter(Boolean),
      target_audience: val("#f-audience"), use_case: val("#f-usecase"), brand_voice: state.tone,
      brand_voice_id: $("#f-voice").value || null, keyword: val("#f-keyword"),
      output_format: $("#f-format").value || "plain", provider: $("#f-provider").value || "auto",
    };
  }
  function clearErrors() {
    $$("#gen-form .err").forEach((e) => (e.textContent = ""));
    $$("#gen-form .field.invalid").forEach((e) => e.classList.remove("invalid"));
    $("#form-error").hidden = true;
  }
  function setFieldError(name, msg) {
    const e = $(`#gen-form [data-err="${name}"]`);
    if (!e) return false;
    e.textContent = msg;
    e.closest(".field").classList.toggle("invalid", !!msg);
    return true;
  }
  function showFormError(err) {
    if (err.fields) {
      let unplaced = [];
      Object.entries(err.fields).forEach(([k, m]) => { if (!setFieldError(k, m)) unplaced.push(m); });
      if (unplaced.length) { $("#form-error").textContent = unplaced.join(" "); $("#form-error").hidden = false; }
      const first = $("#gen-form .field.invalid input, #gen-form .field.invalid textarea");
      if (first) first.focus();
    } else {
      $("#form-error").textContent = `${err.message || "Something went wrong."} Your inputs are kept — you can retry.`;
      $("#form-error").hidden = false;
    }
  }
  function clientValidate() {
    const m = state.mode;
    let ok = true;
    if (m === "image-to-text" || m === "image-to-image") {
      if (!state.image) { setFieldError("image", "Please add a product photo."); ok = false; }
    }
    if (m === "text-to-text" || m === "text-to-image") {
      if (!val("#f-name")) { setFieldError("product_name", "Product name is required."); ok = false; }
      if (!val("#f-category")) { setFieldError("category", "Category is required."); ok = false; }
    }
    return ok;
  }

  $("#load-example").onclick = () => {
    const ex = state.meta?.example;
    if (!ex) return;
    $("#f-name").value = ex.product_name; $("#f-category").value = ex.category; $("#f-materials").value = ex.materials;
    $("#f-size").value = ex.size; $("#f-features").value = ex.features.join("\n"); $("#f-audience").value = "";
    $("#f-usecase").value = ""; $("#f-keyword").value = ex.keyword;
    setTone(ex.brand_voice);
    toast("Loaded the illustrative example from the research deck (slide 12).");
  };
  $("#gen-form").addEventListener("input", () => { if (currentStep() < 2) setStep(1); });

  $("#gen-form").onsubmit = async (e) => {
    e.preventDefault();
    clearErrors();
    if (!clientValidate()) return;
    const btn = $("#generate-btn");
    btn.disabled = true; $(".spinner", btn).hidden = false;
    $("#empty-state").hidden = true; $("#text-result").hidden = true; $("#image-result").hidden = true; $("#skeleton").hidden = false;
    setStep(3);
    try {
      let r;
      const m = state.mode;
      if (m === "text-to-text") {
        r = await api("/api/generate/text-to-text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(facts()) });
      } else if (m === "text-to-image") {
        r = await api("/api/generate/text-to-image", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ facts: facts(), style: state.style, extra_prompt: val("#f-extra") }) });
      } else {
        const fd = new FormData();
        fd.append("image", state.image);
        fd.append("provider", $("#f-provider").value || "auto");
        if (m === "image-to-text") {
          const f = facts();
          ["product_name", "category", "materials", "size", "target_audience", "keyword", "output_format"].forEach((k) => fd.append(k, f[k]));
          fd.append("features", f.features.join("\n"));
          fd.append("brand_voice", f.brand_voice);
          r = await api("/api/generate/image-to-text", { method: "POST", body: fd });
        } else {
          fd.append("style", state.style);
          fd.append("extra_prompt", val("#f-extra"));
          r = await api("/api/generate/image-to-image", { method: "POST", body: fd });
        }
      }
      state.result = r;
      state.shownAt = performance.now();
      r.draft ? renderText(r) : renderImage(r);
      setStep(4);
    } catch (err) {
      $("#skeleton").hidden = true; $("#empty-state").hidden = false; setStep(1);
      showFormError(err);
      toast(err.message || "Generation failed.", "bad");
    } finally {
      btn.disabled = false; $(".spinner", btn).hidden = true;
    }
  };

  function badges(r) {
    return `<span class="badge acc">${esc(MODE_LABEL[r.mode])}</span><span class="badge">${esc(r.provider)} · ${esc(r.model)}</span><span class="badge">${r.latency_ms} ms</span>`;
  }
  function flagsHtml(flags) {
    if (!flags || !flags.length) return `<div class="flag ok">${ICON_OK}<span>No review flags raised. Still check every claim against your product data.</span></div>`;
    return flags.map((f) => `<div class="flag">${ICON_WARN}<span>${esc(f)}</span></div>`).join("");
  }

  function renderText(r) {
    $("#skeleton").hidden = true;
    $("#text-result").hidden = false;
    $("#text-badges").innerHTML = badges(r);
    $("#text-flags").innerHTML = flagsHtml(r.draft.review_flags);
    $("#r-title").value = r.draft.title;
    $("#r-desc").value = r.draft.description;
    $("#r-bullets").value = r.draft.bullets.join("\n");
    $("#r-meta").value = r.draft.meta_description;
    $("#r-prompt").textContent = r.prompt ? `SYSTEM\n${r.prompt.system}\n\nUSER\n${r.prompt.user}` : "Vision prompt is built on the server from the photo and your facts.";
    if (r.input_image_url) {
      $("#it-thumb").hidden = false;
      $("#it-thumb img").src = r.input_image_url;
      const a = r.analysis || {};
      $("#it-analysis").innerHTML = [`${a.width}×${a.height}`, a.orientation, `colours: ${(a.dominant_colours || []).join(", ")}`, `background: ${a.background}`]
        .map((x) => `<span class="tag">${esc(x)}</span>`).join("");
    } else $("#it-thumb").hidden = true;
    setExportFmt(r.export_preview && state.meta ? ($("#f-format").value || "plain") : "plain");
    $("#r-claims").value = r.draft.bullets.length + 1;
    $("#r-unsupported").value = 0;
    $("#r-confirm").checked = false;
    state.rating = 0; drawStars("#stars", 0, (n) => (state.rating = n));
    updateCounts(); syncReviewGate();
  }

  function updateCounts() {
    $$(".count").forEach((c) => {
      const el = $(`#${c.dataset.for}`);
      const n = el.value.length;
      const max = +c.dataset.max || 0;
      c.textContent = max ? `${n}/${max}` : `${n} chars`;
      c.classList.toggle("over", max && n > max);
    });
  }
  ["#r-title", "#r-meta"].forEach((s) => $(s).addEventListener("input", updateCounts));

  function drawStars(sel, n, onSet) {
    const box = $(sel);
    box.innerHTML = [1, 2, 3, 4, 5].map((i) => `<button type="button" class="star ${i <= n ? "on" : ""}" data-n="${i}" aria-label="${i} star">★</button>`).join("");
    $$(".star", box).forEach((s) => (s.onclick = () => { onSet(+s.dataset.n); drawStars(sel, +s.dataset.n, onSet); }));
  }

  function editedDraft() {
    return {
      title: val("#r-title"), description: $("#r-desc").value.trim(),
      bullets: $("#r-bullets").value.split("\n").map((s) => s.trim()).filter(Boolean),
      meta_description: val("#r-meta"), review_flags: state.result?.draft?.review_flags || [],
    };
  }

  function syncReviewGate() {
    const ok = $("#r-confirm").checked;
    ["#approve-btn", "#copy-btn", "#download-btn"].forEach((s) => ($(s).disabled = !ok));
    $("#approve-hint").textContent = ok ? "Export is unlocked. Approving records your review in MongoDB for evaluation." : "Tick the review box to unlock approval and export.";
  }
  $("#r-confirm").onchange = syncReviewGate;

  $("#approve-btn").onclick = async () => {
    const r = state.result; if (!r) return;
    const total = +$("#r-claims").value || 0, unsupported = +$("#r-unsupported").value || 0;
    if (unsupported > total) return toast("Unsupported claims cannot exceed claims checked.", "bad");
    try {
      await api(`/api/history/${r.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true, edited: editedDraft(), rating: state.rating || null, unsupported_claims: unsupported,
          total_claims: total, seconds_to_approve: Math.round((performance.now() - state.shownAt) / 100) / 10 }) });
      toast("Draft approved and saved.", "good");
      setStep(5);
    } catch (e) { toast(e.message, "bad"); }
  };

  async function exportText() {
    const r = state.result;
    const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft: editedDraft(), format: state.exportFmt, product_name: val("#f-name") || r?.draft?.title || "listing" }) });
    if (!res.ok) throw { message: "Export failed." };
    const name = (res.headers.get("content-disposition") || "").match(/filename="(.+)"/)?.[1] || "listing.txt";
    return { text: await res.text(), name, type: res.headers.get("content-type") };
  }
  $("#copy-btn").onclick = async () => {
    try { const { text } = await exportText(); await navigator.clipboard.writeText(text); toast(`Copied as ${state.exportFmt.toUpperCase()}.`, "good"); setStep(5); }
    catch (e) { toast(e.message || "Copy failed.", "bad"); }
  };
  $("#download-btn").onclick = async () => {
    try {
      const { text, name, type } = await exportText();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([text], { type }));
      a.download = name; a.click(); URL.revokeObjectURL(a.href);
      setStep(5);
    } catch (e) { toast(e.message || "Download failed.", "bad"); }
  };

  function renderImage(r) {
    $("#skeleton").hidden = true;
    $("#image-result").hidden = false;
    $("#img-badges").innerHTML = badges(r);
    $("#img-flags").innerHTML = flagsHtml(r.review_flags);
    $("#img-prompt").textContent = r.prompt || "";
    const isEdit = r.mode === "image-to-image";
    $("#img-res-title").textContent = isEdit ? "Before / after" : "Generated image";
    $("#img-single").hidden = isEdit;
    $("#img-compare").hidden = !isEdit;
    if (isEdit) {
      $("#cmp-before").src = r.input_image_url; $("#cmp-after").src = r.image_url;
      $("#cmp-range").value = 50; moveCompare(50);
    } else $("#img-out").src = r.image_url;
    const dl = $("#img-download");
    dl.href = r.image_url; dl.download = `${r.mode}-${r.id}.png`;
    dl.classList.add("disabled");
    $("#img-confirm").checked = false; $("#img-approve").disabled = true;
    state.imgRating = 0; drawStars("#img-stars", 0, (n) => (state.imgRating = n));
  }
  function moveCompare(v) {
    $("#cmp-after-wrap").style.clipPath = `inset(0 0 0 ${v}%)`;
    $("#cmp-handle").style.left = `${v}%`;
  }
  $("#cmp-range").oninput = (e) => moveCompare(e.target.value);
  $("#img-confirm").onchange = (e) => { $("#img-approve").disabled = !e.target.checked; $("#img-download").classList.toggle("disabled", !e.target.checked); };
  $("#img-approve").onclick = async () => {
    const r = state.result; if (!r) return;
    try {
      await api(`/api/history/${r.id}/review`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved: true, rating: state.imgRating || null, seconds_to_approve: Math.round((performance.now() - state.shownAt) / 100) / 10 }) });
      toast("Image approved and saved.", "good"); setStep(5);
    } catch (e) { toast(e.message, "bad"); }
  };
  $("#img-download").addEventListener("click", () => setStep(5));
  $("#img-to-text").onclick = async () => {
    const r = state.result; if (!r) return;
    const blob = await (await fetch(r.image_url)).blob();
    const file = new File([blob], "generated.png", { type: "image/png" });
    setMode("image-to-text");
    takeFile(file);
    window.scrollTo({ top: $("#gen-form").offsetTop - 90, behavior: "smooth" });
    toast("Image loaded into Image → Text. Add facts and generate.");
  };

  async function loadHistory() {
    const seg = $("#history-filter");
    if (!seg.children.length) {
      seg.innerHTML = [["", "All"], ...Object.entries(MODE_LABEL)].map(([k, l]) => `<button data-m="${k}">${esc(l)}</button>`).join("");
      $$("button", seg).forEach((b) => (b.onclick = () => { state.historyMode = b.dataset.m; loadHistory(); }));
    }
    $$("button", seg).forEach((b) => b.classList.toggle("on", b.dataset.m === state.historyMode));
    const grid = $("#history-grid");
    try {
      const { items } = await api(`/api/history${state.historyMode ? `?mode=${state.historyMode}` : ""}`);
      if (!items.length) { grid.innerHTML = `<div class="empty">Nothing here yet. Generate something in the Studio.</div>`; return; }
      grid.innerHTML = items.map((it) => {
        const out = it.final_output || it.output || {};
        const img = out.image_id || (it.image_ids || [])[0];
        const title = out.title || it.input?.product_name || MODE_LABEL[it.mode];
        const rv = it.review;
        return `<article class="h-card" data-id="${it.id}">
          <div class="h-thumb">${img ? `<img src="/api/images/${img}" alt="" loading="lazy">` : `<div class="txt">${esc(out.description || "")}</div>`}</div>
          <div class="h-body"><b>${esc(title)}</b>
            <div class="h-meta"><span class="tag acc">${esc(MODE_LABEL[it.mode])}</span>
            ${rv?.approved ? '<span class="tag good">Approved</span>' : '<span class="tag">Draft</span>'}
            ${out.review_flags?.length ? `<span class="tag warn">${out.review_flags.length} flags</span>` : ""}
            <span>${new Date(it.created_at).toLocaleString()}</span></div></div></article>`;
      }).join("");
      $$(".h-card", grid).forEach((c) => (c.onclick = () => openHistory(c.dataset.id)));
    } catch (e) { grid.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  async function openHistory(id) {
    try {
      const it = await api(`/api/history/${id}`);
      const out = it.final_output || it.output || {};
      const inp = it.input || {};
      let html = `<h2>${esc(out.title || inp.product_name || MODE_LABEL[it.mode])}</h2>
        <div class="meta-badges" style="margin-bottom:14px"><span class="badge acc">${esc(MODE_LABEL[it.mode])}</span><span class="badge">${esc(it.provider)} · ${esc(it.model)}</span>
        ${it.review?.approved ? '<span class="badge" style="color:var(--good)">approved</span>' : ""}${it.review?.rating ? `<span class="badge">★ ${it.review.rating}</span>` : ""}</div>`;
      const imgs = (it.image_ids || []).map((i) => `<img src="/api/images/${i}" alt="">`).join("");
      if (imgs) html += `<div class="modal-imgs">${imgs}</div>`;
      if (out.description) {
        html += `<p>${esc(out.description)}</p><ul>${(out.bullets || []).map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
          <p><b>Meta:</b> ${esc(out.meta_description)}</p>${flagsHtml(out.review_flags)}`;
      }
      if (out.prompt) html += `<h3>Prompt</h3><pre>${esc(out.prompt)}</pre>`;
      if (!inp.redacted) html += `<details><summary>Inputs stored</summary><pre>${esc(JSON.stringify(inp, null, 2))}</pre></details>`;
      html += `<div style="margin-top:16px"><button class="btn danger" id="del-gen">Delete from database</button></div>`;
      $("#modal-body").innerHTML = html;
      $("#modal").hidden = false;
      $("#del-gen").onclick = async () => {
        if (!confirm("Delete this generation and its images?")) return;
        await api(`/api/history/${id}`, { method: "DELETE" });
        $("#modal").hidden = true; toast("Deleted."); loadHistory();
      };
    } catch (e) { toast(e.message, "bad"); }
  }
  $("#modal-close").onclick = () => ($("#modal").hidden = true);
  $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").hidden = true; });

  const fmt = {
    pct: (v) => (v == null ? "—" : `${(v * 100).toFixed(v < 0.1 && v > 0 ? 1 : 0)}%`),
    ms: (v) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`),
    num: (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d)),
  };
  async function loadMetrics() {
    try {
      const m = await api("/api/metrics");
      const kpis = [
        ["Requests", m.total_requests, `${fmt.pct(m.error_rate)} error rate`],
        ["Median latency", fmt.ms(m.p50_ms), `p95 ${fmt.ms(m.p95_ms)}`],
        ["Approval rate", fmt.pct(m.approval_rate), `${m.approved}/${m.reviewed} reviewed drafts`],
        ["Avg. quality rating", m.avg_rating == null ? "—" : `${fmt.num(m.avg_rating)} / 5`, "human reviewer score"],
        ["Seconds per approved", m.avg_seconds_per_approved == null ? "—" : `${fmt.num(m.avg_seconds_per_approved)} s`, "from draft shown to approval"],
        ["Unsupported claim rate", fmt.pct(m.unsupported_claim_rate), "reviewer-marked claims"],
        ["Auto flags / draft", fmt.num(m.avg_auto_flags, 2), "guardrail warnings raised"],
        ["Under 3 seconds", fmt.pct(m.share_under_3s), "share of successful requests"],
      ];
      $("#kpis").innerHTML = kpis.map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
      const rows = Object.entries(m.by_mode);
      const max = Math.max(1, ...rows.map(([, r]) => r.p50_ms || 0));
      $("#latency-bars").innerHTML = rows.map(([k, r]) =>
        `<div class="bar-row" title="${esc(MODE_LABEL[k])}: median ${fmt.ms(r.p50_ms)}, p95 ${fmt.ms(r.p95_ms)}, ${r.requests} requests, ${r.errors} errors">
          <span class="lbl">${esc(MODE_LABEL[k])}</span><div class="track"><div class="fill" style="width:${((r.p50_ms || 0) / max) * 100}%"></div></div><span class="val">${fmt.ms(r.p50_ms)}</span></div>`).join("");
      $("#latency-table").innerHTML = `<thead><tr><th>Mode</th><th>Requests</th><th>Errors</th><th>p50</th><th>p95</th></tr></thead><tbody>${
        rows.map(([k, r]) => `<tr><td>${esc(MODE_LABEL[k])}</td><td>${r.requests}</td><td>${r.errors}</td><td>${fmt.ms(r.p50_ms)}</td><td>${fmt.ms(r.p95_ms)}</td></tr>`).join("")}</tbody>`;
      const claims = [
        ["“Under 3 seconds” generation", "Unmeasured response-time target", "Record latency across models and request sizes", `${fmt.pct(m.share_under_3s)} of requests under 3 s (median ${fmt.ms(m.p50_ms)})`],
        ["30 min → under 30 sec; “95%” reduction", "Source estimates conflict mathematically", "Time matched tasks; clarify what is included", m.avg_seconds_per_approved == null ? "Approve drafts to measure" : `${fmt.num(m.avg_seconds_per_approved)} s average review-to-approval`],
        ["“10× faster” catalog updates", "No workflow benchmark supplied", "Measure batch size and approval time", `${m.approved} approved of ${m.generations} generations`],
        ["“High-converting” copy", "No conversion study supplied", "Run controlled experiment and report sample size", "Not measurable in this app - needs live A/B test"],
      ];
      $("#claims-table tbody").innerHTML = claims.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("");
    } catch (e) { toast(e.message, "bad"); }
  }
  $("#refresh-metrics").onclick = loadMetrics;

  async function loadVoiceOptions() {
    try {
      const { items } = await api("/api/voices");
      $("#f-voice").innerHTML = `<option value="">None</option>` + items.map((v) => `<option value="${v.id}">${esc(v.name)} (${esc(v.base_tone)})</option>`).join("");
      return items;
    } catch { return []; }
  }
  async function loadVoices() {
    const items = await loadVoiceOptions();
    $("#voice-list").innerHTML = items.length ? items.map((v) => `<div class="voice"><div class="voice-top"><b>${esc(v.name)}</b>
        <span><span class="tag acc">${esc(v.base_tone)}</span> <button class="btn ghost sm" data-del="${v.id}">Delete</button></span></div>
        ${v.rules ? `<p>${esc(v.rules)}</p>` : ""}${v.example ? `<p><i>“${esc(v.example)}”</i></p>` : ""}</div>`).join("")
      : `<div class="card empty">No saved voices yet. Create one to keep tone consistent across products.</div>`;
    $$("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/voices/${b.dataset.del}`, { method: "DELETE" }); toast("Voice deleted."); loadVoices(); }));
  }
  $("#voice-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/voices", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: val("#v-name"), base_tone: $("#v-tone").value, rules: val("#v-rules"), example: val("#v-example") }) });
      e.target.reset(); toast("Voice saved.", "good"); loadVoices();
    } catch (err) { toast(err.message || "Could not save voice.", "bad"); }
  };

  setMode("text-to-text");
  init();
})();
