/* VStarcam local — interfaz */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const tpl = $("#tile-tpl");

  const state = {
    cameras: [],
    status: null,
    tiles: new Map(),
    focusId: null,
    eventsOpen: false,
    lastSeenEvent: Number(localStorage.getItem("vs_seen_event") || 0),
    sound: localStorage.getItem("vs_sound") === "1",
    pollTimer: null,
  };

  const esc = (s) =>
    String(s === undefined || s === null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  /* ------------------------------------------------------------------ API */
  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
    if (opts.body !== undefined) {
      init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
      init.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, init);
    if (res.status === 401) {
      location.href = "/login";
      throw new Error("Sesión caducada");
    }
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("json") ? await res.json() : await res.text();
    if (!res.ok) {
      const msg = data && data.error ? data.error : "HTTP " + res.status;
      throw new Error(msg);
    }
    return data;
  }

  /* --------------------------------------------------------------- toasts */
  function toast(message, kind, extraHtml) {
    const box = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.innerHTML =
      '<span class="ico">' +
      (kind === "ok" ? "✔" : kind === "err" ? "⚠" : "ℹ") +
      '</span><span class="txt"></span><button class="close" title="Cerrar">✕</button>';
    el.querySelector(".txt").textContent = message;
    if (extraHtml) el.querySelector(".txt").insertAdjacentHTML("beforeend", extraHtml);
    el.querySelector(".close").onclick = () => el.remove();
    box.appendChild(el);
    setTimeout(() => el.remove(), kind === "err" ? 9000 : 5000);
  }

  /* -------------------------------------------------------------- helpers */
  window.addEventListener("error", (ev) => {
    if (ev && ev.message) toast("Error de interfaz: " + ev.message, "err");
  });
  window.addEventListener("unhandledrejection", (ev) => {
    console.error("Promesa rechazada:", ev.reason);
  });

  function fmtTime(ts) {
    return new Date(ts * 1000).toLocaleString("es-ES", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }
  function fmtAgo(ts) {
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 60) return "hace " + s + " s";
    if (s < 3600) return "hace " + Math.floor(s / 60) + " min";
    if (s < 86400) return "hace " + Math.floor(s / 3600) + " h";
    return "hace " + Math.floor(s / 86400) + " d";
  }
  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
    return n.toFixed(n >= 10 ? 0 : 1) + " " + units[i];
  }

  function setDeep(obj, key, value) {
    const parts = key.split(".");
    let node = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!node[parts[i]] || typeof node[parts[i]] !== "object") node[parts[i]] = {};
      node = node[parts[i]];
    }
    node[parts[parts.length - 1]] = value;
  }

  /* ----------------------------------------------------------------- PTZ */
  async function sendPtz(camId, payload) {
    try {
      return await api("/api/ptz/" + encodeURIComponent(camId), { method: "POST", body: payload });
    } catch (err) {
      toast("PTZ: " + err.message, "err");
      throw err;
    }
  }

  function ptzStart(t, action) {
    if (action === "home") {
      sendPtz(t.cam.id, { action: "home" }).catch(() => {});
      return;
    }
    const speed = Number(t.el.querySelector(".speed").value) / 100;
    const fire = () => sendPtz(t.cam.id, { action, speed, duration: 0.75 }).catch(() => {});
    clearInterval(t.holdTimer);
    fire();
    t.holdTimer = setInterval(fire, 420);
  }
  function ptzStop(t, action) {
    clearInterval(t.holdTimer);
    t.holdTimer = null;
    if (action && action !== "home") {
      sendPtz(t.cam.id, { action: "stop" }).catch(() => {});
    }
  }

  /* ------------------------------------------------------------- streaming */
  function stopStream(t) {
    t.stopped = true;
    if (t.stream) { t.stream.stop(); t.stream = null; }
    if (t.fallbackTimer) { clearTimeout(t.fallbackTimer); t.fallbackTimer = null; }
  }

  function startStream(t) {
    stopStream(t);
    t.stopped = false;
    t.el.classList.remove("playing", "offline");
    t.img.hidden = true;
    t.video.style.display = "";
    t.phText.textContent = "Conectando…";
    t.retry.hidden = true;

    if (!t.cam.rtsp_url) {
      t.mode = null;
      t.phText.textContent = "Sin URL RTSP configurada";
      t.el.classList.add("offline");
      return;
    }

    const useWebrtc = state.status && state.status.go2rtc && state.status.go2rtc.enabled;
    if (useWebrtc && window.VSStream) {
      t.mode = "webrtc";
      t.stream = new window.VSStream(t.video, t.cam.id, { onState: (st, msg) => onStreamState(t, st, msg) });
      t.stream.start();
    } else {
      startFallback(t);
    }
  }

  function onStreamState(t, st, msg) {
    if (t.mode !== "webrtc") return;
    if (st === "playing") {
      t.el.classList.add("playing");
      t.el.classList.remove("offline");
      t.phText.textContent = "En directo";
      updateAudioHint(t);
    } else if (st === "connecting" || st === "reconnecting") {
      t.phText.textContent = st === "connecting" ? "Conectando…" : "Reconectando…";
      t.el.classList.remove("playing");
    } else if (st === "failed") {
      t.phText.textContent = "Sin respuesta, reintentando…";
      t.el.classList.add("offline");
    } else if (st === "unavailable" || st === "unsupported") {
      t.el.classList.add("offline");
      t.phText.textContent = "WebRTC no disponible, usando imágenes…";
      startFallback(t, true);
    }
  }

  function startFallback(t, silent) {
    if (t.stream) { t.stream.stop(); t.stream = null; }
    t.mode = "fallback";
    if (t.fallbackTimer) clearTimeout(t.fallbackTimer);
    t.video.style.display = "none";
    t.img.hidden = false;
    t.el.classList.remove("playing");
    t.phText.textContent = "Conectando…";

    let failures = 0;
    const tick = () => {
      if (t.stopped) return;
      const src = "/api/cameras/" + encodeURIComponent(t.cam.id) + "/snapshot.jpg?_=" + Date.now();
      const probe = new Image();
      probe.onload = () => {
        if (t.stopped) return;
        t.img.src = src;
        t.el.classList.add("playing");
        t.el.classList.remove("offline");
        failures = 0;
      };
      probe.onerror = () => {
        if (t.stopped) return;
        failures++;
        if (failures >= 2) {
          t.el.classList.add("offline");
          t.el.classList.remove("playing");
          t.phText.textContent = "No se pudo conectar con la cámara";
          t.retry.hidden = false;
        }
      };
      probe.src = src;
      t.fallbackTimer = setTimeout(tick, 1300);
    };
    tick();
    if (!silent) {
      // nada: se muestra el modo imagen sin avisar
    }
  }

  function setAudio(t, on) {
    t.video.muted = !on;
    t.el.querySelector('[data-act="audio"]').classList.toggle("on", on);
    if (on) t.video.play().catch(() => {});
    localStorage.setItem("vs_sound", on ? "1" : "0");
    updateAudioHint(t);
  }

  function updateAudioHint(t) {
    const hasAudio = !!(t.video.srcObject && t.video.srcObject.getAudioTracks && t.video.srcObject.getAudioTracks().length);
    t.unmute.hidden = !(hasAudio && t.video.muted && t.mode === "webrtc" && t.el.classList.contains("playing"));
    t.el.querySelector('[data-act="audio"]').classList.toggle("on", !t.video.muted);
  }

  /* ----------------------------------------------------------------- tiles */
  // Cámaras cuyo PTZ solo funciona por el CGI propietario (sin zoom/presets).
  function applyPtzCaps(t) {
    const cgi = String(t.cam.ptz_driver || "").toLowerCase() === "cgi";
    t.ptz.classList.toggle("simple", cgi);
    const zoomer = $(".zoomer", t.el);
    const presets = $(".presets-row", t.el);
    if (zoomer) zoomer.hidden = cgi;
    if (presets) presets.hidden = cgi;
  }

  function makeTile(cam) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    const t = {
      cam: cam,
      el: node,
      video: $(".stream", node),
      img: $(".fallback", node),
      phText: $(".ph-text", node),
      retry: $(".retry", node),
      unmute: $(".unmute", node),
      ptz: $(".ptz", node),
      stream: null,
      mode: null,
      stopped: true,
      fallbackTimer: null,
      holdTimer: null,
    };
    node.dataset.id = cam.id;
    $(".name", node).textContent = cam.name;
    $(".meta", node).textContent = cam.host || "";
    applyPtzCaps(t);
    t.video.muted = !state.sound;

    // Herramientas
    $$(".tbtn", node).forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const act = btn.dataset.act;
        if (act === "audio") {
          setAudio(t, t.video.muted);
        } else if (act === "ptz") {
          const show = t.ptz.hidden;
          t.ptz.hidden = !show;
          btn.classList.toggle("on", show);
          if (show && !t.ptz.classList.contains("simple")) loadPresets(t);
        } else if (act === "snapshot") {
          takeSnapshot(t);
        } else if (act === "record") {
          toggleRecord(t);
        } else if (act === "edit") {
          cameraForm(t.cam, {});
        } else if (act === "focus") {
          setFocus(state.focusId === t.cam.id ? null : t.cam.id);
        }
      });
    });

    // Menú contextual de "pantalla completa" con doble clic
    $(".viewport", node).addEventListener("dblclick", () => {
      const vp = $(".viewport", node);
      if (document.fullscreenElement) document.exitFullscreen();
      else if (vp.requestFullscreen) vp.requestFullscreen();
    });

    t.unmute.addEventListener("click", () => setAudio(t, true));
    t.retry.addEventListener("click", (ev) => {
      ev.stopPropagation();
      t.retry.hidden = true;
      startStream(t);
    });

    // PTZ: mantener pulsado para mover
    $$("[data-move]", node).forEach((btn) => {
      const action = btn.dataset.move;
      const down = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        btn.classList.add("pressed");
        ptzStart(t, action);
      };
      const up = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        btn.classList.remove("pressed");
        ptzStop(t, action);
      };
      btn.addEventListener("pointerdown", down);
      btn.addEventListener("pointerup", up);
      btn.addEventListener("pointerleave", up);
      btn.addEventListener("pointercancel", up);
      btn.addEventListener("contextmenu", (ev) => ev.preventDefault());
    });

    $(".preset-select", node).addEventListener("change", (ev) => {
      const token = ev.target.value;
      if (!token) return;
      sendPtz(t.cam.id, { action: "preset", preset: token }).catch(() => {});
    });

    $(".preset-save", node).addEventListener("click", async () => {
      const name = prompt("Nombre del preset (posición actual):", "Posición " + (t.el.querySelectorAll(".preset-select option").length));
      if (!name) return;
      try {
        await api("/api/cameras/" + encodeURIComponent(t.cam.id) + "/presets", {
          method: "POST", body: { name },
        });
        toast("Preset guardado: " + name, "ok");
        loadPresets(t);
      } catch (err) { toast(err.message, "err"); }
    });

    // Teclado cuando la cámara tiene foco
    node.tabIndex = 0;

    return t;
  }

  async function loadPresets(t) {
    const select = t.el.querySelector(".preset-select");
    if (select.dataset.loaded === "1") return;
    try {
      const res = await api("/api/cameras/" + encodeURIComponent(t.cam.id) + "/presets");
      select.innerHTML = '<option value="">— Presets —</option>' +
        (res.presets || []).map((p) => `<option value="${esc(p.token)}">${esc(p.name)}</option>`).join("");
      select.dataset.loaded = "1";
    } catch (err) {
      // Cámara sin PTZ ONVIF: dejamos el selector vacío sin molestar
      select.innerHTML = '<option value="">Sin presets</option>';
    }
  }

  async function takeSnapshot(t) {
    try {
      const res = await api("/api/cameras/" + encodeURIComponent(t.cam.id) + "/snapshot", { method: "POST" });
      toast("Captura guardada", "ok", ` · <a href="${res.url}" target="_blank" rel="noopener" style="color:#93c5fd">abrir</a>`);
      refreshEvents();
    } catch (err) { toast("Captura: " + err.message, "err"); }
  }

  async function toggleRecord(t) {
    const on = t.el.classList.contains("recording");
    try {
      await api("/api/record/" + encodeURIComponent(t.cam.id) + "/" + (on ? "stop" : "start"), { method: "POST" });
      t.el.classList.toggle("recording", !on);
      toast(on ? "Grabación detenida" : "Grabación iniciada", "ok");
    } catch (err) { toast("Grabación: " + err.message, "err"); }
  }

  function setFocus(camId) {
    state.focusId = camId;
    state.tiles.forEach((t, id) => {
      t.el.classList.toggle("focus", id === camId);
      if (id === camId) {
        t.el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  /* --------------------------------------------------------------- render */
  function renderTiles() {
    const grid = $("#grid");
    const empty = $("#empty");
    const has = state.cameras.length > 0;
    empty.hidden = has;
    grid.style.display = has ? "" : "none";
    grid.classList.toggle("single", state.cameras.length === 1);

    const ids = new Set(state.cameras.map((c) => c.id));
    state.tiles.forEach((t, id) => {
      if (!ids.has(id)) {
        stopStream(t);
        t.el.remove();
        state.tiles.delete(id);
      }
    });

    state.cameras.forEach((cam, index) => {
      let t = state.tiles.get(cam.id);
      if (!t) {
        t = makeTile(cam);
        state.tiles.set(cam.id, t);
        grid.appendChild(t.el);
      } else {
        const oldUrl = t.cam.rtsp_url;
        t.cam = cam;
        $(".name", t.el).textContent = cam.name;
        $(".meta", t.el).textContent = cam.host || "";
        applyPtzCaps(t);
        if (oldUrl !== cam.rtsp_url) {
          t.el.querySelector(".preset-select").dataset.loaded = "";
          startStream(t);
        }
      }
      if (grid.children[index] !== t.el) grid.insertBefore(t.el, grid.children[index] || null);
    });

    state.cameras.forEach((cam) => {
      const t = state.tiles.get(cam.id);
      if (t && t.stopped && !t.el.classList.contains("offline")) startStream(t);
    });
  }

  function syncStatus() {
    if (!state.status) return;
    const conn = $("#conn");
    conn.classList.remove("ok", "bad");
    conn.classList.add("ok");
    $(".conn-text", conn).textContent = "en línea";

    (state.status.cameras || []).forEach((s) => {
      const t = state.tiles.get(s.id);
      if (!t) return;
      if (s.recording) t.el.classList.add("recording");
      else t.el.classList.remove("recording");
    });
  }

  async function refreshStatus() {
    try {
      state.status = await api("/api/status");
      syncStatus();
    } catch (err) {
      const conn = $("#conn");
      conn.classList.remove("ok");
      conn.classList.add("bad");
      $(".conn-text", conn).textContent = "sin conexión";
    }
  }

  async function loadCameras() {
    const res = await api("/api/cameras");
    state.cameras = res.cameras || [];
    renderTiles();
    const filter = $("#events-filter");
    filter.innerHTML = '<option value="">Todas</option>' +
      state.cameras.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  }

  /* --------------------------------------------------------------- eventos */
  const CATEGORY_LABELS = {
    persona: "Persona", vehiculo: "Vehículo", animal: "Animal",
    objeto: "Objeto", movimiento: "Movimiento", snapshot: "Captura",
  };

  function eventCategory(e) {
    const cat = e.meta && e.meta.category;
    if (cat) return cat;
    return e.type === "snapshot" ? "snapshot" : "movimiento";
  }

  function fmtDuration(e) {
    const s = Number(e.meta && e.meta.duration);
    if (!s || s < 1) return "";
    if (s < 60) return Math.round(s) + " s";
    return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  }

  async function refreshEvents() {
    let events = [];
    try {
      const res = await api("/api/events?limit=60");
      events = res.events || [];
    } catch (err) { return; }

    const unseen = events.filter((e) => e.ts > state.lastSeenEvent).length;
    const pill = $("#events-count");
    if (unseen > 0 && !state.eventsOpen) {
      pill.hidden = false;
      pill.textContent = unseen > 99 ? "99+" : unseen;
    } else {
      pill.hidden = true;
    }

    if (!state.eventsOpen) return;
    const camFilter = $("#events-filter").value;
    const kindFilter = $("#events-kind").value;
    const list = events.filter((e) =>
      (!camFilter || e.camera_id === camFilter) &&
      (!kindFilter || eventCategory(e) === kindFilter)
    );
    const box = $("#events-list");
    if (!list.length) {
      box.innerHTML = '<div class="events-empty">Sin eventos todavía.<br>Activa la detección de movimiento en una cámara.</div>';
      return;
    }
    box.innerHTML = list.map((e) => {
      const cam = state.cameras.find((c) => c.id === e.camera_id);
      const title = cam ? cam.name : e.camera_id;
      const img = e.snapshot_url
        ? `<img src="${esc(e.snapshot_url)}" alt="" loading="lazy">`
        : '<div class="noimg">sin foto</div>';
      const kind = eventCategory(e);
      const kindLabel = CATEGORY_LABELS[kind] || e.type;
      const desc = e.description || e.message || "";
      const details = [fmtTime(e.ts), fmtAgo(e.ts), fmtDuration(e)].filter(Boolean).join(" · ");
      return `<div class="event" data-snap="${esc(e.snapshot_url || "")}" data-clip="${esc(e.clip_url || "")}">
        ${img}
        <div class="body">
          <div class="title">${esc(title)}</div>
          ${desc ? `<div class="desc">${esc(desc)}</div>` : ""}
          <div class="sub">${esc(details)}</div>
        </div>
        <span class="kind ${esc(kind)}">${esc(kindLabel)}</span>
      </div>`;
    }).join("");

    const evs = [];
    $$(".event", box).forEach((el) => {
      evs.push({snap: el.dataset.snap, clip: el.dataset.clip});
      el.addEventListener("click", () => {
        if (el.dataset.clip) {
          playMedia(el.dataset.clip, "Evento");
          return;
        }
        if (el.dataset.snap) {
          openLightbox(evs.filter(e => e.snap).map(e => e.snap), evs.filter(e => e.snap).findIndex(e => e.snap === el.dataset.snap));
        }
      });
    });
  }

  function openEvents() {
    state.eventsOpen = true;
    $("#events-drawer").classList.add("open");
    $("#events-drawer").setAttribute("aria-hidden", "false");
    $("#drawer-backdrop").hidden = false;
    state.lastSeenEvent = Math.floor(Date.now() / 1000);
    localStorage.setItem("vs_seen_event", String(state.lastSeenEvent));
    $("#events-count").hidden = true;
    refreshEvents();
  }
  function closeEvents() {
    state.eventsOpen = false;
    $("#events-drawer").classList.remove("open");
    $("#events-drawer").setAttribute("aria-hidden", "true");
    $("#drawer-backdrop").hidden = true;
  }

  /* ------------------------------------------------------------- modales */
  let escHandler = null;
  function openModal(opts) {
    closeModal();
    const root = $("#modal-root");
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.innerHTML = `<div class="modal ${opts.wide ? "wide" : ""}" role="dialog" aria-modal="true">
      <div class="modal-head"><h2></h2><button class="btn ghost icon-only x" title="Cerrar">✕</button></div>
      <div class="modal-body"></div>
      <div class="modal-foot"></div></div>`;
    $("h2", overlay).textContent = opts.title;
    $(".modal-body", overlay).innerHTML = opts.body || "";
    const foot = $(".modal-foot", overlay);
    if (opts.footer) foot.innerHTML = opts.footer;
    else foot.remove();
    $(".x", overlay).onclick = closeModal;
    overlay.addEventListener("mousedown", (ev) => { if (ev.target === overlay) closeModal(); });
    root.appendChild(overlay);
    escHandler = (ev) => { if (ev.key === "Escape") closeModal(); };
    document.addEventListener("keydown", escHandler);
    if (opts.onMount) opts.onMount(overlay);
    const first = $("input, select, textarea", overlay);
    if (first && opts.autofocus !== false) setTimeout(() => first.focus(), 40);
    return overlay;
  }
  function closeModal() {
    $("#modal-root").innerHTML = "";
    if (escHandler) document.removeEventListener("keydown", escHandler);
    escHandler = null;
  }

  function openLightbox(images, startIndex) {
    if (!images || !images.length) return;
    let idx = startIndex >= 0 ? startIndex : 0;
    const overlay = document.createElement("div");
    overlay.className = "overlay";
    overlay.innerHTML = `<div class="lightbox" role="dialog" aria-modal="true">
      <button class="lb-btn lb-close" title="Cerrar">✕</button>
      <button class="lb-btn lb-prev" title="Anterior">←</button>
      <img class="lb-img" src="" alt="">
      <button class="lb-btn lb-next" title="Siguiente">→</button>
      <div class="lb-counter"></div>
    </div>`;
    const img = overlay.querySelector(".lb-img");
    const counter = overlay.querySelector(".lb-counter");
    const prev = overlay.querySelector(".lb-prev");
    const next = overlay.querySelector(".lb-next");
    const close = overlay.querySelector(".lb-close");

    function render() {
      img.src = images[idx];
      counter.textContent = (idx + 1) + " / " + images.length;
      prev.style.display = images.length > 1 ? "" : "none";
      next.style.display = images.length > 1 ? "" : "none";
    }

    prev.onclick = (e) => { e.stopPropagation(); idx = (idx - 1 + images.length) % images.length; render(); };
    next.onclick = (e) => { e.stopPropagation(); idx = (idx + 1) % images.length; render(); };
    close.onclick = () => overlay.remove();
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    document.addEventListener("keydown", function onKey(ev) {
      if (ev.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", onKey); }
      if (ev.key === "ArrowLeft") { idx = (idx - 1 + images.length) % images.length; render(); }
      if (ev.key === "ArrowRight") { idx = (idx + 1) % images.length; render(); }
    });

    $("#modal-root").appendChild(overlay);
    render();
  }

  /* ------------------------------------------------------- formulario cámara */
  function cameraForm(cam, detected) {
    cam = cam || {};
    detected = detected || {};
    const isNew = !cam.id;
    const motion = cam.motion || {};
    const rec = cam.record || {};
    const rtsp = cam.rtsp_url || detected.rtsp_url || "";
    const host = cam.host || detected.ip || "";
    const info = detected.info && (detected.info.manufacturer || detected.info.model)
      ? [detected.info.manufacturer, detected.info.model].filter(Boolean).join(" ")
      : "";

    const body = `
      <div class="field">
        <label>Nombre</label>
        <input id="f-name" value="${esc(cam.name || detected.name || "")}" placeholder="Entrada, Salón, Garaje…">
      </div>

      <div class="grid-3">
        <div class="field">
          <label>IP o host</label>
          <input id="f-host" value="${esc(host)}" placeholder="192.168.1.50" autocomplete="off">
        </div>
        <div class="field">
          <label>Puerto ONVIF</label>
          <input id="f-onvif-port" type="number" value="${esc(cam.onvif_port || 80)}">
        </div>
        <div class="field">
          <label>Puerto RTSP</label>
          <input id="f-rtsp-port" type="number" value="${esc(cam.rtsp_port || 554)}">
        </div>
      </div>

      <div class="grid-2">
        <div class="field">
          <label>Usuario</label>
          <input id="f-user" value="${esc(cam.username || "admin")}" autocomplete="username">
        </div>
        <div class="field">
          <label>Contraseña ${cam.has_password ? '<span style="color:#86efac">(guardada)</span>' : ""}</label>
          <input id="f-pass" type="password" value="" placeholder="${cam.has_password ? "dejar vacío para mantener" : "contraseña de la cámara"}" autocomplete="current-password">
        </div>
      </div>

      <div class="field">
        <label>URL RTSP</label>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          <select id="f-rtsp-preset" class="input" style="flex:1;height:32px">
            <option value="">Personalizada</option>
            <option value="udp0">UDP av0_0 (1280x720)</option>
            <option value="udp1">UDP av0_1</option>
            <option value="tcp0">TCP av0_0 (1280x720)</option>
            <option value="tcp1">TCP av0_1 (640x360)</option>
          </select>
          <button class="btn ghost tiny" id="f-apply-preset" type="button">Aplicar</button>
        </div>
        <textarea id="f-rtsp" placeholder="rtsp://usuario:clave@192.168.1.50:554/onvif1">${esc(rtsp)}</textarea>
        <span class="hint" id="f-info">${esc(info)}</span>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px">
        <button class="btn ghost tiny" id="f-detect">🔎 Detectar con ONVIF</button>
        <button class="btn ghost tiny" id="f-probe">▶ Probar stream</button>
      </div>

      <div class="section-title">Movimiento y grabación</div>
      <div class="switch-row">
        <div class="txt"><b>Detección de movimiento</b><span>Genera eventos, capturas y avisos</span></div>
        <label class="switch"><input type="checkbox" id="f-motion" ${motion.enabled ? "checked" : ""}><i></i></label>
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Sensibilidad (% de píxeles)</label>
          <input id="f-threshold" type="number" step="0.1" min="0.1" max="50" value="${esc(motion.threshold || 1.5)}">
          <span class="hint">Menor = más sensible. Prueba 1–3 %.</span>
        </div>
        <div class="field">
          <label>Espera entre avisos (s)</label>
          <input id="f-cooldown" type="number" min="5" value="${esc(motion.cooldown || 30)}">
        </div>
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Grabación</label>
          <select id="f-recmode">
            <option value="off" ${rec.mode === "off" || !rec.mode ? "selected" : ""}>Desactivada</option>
            <option value="motion" ${rec.mode === "motion" ? "selected" : ""}>Solo al detectar movimiento</option>
            <option value="continuous" ${rec.mode === "continuous" ? "selected" : ""}>Continua</option>
          </select>
        </div>
        <div class="field">
          <label>Duración del clip (s)</label>
          <input id="f-clip" type="number" min="3" value="${esc(rec.clip_seconds || 15)}">
        </div>
      </div>
      <div class="switch-row">
        <div class="txt"><b>Audio</b><span>Escuchar el sonido de la cámara (si lo emite)</span></div>
        <label class="switch"><input type="checkbox" id="f-audio" ${cam.audio !== false ? "checked" : ""}><i></i></label>
      </div>
    `;

    const footer = `<button class="btn ghost left" id="f-delete" ${isNew ? "hidden" : ""}>Eliminar</button>
      <button class="btn ghost" id="f-cancel">Cancelar</button>
      <button class="btn primary" id="f-save">${isNew ? "Añadir cámara" : "Guardar cambios"}</button>`;

    const overlay = openModal({
      title: isNew ? "Añadir cámara" : "Editar " + (cam.name || cam.id),
      body, footer,
    });

    const val = (id) => $(id, overlay).value.trim();

    $("#f-cancel", overlay).onclick = closeModal;

    $("#f-apply-preset", overlay).onclick = () => {
      const sel = $("#f-rtsp-preset", overlay);
      const u = val("#f-user") || "admin";
      const p = $("#f-pass", overlay).value || "";
      const h = val("#f-host") || "";
      if (!h) return toast("Indica la IP de la cámara", "err");
      if (sel.value === "udp0") $("#f-rtsp", overlay).value = `rtsp://${encodeURIComponent(u)}:${encodeURIComponent(p)}@${h}:10554/udp/av0_0`;
      else if (sel.value === "udp1") $("#f-rtsp", overlay).value = `rtsp://${encodeURIComponent(u)}:${encodeURIComponent(p)}@${h}:10554/udp/av0_1`;
      else if (sel.value === "tcp0") $("#f-rtsp", overlay).value = `rtsp://${encodeURIComponent(u)}:${encodeURIComponent(p)}@${h}:10554/tcp/av0_0`;
      else if (sel.value === "tcp1") $("#f-rtsp", overlay).value = `rtsp://${encodeURIComponent(u)}:${encodeURIComponent(p)}@${h}:10554/tcp/av0_1`;
    };

    $("#f-detect", overlay).onclick = async (ev) => {
      const btn = ev.currentTarget;
      if (!val("#f-host")) return toast("Escribe primero la IP de la cámara", "err");
      btn.disabled = true;
      btn.textContent = "Detectando…";
      try {
        const r = await api("/api/detect", {
          method: "POST",
          body: {
            host: val("#f-host"),
            username: val("#f-user"),
            password: $("#f-pass", overlay).value,
            onvif_port: Number(val("#f-onvif-port")) || 80,
            camera_id: cam.id || "",
          },
        });
        if (r.rtsp_url) {
          $("#f-rtsp", overlay).value = r.rtsp_url;
          toast("URL RTSP detectada", "ok");
        }
        if (r.onvif) {
          const name = [r.info.manufacturer, r.info.model].filter(Boolean).join(" ");
          $("#f-info", overlay).textContent = "ONVIF ✓ " + (name || "cámara compatible") + (r.ptz ? " · PTZ ✓" : "");
          $("#f-info", overlay).style.color = "#86efac";
          if (!$("#f-name", overlay).value) $("#f-name", overlay).value = r.info.model || "Cámara";
        } else {
          $("#f-info", overlay).textContent = "ONVIF: " + (r.error || "sin respuesta") + (r.rtsp_url ? " · RTSP encontrado" : "");
          $("#f-info", overlay).style.color = "#fbbf24";
        }
      } catch (err) {
        toast("Detección: " + err.message, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "🔎 Detectar con ONVIF";
      }
    };

    $("#f-probe", overlay).onclick = async (ev) => {
      const btn = ev.currentTarget;
      let url = $("#f-rtsp", overlay).value.trim();
      if (!url) return toast("Falta la URL RTSP", "err");
      btn.disabled = true;
      btn.textContent = "Probando…";
      try {
        const r = await api("/api/probe", { method: "POST", body: { url } });
        if (r.ok) {
          $("#f-info", overlay).textContent = `Stream OK · ${r.codec} ${r.width}×${r.height}${r.audio ? " · audio " + r.audio_codec : ""}`;
          $("#f-info", overlay).style.color = "#86efac";
          toast("El stream responde correctamente", "ok");
        } else {
          $("#f-info", overlay).textContent = "Error: " + r.error;
          $("#f-info", overlay).style.color = "#f87171";
        }
      } catch (err) {
        toast("Prueba: " + err.message, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "▶ Probar stream";
      }
    };

    $("#f-save", overlay).onclick = async (ev) => {
      const btn = ev.currentTarget;
      const payload = {
        id: cam.id || undefined,
        name: val("#f-name") || "Cámara",
        host: val("#f-host"),
        username: val("#f-user"),
        onvif_port: Number(val("#f-onvif-port")) || 80,
        rtsp_port: Number(val("#f-rtsp-port")) || 554,
        rtsp_url: $("#f-rtsp", overlay).value.trim(),
        enabled: true,
        audio: $("#f-audio", overlay).checked,
        motion: {
          enabled: $("#f-motion", overlay).checked,
          threshold: Number(val("#f-threshold")) || 1.5,
          cooldown: Number(val("#f-cooldown")) || 30,
          min_area: motion.min_area || 0.5,
        },
        record: {
          mode: $("#f-recmode", overlay).value,
          clip_seconds: Number(val("#f-clip")) || 15,
          segment_seconds: rec.segment_seconds || 300,
        },
      };
      const pass = $("#f-pass", overlay).value;
      if (pass) payload.password = pass;
      else if (!cam.has_password) payload.password = "";

      if (!payload.host && !payload.rtsp_url) return toast("Indica la IP o la URL RTSP", "err");

      btn.disabled = true;
      try {
        await api("/api/cameras", { method: "POST", body: payload });
        closeModal();
        toast("Cámara guardada", "ok");
        await loadCameras();
        await refreshStatus();
      } catch (err) {
        toast("Guardar: " + err.message, "err");
      } finally {
        btn.disabled = false;
      }
    };

    $("#f-delete", overlay).onclick = async () => {
      if (!confirm("¿Eliminar la cámara «" + (cam.name || cam.id) + "»? No se borran las grabaciones.")) return;
      try {
        await api("/api/cameras/" + encodeURIComponent(cam.id), { method: "DELETE" });
        closeModal();
        toast("Cámara eliminada", "ok");
        await loadCameras();
      } catch (err) { toast(err.message, "err"); }
    };

    if (detected.ip && !cam.id) {
      // rellenado automático desde el escaneo
    }
  }

  /* ---------------------------------------------------------------- escaneo */
  function scanModal() {
    const body = `
      <p class="hint" style="margin-top:0">Se buscarán cámaras ONVIF y dispositivos con el puerto RTSP abierto en tu red local. Tarda entre 10 y 30 segundos.</p>
      <div class="grid-2">
        <div class="field"><label>Usuario ONVIF</label><input id="s-user" value="admin"></div>
        <div class="field"><label>Contraseña</label><input id="s-pass" type="password" placeholder="la de la cámara"></div>
      </div>
      <div class="scan-status" id="s-status" hidden><span class="spinner"></span><span class="txt">Escaneando la red…</span></div>
      <div class="devices" id="s-devices"></div>
    `;
    const footer = `<button class="btn ghost left" id="s-close">Cerrar</button>
      <button class="btn primary" id="s-start">Buscar cámaras</button>`;
    const overlay = openModal({ title: "Buscar cámaras en la red", body, footer });

    $("#s-close", overlay).onclick = closeModal;
    $("#s-start", overlay).onclick = async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      $("#s-status", overlay).hidden = false;
      $("#s-devices", overlay).innerHTML = "";
      try {
        const r = await api("/api/scan", {
          method: "POST",
          body: { username: $("#s-user", overlay).value, password: $("#s-pass", overlay).value, timeout: 8 },
        });
        renderDevices(overlay, r.devices || []);
      } catch (err) {
        toast("Escaneo: " + err.message, "err");
      } finally {
        btn.disabled = false;
        $("#s-status", overlay).hidden = true;
      }
    };
  }

  function renderDevices(overlay, devices) {
    const box = $("#s-devices", overlay);
    if (!devices.length) {
      box.innerHTML = '<div class="events-empty">No se encontraron dispositivos.<br>Prueba a añadir la cámara a mano con su IP.</div>';
      return;
    }
    box.innerHTML = devices.map((d, i) => {
      const name = d.info && (d.info.manufacturer || d.info.model)
        ? [d.info.manufacturer, d.info.model].filter(Boolean).join(" ")
        : "";
      return `<div class="device" data-i="${i}">
        <div class="grow">
          <div class="ip">${esc(d.ip)} ${d.onvif ? '<span class="tagok">ONVIF</span>' : ""}</div>
          <div class="sub">${esc(name || "Dispositivo de red")} · puertos ${esc((d.ports || []).join(", ") || "—")}${d.rtsp ? " · RTSP ✓" : ""}</div>
        </div>
        <button class="btn ghost tiny use">Añadir</button>
      </div>`;
    }).join("");
    $$(".device", box).forEach((row) => {
      $(".use", row).onclick = () => {
        const d = devices[Number(row.dataset.i)];
        closeModal();
        cameraForm(
          { name: (d.info && d.info.model) || "", host: d.ip, username: $("#s-user", overlay).value, onvif_port: 80 },
          d
        );
      };
    });
  }

  /* ------------------------------------------------------------ grabaciones */
  function playMedia(url, title) {
    const overlay = openModal({
      title: title || "Reproducción",
      wide: true,
      body: `<div class="player-wrap"><video id="p-video" controls autoplay playsinline src="${esc(url)}"></video></div>`,
      footer: `<button class="btn ghost left" id="p-download">Descargar</button>
               <button class="btn primary" id="p-close">Cerrar</button>`,
    });
    $("#p-close", overlay).onclick = closeModal;
    $("#p-download", overlay).onclick = () => {
      const a = document.createElement("a");
      a.href = url;
      a.download = (url.split("/").pop() || "video.mp4");
      a.click();
    };
  }

  async function recordingsModal() {
    const overlay = openModal({
      title: "Grabaciones",
      wide: true,
      body: '<div class="scan-status"><span class="spinner"></span><span>Cargando…</span></div><div class="reclist" id="rec-list"></div>',
      footer: '<button class="btn ghost left" id="rec-clean">Limpiar antiguas</button><button class="btn ghost" id="rec-close">Cerrar</button>',
    });
    $("#rec-close", overlay).onclick = closeModal;
    $("#rec-clean", overlay).onclick = async () => {
      if (!confirm("Se borrarán las grabaciones y capturas más antiguas según la retención configurada. ¿Continuar?")) return;
      try {
        const r = await api("/api/cleanup", { method: "POST" });
        toast(`Limpieza: ${r.removed} ficheros (${fmtBytes(r.freed)})`, "ok");
        loadRecordings(overlay);
      } catch (err) { toast(err.message, "err"); }
    };
    loadRecordings(overlay);
  }

  async function loadRecordings(overlay) {
    const box = $("#rec-list", overlay);
    try {
      const r = await api("/api/recordings");
      const items = r.recordings || [];
      if (!items.length) {
        box.innerHTML = '<div class="events-empty">Todavía no hay grabaciones.<br>Actívalas con el botón ⏺ de cada cámara o desde sus ajustes.</div>';
        return;
      }
      box.innerHTML = items.map((it) => `
        <div class="recitem" data-url="${esc(it.url)}" data-path="${esc(it.path)}">
          <div><div class="nm">${esc(it.name)}</div><div class="sub">${esc(fmtTime(it.mtime))} · ${esc(it.size_human)}</div></div>
          <button class="btn ghost tiny play">▶</button>
          <button class="btn ghost tiny del" title="Borrar">🗑</button>
        </div>`).join("");
      $$(".recitem", box).forEach((row) => {
        $(".play", row).onclick = () => playMedia(row.dataset.url, "Grabación");
        $(".del", row).onclick = async () => {
          if (!confirm("¿Borrar esta grabación?")) return;
          try {
            await api("/api/recordings?path=" + encodeURIComponent(row.dataset.path), { method: "DELETE" });
            row.remove();
            toast("Grabación borrada", "ok");
          } catch (err) { toast(err.message, "err"); }
        };
      });
    } catch (err) {
      box.innerHTML = '<div class="events-empty">Error: ' + esc(err.message) + "</div>";
    }
  }

  /* --------------------------------------------------------------- ajustes */
  function settingsModal(tab) {
    tab = tab || "general";
    const body = `
      <div class="tabs" id="set-tabs">
        <button class="tab ${tab === "general" ? "active" : ""}" data-tab="general">General</button>
        <button class="tab ${tab === "cloud" ? "active" : ""}" data-tab="cloud">Nube</button>
        <button class="tab ${tab === "alerts" ? "active" : ""}" data-tab="alerts">Avisos</button>
      </div>
      <div style="padding-top:16px" id="set-body"></div>
    `;
    const footer = `<button class="btn ghost left" id="set-clean">Limpiar almacenamiento</button>
      <button class="btn ghost" id="set-close">Cancelar</button>
      <button class="btn primary" id="set-save">Guardar</button>`;
    const overlay = openModal({ title: "Ajustes", body, footer, wide: true });

    $("#set-close", overlay).onclick = closeModal;
    $("#set-clean", overlay).onclick = async () => {
      try {
        const r = await api("/api/cleanup", { method: "POST" });
        toast(`Borrados ${r.removed} ficheros (${fmtBytes(r.freed)})`, "ok");
      } catch (err) { toast(err.message, "err"); }
    };

    $$(".tab", overlay).forEach((btn) => {
      btn.onclick = () => {
        $$(".tab", overlay).forEach((b) => b.classList.toggle("active", b === btn));
        renderSettingsTab(overlay, btn.dataset.tab);
      };
    });

    $("#set-save", overlay).onclick = async (ev) => {
      const btn = ev.currentTarget;
      const patch = {};
      $$("[data-key]", overlay).forEach((inp) => {
        let v;
        if (inp.type === "checkbox") v = inp.checked;
        else if (inp.type === "number") v = inp.value === "" ? 0 : Number(inp.value);
        else v = inp.value;
        setDeep(patch, inp.dataset.key, v);
      });
      btn.disabled = true;
      try {
        await api("/api/settings", { method: "POST", body: patch });
        toast("Ajustes guardados", "ok");
        closeModal();
        await refreshStatus();
      } catch (err) {
        toast("Guardar: " + err.message, "err");
      } finally { btn.disabled = false; }
    };

    renderSettingsTab(overlay, tab);

    async function renderSettingsTab(root, name) {
      const box = $("#set-body", root);
      box.innerHTML = '<div class="scan-status"><span class="spinner"></span><span>Cargando…</span></div>';
      let s;
      try {
        const res = await api("/api/settings");
        s = res.settings;
      } catch (err) {
        box.innerHTML = '<div class="events-empty">Error: ' + esc(err.message) + "</div>";
        return;
      }
      if (name === "general") box.innerHTML = generalTab(s);
      if (name === "cloud") box.innerHTML = cloudTab(s);
      if (name === "alerts") box.innerHTML = alertsTab(s);
      wireTestButtons(root, box, name);
    }
  }

  function generalTab(s) {
    const st = s.storage || {};
    const g = s.go2rtc || {};
    const srv = s.server || {};
    return `
      <div class="section-title">Acceso al panel</div>
      <div class="field">
        <label>Contraseña del panel</label>
        <input type="text" data-key="server.password" value="${esc(srv.password || "")}" placeholder="vacío = sin contraseña">
        <span class="hint">Se pide al entrar desde otro dispositivo de la red. Vacío = acceso libre en tu LAN.</span>
      </div>
      <div class="field">
        <label>Puerto del panel</label>
        <input type="number" data-key="server.port" value="${esc(srv.port || 8080)}">
        <span class="hint">El cambio se aplica al reiniciar el programa.</span>
      </div>

      <div class="section-title">Almacenamiento</div>
      <div class="grid-2">
        <div class="field"><label>Conservar grabaciones (días)</label>
          <input type="number" min="1" data-key="storage.keep_days" value="${esc(st.keep_days || 14)}"></div>
        <div class="field"><label>Límite de disco (GB)</label>
          <input type="number" min="1" data-key="storage.max_gb" value="${esc(st.max_gb || 100)}"></div>
      </div>

      <div class="section-title">Vídeo (go2rtc)</div>
      <div class="field">
        <label>IP que anunciar a los navegadores</label>
        <input type="text" data-key="go2rtc.candidate_ip" value="${esc(g.candidate_ip || "")}" placeholder="se detecta automáticamente">
        <span class="hint">Solo cámbialo si ves la imagen en tu equipo pero no desde otros dispositivos.</span>
      </div>
      <div class="grid-2">
        <div class="field"><label>Puerto API interno</label>
          <input type="number" data-key="go2rtc.api_port" value="${esc(g.api_port || 1984)}"></div>
        <div class="field"><label>Puerto WebRTC (UDP/TCP)</label>
          <input type="number" data-key="go2rtc.webrtc_port" value="${esc(g.webrtc_port || 8555)}"></div>
      </div>
    `;
  }

  function cloudTab(s) {
    const c = s.cloud || {};
    return `
      <div class="switch-row">
        <div class="txt"><b>Subir grabaciones a mi nube</b><span>Se suben automáticamente los clips y las capturas</span></div>
        <label class="switch"><input type="checkbox" data-key="cloud.enabled" ${c.enabled ? "checked" : ""}><i></i></label>
      </div>
      <div class="field" style="margin-top:14px">
        <label>Método</label>
        <select data-key="cloud.type">
          <option value="webdav" ${c.type === "webdav" ? "selected" : ""}>WebDAV (Nextcloud, ownCloud, NAS…)</option>
          <option value="rclone" ${c.type === "rclone" ? "selected" : ""}>rclone (Drive, S3, Dropbox…) con remoto configurado</option>
        </select>
      </div>
      <div class="field">
        <label>Carpeta remota</label>
        <input type="text" data-key="cloud.folder" value="${esc(c.folder || "vstarcam")}" placeholder="vstarcam">
      </div>
      <div class="section-title">WebDAV</div>
      <div class="field"><label>URL del servidor</label>
        <input type="text" data-key="cloud.url" value="${esc(c.url || "")}" placeholder="https://nube.ejemplo.com/remote.php/dav/files/usuario"></div>
      <div class="grid-2">
        <div class="field"><label>Usuario</label><input type="text" data-key="cloud.username" value="${esc(c.username || "")}"></div>
        <div class="field"><label>Contraseña</label><input type="password" data-key="cloud.password" value="${esc(c.password || "")}"></div>
      </div>
      <div class="section-title">rclone</div>
      <div class="field">
        <label>Remoto y ruta</label>
        <input type="text" data-key="cloud.rclone_remote" value="${esc(c.rclone_remote || "")}" placeholder="minkube:Nextcloud/vstarcam">
        <span class="hint">Configura el remoto una vez con <code>rclone config</code>. Aquí solo el destino.</span>
      </div>
      <div class="switch-row">
        <div class="txt"><b>Borrar en local tras subir</b><span>Ahorra disco; el vídeo queda solo en la nube</span></div>
        <label class="switch"><input type="checkbox" data-key="cloud.delete_after_upload" ${c.delete_after_upload ? "checked" : ""}><i></i></label>
      </div>
      <div style="margin-top:14px"><button class="btn ghost tiny" id="test-cloud">Probar conexión</button></div>
    `;
  }

  function alertsTab(s) {
    const a = s.alerts || {};
    const ntfy = a.ntfy || {};
    const tg = a.telegram || {};
    return `
      <div class="switch-row">
        <div class="txt"><b>ntfy</b><span>Notificación instantánea al móvil, gratis y sin cuenta</span></div>
        <label class="switch"><input type="checkbox" data-key="alerts.ntfy.enabled" ${ntfy.enabled ? "checked" : ""}><i></i></label>
      </div>
      <div class="grid-2">
        <div class="field"><label>Servidor ntfy</label><input type="text" data-key="alerts.ntfy.url" value="${esc(ntfy.url || "https://ntfy.sh")}"></div>
        <div class="field"><label>Tema (topic)</label><input type="text" data-key="alerts.ntfy.topic" value="${esc(ntfy.topic || "")}" placeholder="mi-casa-8f3a"></div>
      </div>
      <p class="hint">Instala la app ntfy y suscríbete a ese tema. Usa un nombre difícil de adivinar.</p>

      <div class="section-title">Telegram</div>
      <div class="switch-row">
        <div class="txt"><b>Bot de Telegram</b><span>Envía la foto del evento a tu chat</span></div>
        <label class="switch"><input type="checkbox" data-key="alerts.telegram.enabled" ${tg.enabled ? "checked" : ""}><i></i></label>
      </div>
      <div class="field"><label>Token del bot</label><input type="text" data-key="alerts.telegram.token" value="${esc(tg.token || "")}" placeholder="123456:ABC-DEF…"></div>
      <div class="field"><label>Chat ID</label><input type="text" data-key="alerts.telegram.chat_id" value="${esc(tg.chat_id || "")}"></div>

      <div class="section-title">Webhook</div>
      <div class="field">
        <label>URL</label>
        <input type="text" data-key="alerts.webhook" value="${esc(a.webhook || "")}" placeholder="https://…">
        <span class="hint">Recibirá un JSON con título, mensaje y ruta de la imagen.</span>
      </div>
      <div style="margin-top:14px"><button class="btn ghost tiny" id="test-alerts">Enviar prueba</button></div>
    `;
  }

  function wireTestButtons(root, box, tab) {
    const cloudBtn = $("#test-cloud", box);
    if (cloudBtn) {
      cloudBtn.onclick = async () => {
        cloudBtn.disabled = true;
        try {
          // Guarda primero lo que haya en el formulario
          const patch = {};
          $$("[data-key]", root).forEach((inp) => {
            let v;
            if (inp.type === "checkbox") v = inp.checked;
            else if (inp.type === "number") v = Number(inp.value || 0);
            else v = inp.value;
            setDeep(patch, inp.dataset.key, v);
          });
          await api("/api/settings", { method: "POST", body: patch });
          const r = await api("/api/cloud/test", { method: "POST" });
          toast(r.ok ? "Nube: " + (r.message || "conexión correcta") : "Nube: " + (r.error || "error"), r.ok ? "ok" : "err");
        } catch (err) { toast(err.message, "err"); }
        finally { cloudBtn.disabled = false; }
      };
    }
    const alertsBtn = $("#test-alerts", box);
    if (alertsBtn) {
      alertsBtn.onclick = async () => {
        alertsBtn.disabled = true;
        try {
          await api("/api/settings", { method: "POST", body: { alerts: s_alerts(root) } });
          const r = await api("/api/alerts/test", { method: "POST" });
          const bad = (r.results || []).filter((x) => !x.ok);
          toast(bad.length ? "Aviso: " + bad.map((b) => b.channel + " " + b.error).join("; ") : "Aviso de prueba enviado", bad.length ? "err" : "ok");
        } catch (err) { toast(err.message, "err"); }
        finally { alertsBtn.disabled = false; }
      };
    }
  }

  function s_alerts(root) {
    const out = {};
    $$("[data-key^='alerts.']", root).forEach((inp) => {
      let v;
      if (inp.type === "checkbox") v = inp.checked;
      else v = inp.value;
      setDeep(out, inp.dataset.key.replace(/^alerts\./, ""), v);
    });
    return out;
  }

  /* ------------------------------------------------------------------ boot */
  async function boot() {
    await refreshStatus();
    try {
      await loadCameras();
    } catch (err) {
      toast("No se pudieron cargar las cámaras: " + err.message, "err");
    }
    await refreshEvents();

    if (state.status && state.status.go2rtc && !state.status.go2rtc.installed) {
      toast("go2rtc no está instalado: verás imágenes de baja frecuencia. Ejecuta ./setup.sh", "err");
    }

    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      await refreshStatus();
      await refreshEvents();
    }, 5000);
  }

  /* ------------------------------------------------------------- eventos UI */
  $("#btn-add").onclick = () => cameraForm({}, {});
  $("#empty-add").onclick = () => cameraForm({}, {});
  $("#btn-scan").onclick = scanModal;
  $("#empty-scan").onclick = scanModal;
  $("#btn-settings").onclick = () => settingsModal();
  $("#btn-recordings").onclick = recordingsModal;
  $("#btn-events").onclick = () => (state.eventsOpen ? closeEvents() : openEvents());
  $("#events-close").onclick = closeEvents;
  $("#drawer-backdrop").onclick = closeEvents;
  $("#events-filter").onchange = refreshEvents;
  $("#events-kind").onchange = refreshEvents;

  document.addEventListener("keydown", (ev) => {
    if (ev.target.matches("input, textarea, select")) return;
    const t = state.focusId ? state.tiles.get(state.focusId) : null;
    if (!t) return;
    const map = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
                  "+": "zoom_in", "-": "zoom_out" };
    const action = map[ev.key];
    if (!action) return;
    if (action.startsWith("zoom_") && t.ptz.classList.contains("simple")) return;
    ev.preventDefault();
    state.keyHeld = state.keyHeld || {};
    if (state.keyHeld[action]) return; // ignora la repetición del teclado
    state.keyHeld[action] = true;
    const blink = t.el.querySelector('[data-move="' + action + '"]');
    if (blink) blink.classList.add("pressed");
    ptzStart(t, action);
    const up = () => {
      state.keyHeld[action] = false;
      if (blink) blink.classList.remove("pressed");
      ptzStop(t, action);
      document.removeEventListener("keyup", up);
    };
    document.addEventListener("keyup", up);
  });

  window.addEventListener("beforeunload", () => {
    state.tiles.forEach((t) => stopStream(t));
  });

  boot();
})();
