(() => {
  "use strict";

  const PALETTE = ["#5fb3ff", "#ff8a5c", "#7ee787", "#c792ea", "#ffd166",
                    "#4dd0e1", "#f47fb0", "#a6e22e", "#9aa5ce", "#ff6f91"];

  const el = (id) => document.getElementById(id);

  const dom = {
    dirPath: el("dirPath"),
    fileSelect: el("fileSelect"),
    reloadFilesBtn: el("reloadFilesBtn"),
    statusDot: el("statusDot"),
    statusText: el("statusText"),
    saveBtn: el("saveBtn"),
    legend: el("legend"),
    zoomOutBtn: el("zoomOutBtn"),
    zoomInBtn: el("zoomInBtn"),
    fitBtn: el("fitBtn"),
    prevSegBtn: el("prevSegBtn"),
    nextSegBtn: el("nextSegBtn"),
    playheadReadout: el("playheadReadout"),
    searchInput: el("searchInput"),
    timelineScroll: el("timelineScroll"),
    timelineInner: el("timelineInner"),
    laneLabels: el("laneLabels"),
    ruler: el("ruler"),
    lanes: el("lanes"),
    playhead: el("playhead"),
    nowStripRows: el("nowStripRows"),
    editorEmpty: el("editorEmpty"),
    editorFields: el("editorFields"),
    segSpeakerSelect: el("segSpeakerSelect"),
    newSpeakerInput: el("newSpeakerInput"),
    addSpeakerBtn: el("addSpeakerBtn"),
    segStart: el("segStart"),
    segEnd: el("segEnd"),
    durationReadout: el("durationReadout"),
    segText: el("segText"),
    syncWarning: el("syncWarning"),
    resyncBtn: el("resyncBtn"),
    duplicateSegBtn: el("duplicateSegBtn"),
    deleteSegBtn: el("deleteSegBtn"),
    addSegBtn: el("addSegBtn"),
    toast: el("toast"),
  };

  const state = {
    dir: "",
    fileName: "",
    data: null,          // { segments: [...], ... }
    characters: {},       // { SPEAKER_XX: { name, color } }
    lanes: [],             // ordered list of speaker ids
    selectedSegment: null, // direct reference into data.segments
    playhead: 0,
    pxPerSec: 60,
    dirty: false,
    searchTerm: "",
  };

  // ---------------- utils ----------------
  function fmtTime(t) {
    if (!isFinite(t) || t < 0) t = 0;
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    return `${String(m).padStart(2, "0")}:${s.toFixed(3).padStart(6, "0")}`;
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function toast(msg, isError) {
    dom.toast.textContent = msg;
    dom.toast.hidden = false;
    dom.toast.className = "toast" + (isError ? " error" : "");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { dom.toast.hidden = true; }, 3200);
  }

  function markDirty(d = true) {
    state.dirty = d;
    dom.saveBtn.disabled = !d;
    dom.statusDot.className = "status-dot" + (d ? " dirty" : " saved");
    dom.statusText.textContent = state.fileName
      ? (d ? `${state.fileName} — modifications non enregistrées` : `${state.fileName} — à jour`)
      : "Aucun fichier chargé";
  }

  async function api(path, opts) {
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `Erreur ${res.status}`);
    return body;
  }

  // ---------------- speaker / character helpers ----------------
  function segSpeaker(seg) {
    if (seg.speaker) return seg.speaker;
    if (seg.words && seg.words.length) {
      const counts = {};
      for (const w of seg.words) {
        if (w.speaker) counts[w.speaker] = (counts[w.speaker] || 0) + 1;
      }
      let best = null, bestN = 0;
      for (const [k, n] of Object.entries(counts)) {
        if (n > bestN) { best = k; bestN = n; }
      }
      if (best) return best;
    }
    return "SANS_SPEAKER";
  }

  function ensureCharacter(id) {
    if (!state.characters[id]) {
      const n = Object.keys(state.characters).length;
      state.characters[id] = { name: id, color: PALETTE[n % PALETTE.length] };
      return true; // created new
    }
    return false;
  }

  async function persistCharacters() {
    try {
      await api("/api/characters", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state.characters),
      });
    } catch (e) {
      console.warn("Impossible d'enregistrer characters.json", e);
    }
  }

  function rebuildLanes() {
    if (!state.data || !state.data.segments) { state.lanes = []; return; }
    const seen = new Set();
    let created = false;
    for (const seg of state.data.segments) {
      const sp = segSpeaker(seg);
      if (!seen.has(sp)) {
        seen.add(sp);
        if (ensureCharacter(sp)) created = true;
      }
    }
    // ordre stable : d'abord les speakers déjà connus dans characters.json (ordre d'insertion),
    // puis ceux nouvellement découverts.
    const known = Object.keys(state.characters).filter((id) => seen.has(id));
    state.lanes = known;
    if (created) persistCharacters();
  }

  // ---------------- loading ----------------
  async function refreshStatus() {
    const s = await api("/api/status");
    state.dir = s.dir;
    dom.dirPath.textContent = s.dir;
  }

  async function refreshFileList(selectName) {
    const res = await api("/api/files");
    dom.fileSelect.innerHTML = '<option value="">— choisir un fichier —</option>';
    for (const f of res.files) {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      dom.fileSelect.appendChild(opt);
    }
    if (selectName) dom.fileSelect.value = selectName;
  }

  async function loadCharacters() {
    try {
      state.characters = await api("/api/characters");
    } catch (e) {
      state.characters = {};
    }
  }

  async function loadFile(name) {
    if (!name) return;
    const res = await api(`/api/load?file=${encodeURIComponent(name)}`);
    state.fileName = res.file;
    state.data = res.data;
    if (!Array.isArray(state.data.segments)) state.data.segments = [];
    state.selectedSegment = null;
    state.playhead = 0;
    rebuildLanes();
    fitZoom();
    renderAll();
    markDirty(false);
    dom.addSegBtn.disabled = false;
    toast(`Chargé : ${state.fileName}`);
  }

  async function saveFile() {
    if (!state.data || !state.fileName) return;
    // tri par heure de début + reconstruction de word_segments pour cohérence
    state.data.segments.sort((a, b) => (a.start ?? 0) - (b.start ?? 0));
    const flat = [];
    for (const seg of state.data.segments) {
      if (Array.isArray(seg.words)) flat.push(...seg.words);
    }
    state.data.word_segments = flat;
    try {
      await api("/api/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: state.fileName, data: state.data }),
      });
      markDirty(false);
      renderTimeline(); // l'ordre a pu changer après le tri
      toast("Enregistré ✓");
    } catch (e) {
      toast("Échec de l'enregistrement : " + e.message, true);
    }
  }

  // ---------------- zoom / time <-> px ----------------
  function totalDuration() {
    let max = 5;
    if (state.data && state.data.segments) {
      for (const seg of state.data.segments) max = Math.max(max, seg.end || 0);
    }
    return max + 3;
  }
  function timeToX(t) { return t * state.pxPerSec; }
  function xToTime(x) { return Math.max(0, x / state.pxPerSec); }

  function fitZoom() {
    const w = dom.timelineScroll.clientWidth || 800;
    state.pxPerSec = clamp(w / totalDuration(), 8, 400);
  }

  // ---------------- rendering ----------------
  function renderAll() {
    renderLegend();
    renderTimeline();
    renderNowStrip();
    renderEditor();
  }

  function renderLegend() {
    dom.legend.innerHTML = "";
    for (const id of state.lanes) {
      const meta = state.characters[id] || { name: id, color: "#888" };
      const chip = document.createElement("div");
      chip.className = "legend-chip";
      const dot = document.createElement("span");
      dot.className = "legend-swatch";
      dot.style.background = meta.color;
      const idSpan = document.createElement("span");
      idSpan.className = "legend-id";
      idSpan.textContent = id;
      const input = document.createElement("input");
      input.type = "text";
      input.value = meta.name;
      input.title = "Nom affiché pour " + id;
      input.addEventListener("change", () => {
        meta.name = input.value.trim() || id;
        persistCharacters();
        renderTimeline();
        renderNowStrip();
        renderEditorSpeakerOptions();
      });
      chip.appendChild(dot);
      chip.appendChild(input);
      chip.appendChild(idSpan);
      dom.legend.appendChild(chip);
    }
  }

  function renderRuler() {
    const dur = totalDuration();
    const width = timeToX(dur);
    dom.timelineInner.style.width = width + "px";
    dom.ruler.innerHTML = "";
    dom.ruler.style.width = width + "px";

    let step;
    if (state.pxPerSec >= 140) step = 1;
    else if (state.pxPerSec >= 60) step = 2;
    else if (state.pxPerSec >= 25) step = 5;
    else if (state.pxPerSec >= 10) step = 15;
    else step = 30;

    for (let t = 0; t <= dur; t += step) {
      const tick = document.createElement("div");
      tick.className = "ruler-tick major";
      tick.style.left = timeToX(t) + "px";
      const label = document.createElement("span");
      const m = Math.floor(t / 60), s = Math.floor(t % 60);
      label.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      tick.appendChild(label);
      dom.ruler.appendChild(tick);
    }
  }

  function renderLaneLabels() {
    dom.laneLabels.innerHTML = '<div class="lane-labels-spacer"></div>';
    for (const id of state.lanes) {
      const meta = state.characters[id] || { name: id, color: "#888" };
      const row = document.createElement("div");
      row.className = "lane-label-row";
      const dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = meta.color;
      const lbl = document.createElement("span");
      lbl.className = "lbl";
      lbl.textContent = meta.name;
      lbl.title = id;
      row.appendChild(dot);
      row.appendChild(lbl);
      dom.laneLabels.appendChild(row);
    }
  }

  function renderTimeline() {
    if (!state.data) return;
    renderRuler();
    renderLaneLabels();

    const dur = totalDuration();
    dom.lanes.innerHTML = "";
    dom.lanes.style.width = timeToX(dur) + "px";

    const term = state.searchTerm.trim().toLowerCase();

    state.lanes.forEach((laneId) => {
      const lane = document.createElement("div");
      lane.className = "lane";
      lane.dataset.laneId = laneId;
      lane.style.width = timeToX(dur) + "px";
      lane.addEventListener("mousedown", (ev) => {
        if (ev.target === lane) setPlayheadFromClientX(ev.clientX);
      });

      const segs = state.data.segments.filter((s) => segSpeaker(s) === laneId);
      for (const seg of segs) {
        const meta = state.characters[laneId] || { color: "#888" };
        const block = document.createElement("div");
        block.className = "segment";
        if (seg === state.selectedSegment) block.classList.add("selected");
        if (term && !(seg.text || "").toLowerCase().includes(term)) block.classList.add("dim");
        block.style.left = timeToX(seg.start || 0) + "px";
        block.style.width = Math.max(6, timeToX((seg.end || 0) - (seg.start || 0))) + "px";
        block.style.background = meta.color;
        block.textContent = seg.text || "(vide)";
        block.title = `${fmtTime(seg.start)} → ${fmtTime(seg.end)}\n${seg.text || ""}`;

        const handleL = document.createElement("div");
        handleL.className = "handle left";
        const handleR = document.createElement("div");
        handleR.className = "handle right";
        block.appendChild(handleL);
        block.appendChild(handleR);

        block.addEventListener("mousedown", (ev) => onSegmentMouseDown(ev, seg, block));
        lane.appendChild(block);
      }
      dom.lanes.appendChild(lane);
    });

    dom.playhead.style.left = timeToX(state.playhead) + "px";
    dom.playheadReadout.textContent = fmtTime(state.playhead);
  }

  function renderNowStrip() {
    dom.nowStripRows.innerHTML = "";
    if (!state.data) return;
    const t = state.playhead;
    for (const laneId of state.lanes) {
      const meta = state.characters[laneId] || { name: laneId, color: "#888" };
      const segs = state.data.segments.filter((s) => segSpeaker(s) === laneId);
      let current = segs.find((s) => t >= (s.start || 0) && t < (s.end || 0));
      let text, silent = false;
      if (current) {
        text = current.text || "(vide)";
      } else {
        const upcoming = segs
          .filter((s) => (s.start || 0) >= t)
          .sort((a, b) => a.start - b.start)[0];
        if (upcoming) {
          text = `… silence — prochaine réplique dans ${(upcoming.start - t).toFixed(1)}s : "${upcoming.text || ""}"`;
        } else {
          text = "— silence —";
        }
        silent = true;
      }
      const row = document.createElement("div");
      row.className = "now-row" + (silent ? " silent" : "");
      const dot = document.createElement("span");
      dot.className = "now-dot";
      dot.style.background = meta.color;
      const name = document.createElement("span");
      name.className = "now-name";
      name.textContent = meta.name;
      const txt = document.createElement("span");
      txt.className = "now-text";
      txt.textContent = text;
      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(txt);
      if (current) row.addEventListener("click", () => selectSegment(current));
      dom.nowStripRows.appendChild(row);
    }
  }

  function renderEditorSpeakerOptions() {
    const sel = dom.segSpeakerSelect;
    const current = state.selectedSegment ? segSpeaker(state.selectedSegment) : null;
    sel.innerHTML = "";
    for (const id of state.lanes) {
      const meta = state.characters[id] || { name: id };
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = `${meta.name} (${id})`;
      if (id === current) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  function renderEditor() {
    const seg = state.selectedSegment;
    if (!seg) {
      dom.editorEmpty.hidden = false;
      dom.editorFields.hidden = true;
      return;
    }
    dom.editorEmpty.hidden = true;
    dom.editorFields.hidden = false;
    renderEditorSpeakerOptions();
    dom.segStart.value = (seg.start ?? 0).toFixed(3);
    dom.segEnd.value = (seg.end ?? 0).toFixed(3);
    dom.durationReadout.textContent = `durée : ${((seg.end || 0) - (seg.start || 0)).toFixed(3)} s`;
    dom.segText.value = seg.text || "";
    updateSyncWarning();
  }

  function updateSyncWarning() {
    const seg = state.selectedSegment;
    if (!seg) return;
    const fromWords = (seg.words || []).map((w) => w.word).join(" ").replace(/\s+/g, " ").trim();
    const fromText = (seg.text || "").replace(/\s+/g, " ").trim();
    dom.syncWarning.hidden = !(fromWords && fromText && fromWords !== fromText);
  }

  // ---------------- selection / editing ----------------
  function selectSegment(seg) {
    state.selectedSegment = seg;
    renderTimeline();
    renderEditor();
  }

  function deselect() {
    state.selectedSegment = null;
    renderTimeline();
    renderEditor();
  }

  function setPlayheadFromClientX(clientX) {
    const rect = dom.timelineInner.getBoundingClientRect();
    const x = clientX - rect.left;
    state.playhead = xToTime(x);
    dom.playhead.style.left = timeToX(state.playhead) + "px";
    dom.playheadReadout.textContent = fmtTime(state.playhead);
    renderNowStrip();
  }

  function onSegmentMouseDown(ev, seg, block) {
    ev.preventDefault();
    ev.stopPropagation();
    selectSegment(seg);

    const isLeftHandle = ev.target.classList.contains("handle") && ev.target.classList.contains("left");
    const isRightHandle = ev.target.classList.contains("handle") && ev.target.classList.contains("right");
    const startX = ev.clientX;
    const origStart = seg.start || 0;
    const origEnd = seg.end || 0;

    function onMove(mv) {
      const dt = (mv.clientX - startX) / state.pxPerSec;
      if (isLeftHandle) {
        seg.start = clamp(origStart + dt, 0, origEnd - 0.02);
      } else if (isRightHandle) {
        seg.end = Math.max(origStart + 0.02, origEnd + dt);
      } else {
        const dur = origEnd - origStart;
        let ns = clamp(origStart + dt, 0, Infinity);
        seg.start = ns;
        seg.end = ns + dur;
      }
      markDirty();
      renderTimeline();
      if (state.selectedSegment === seg) renderEditor();
      renderNowStrip();
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  function regenerateWords(seg) {
    const tokens = (seg.text || "").split(/\s+/).filter(Boolean);
    const start = seg.start || 0, end = seg.end || start;
    const dur = Math.max(end - start, 0.01);
    const per = dur / Math.max(tokens.length, 1);
    seg.words = tokens.map((tok, i) => ({
      word: tok,
      start: +(start + i * per).toFixed(3),
      end: +(start + (i + 1) * per).toFixed(3),
      score: 1.0,
      speaker: seg.speaker,
    }));
  }

  // ---------------- events ----------------
  function wireEvents() {
    dom.fileSelect.addEventListener("change", () => {
      const name = dom.fileSelect.value;
      if (state.dirty && !confirm("Des modifications non enregistrées seront perdues. Continuer ?")) {
        dom.fileSelect.value = state.fileName;
        return;
      }
      if (name) loadFile(name);
    });

    dom.reloadFilesBtn.addEventListener("click", () => refreshFileList(state.fileName));
    dom.saveBtn.addEventListener("click", saveFile);

    dom.zoomInBtn.addEventListener("click", () => { state.pxPerSec = clamp(state.pxPerSec * 1.4, 4, 800); renderTimeline(); });
    dom.zoomOutBtn.addEventListener("click", () => { state.pxPerSec = clamp(state.pxPerSec / 1.4, 4, 800); renderTimeline(); });
    dom.fitBtn.addEventListener("click", () => { fitZoom(); renderTimeline(); });

    dom.timelineScroll.addEventListener("mousedown", (ev) => {
      if (ev.target === dom.ruler || ev.target.classList.contains("ruler-tick") || ev.target.parentElement === dom.ruler) {
        setPlayheadFromClientX(ev.clientX);
      }
    });
    dom.ruler.addEventListener("mousedown", (ev) => setPlayheadFromClientX(ev.clientX));

    dom.prevSegBtn.addEventListener("click", () => jumpSegment(-1));
    dom.nextSegBtn.addEventListener("click", () => jumpSegment(1));

    dom.searchInput.addEventListener("input", () => {
      state.searchTerm = dom.searchInput.value;
      renderTimeline();
    });

    dom.segSpeakerSelect.addEventListener("change", () => {
      if (!state.selectedSegment) return;
      state.selectedSegment.speaker = dom.segSpeakerSelect.value;
      markDirty();
      renderTimeline();
      renderNowStrip();
    });

    dom.addSpeakerBtn.addEventListener("click", () => {
      const id = dom.newSpeakerInput.value.trim();
      if (!id) return;
      ensureCharacter(id);
      if (!state.lanes.includes(id)) state.lanes.push(id);
      persistCharacters();
      dom.newSpeakerInput.value = "";
      if (state.selectedSegment) {
        state.selectedSegment.speaker = id;
        markDirty();
      }
      renderLegend();
      renderTimeline();
      renderEditor();
    });

    dom.segStart.addEventListener("change", () => {
      if (!state.selectedSegment) return;
      const v = parseFloat(dom.segStart.value);
      if (!isNaN(v)) state.selectedSegment.start = clamp(v, 0, (state.selectedSegment.end || v) - 0.01);
      markDirty();
      renderTimeline(); renderEditor(); renderNowStrip();
    });
    dom.segEnd.addEventListener("change", () => {
      if (!state.selectedSegment) return;
      const v = parseFloat(dom.segEnd.value);
      if (!isNaN(v)) state.selectedSegment.end = Math.max(v, (state.selectedSegment.start || 0) + 0.01);
      markDirty();
      renderTimeline(); renderEditor(); renderNowStrip();
    });

    dom.segText.addEventListener("input", () => {
      if (!state.selectedSegment) return;
      state.selectedSegment.text = dom.segText.value;
      markDirty();
      updateSyncWarning();
      renderTimeline();
      renderNowStrip();
    });

    dom.resyncBtn.addEventListener("click", () => {
      if (!state.selectedSegment) return;
      regenerateWords(state.selectedSegment);
      markDirty();
      updateSyncWarning();
      toast("Mots régénérés (répartition uniforme sur la durée du segment)");
    });

    dom.duplicateSegBtn.addEventListener("click", () => {
      const seg = state.selectedSegment;
      if (!seg) return;
      const dur = (seg.end || 0) - (seg.start || 0);
      const copy = JSON.parse(JSON.stringify(seg));
      copy.start = seg.end;
      copy.end = seg.end + dur;
      state.data.segments.push(copy);
      markDirty();
      rebuildLanes();
      selectSegment(copy);
      renderAll();
    });

    dom.deleteSegBtn.addEventListener("click", () => {
      const seg = state.selectedSegment;
      if (!seg) return;
      if (!confirm("Supprimer ce segment ?")) return;
      const idx = state.data.segments.indexOf(seg);
      if (idx >= 0) state.data.segments.splice(idx, 1);
      state.selectedSegment = null;
      markDirty();
      rebuildLanes();
      renderAll();
    });

    dom.addSegBtn.addEventListener("click", () => {
      if (!state.data) return;
      const laneId = state.selectedSegment ? segSpeaker(state.selectedSegment) : (state.lanes[0] || "SPEAKER_00");
      ensureCharacter(laneId);
      const newSeg = {
        start: +state.playhead.toFixed(3),
        end: +(state.playhead + 1).toFixed(3),
        text: "",
        speaker: laneId,
        words: [],
      };
      state.data.segments.push(newSeg);
      markDirty();
      rebuildLanes();
      selectSegment(newSeg);
      renderAll();
      dom.segText.focus();
    });

    document.addEventListener("keydown", (ev) => {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (ev.key === "ArrowLeft") { state.playhead = Math.max(0, state.playhead - (ev.shiftKey ? 1 : 0.1)); renderTimeline(); renderNowStrip(); }
      else if (ev.key === "ArrowRight") { state.playhead = state.playhead + (ev.shiftKey ? 1 : 0.1); renderTimeline(); renderNowStrip(); }
      else if (ev.key === "Delete" || ev.key === "Backspace") { if (state.selectedSegment) dom.deleteSegBtn.click(); }
      else if ((ev.key === "s" || ev.key === "S") && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); saveFile(); }
    });

    window.addEventListener("beforeunload", (ev) => {
      if (state.dirty) { ev.preventDefault(); ev.returnValue = ""; }
    });
  }

  function jumpSegment(dir) {
    if (!state.data) return;
    const all = state.data.segments.slice().sort((a, b) => (a.start || 0) - (b.start || 0));
    let target = null;
    if (dir > 0) target = all.find((s) => (s.start || 0) > state.playhead + 0.001);
    else target = all.slice().reverse().find((s) => (s.start || 0) < state.playhead - 0.001);
    if (target) {
      state.playhead = target.start || 0;
      selectSegment(target);
      renderTimeline();
      renderNowStrip();
    }
  }

  // ---------------- init ----------------
  async function init() {
    wireEvents();
    try {
      await refreshStatus();
      await loadCharacters();
      await refreshFileList();
    } catch (e) {
      toast("Impossible de contacter le serveur local : " + e.message, true);
    }
  }

  init();
})();
