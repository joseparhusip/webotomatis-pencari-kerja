// Halaman Swipe: geser kartu (mouse & sentuh lewat Pointer Events), tombol ✕ ↺ ♥, dan keyboard ← → Backspace.
(() => {
  const THRESHOLD = 110;   // px: lewat dari ini = keputusan, kurang = kartu kembali ke tengah
  const FLY_MS = 230;
  const START_PX = 8;      // gerakan awal sebelum arah (horizontal/vertikal) ditentukan
  let busy = false;
  let drag = null;

  const stage = () => document.getElementById("swipe-stage");
  const card = () => stage()?.querySelector(".swipe-card");
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const clamp01 = (v) => Math.max(0, Math.min(1, v));

  function syncButtons() {
    const st = stage();
    if (!st) return;
    const hasCard = !!card();
    document.querySelectorAll("[data-swipe]").forEach((b) => {
      b.disabled = busy || (b.dataset.swipe === "undo" ? !st.dataset.last : !hasCard);
    });
  }

  function setStamps(c, dx) {
    c.style.setProperty("--like", clamp01(dx / THRESHOLD).toFixed(2));
    c.style.setProperty("--nope", clamp01(-dx / THRESHOLD).toFixed(2));
  }

  function move(c, dx) {
    c.style.transform = `translateX(${dx}px) rotate(${dx / 20}deg)`;
    setStamps(c, dx);
  }

  function reset(c) {
    c.classList.remove("is-dragging", "is-flying");
    c.style.transition = reduced() ? "none" : "transform 200ms ease, opacity 200ms ease";
    c.style.transform = "";
    c.style.opacity = "";
    setStamps(c, 0);
  }

  function send(action, id, c) {
    busy = true;
    syncButtons();
    const done = () => {
      busy = false;
      if (c && c.isConnected) reset(c);  // permintaan gagal → kartu lama kembali
      syncButtons();
    };
    htmx.ajax("POST", `/swipe/${id}/${action}`, { target: "#swipe-stage", swap: "outerHTML" }).then(done, done);
  }

  function decide(action) {
    const st = stage();
    if (busy || !st) return;
    if (action === "undo") {
      if (st.dataset.last) send("undo", st.dataset.last, null);
      return;
    }
    const c = card();
    if (!c) return;
    // Buka tab sumber LANGSUNG di dalam gestur/klik/tombol, sebelum request — supaya tidak diblokir popup blocker.
    if (action === "like" && c.dataset.url) window.open(c.dataset.url, "_blank", "noopener");
    if (reduced()) { send(action, c.dataset.job, c); return; }
    busy = true;
    syncButtons();
    const dir = action === "like" ? 1 : -1;
    c.classList.add("is-flying");
    c.style.transition = `transform ${FLY_MS}ms ease-in, opacity ${FLY_MS}ms ease-in`;
    c.style.transform = `translateX(${dir * Math.max(window.innerWidth, 700)}px) rotate(${dir * 24}deg)`;
    c.style.opacity = "0";
    setStamps(c, dir * THRESHOLD);
    setTimeout(() => send(action, c.dataset.job, c), FLY_MS);
  }

  // ---------- geser ----------
  document.addEventListener("pointerdown", (e) => {
    const c = e.target instanceof Element ? e.target.closest(".swipe-card") : null;
    if (!c || busy || e.button !== 0) return;
    if (e.target.closest("a, button, input, textarea, select, .swipe-detail")) return;
    drag = { c, id: e.pointerId, x: e.clientX, y: e.clientY, axis: null };
  });

  document.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.axis) {
      if (Math.abs(dx) < START_PX && Math.abs(dy) < START_PX) return;
      drag.axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (drag.axis === "x") {
        try { drag.c.setPointerCapture(e.pointerId); } catch { /* pointer sudah lepas */ }
        drag.c.style.transition = "none";
        drag.c.classList.add("is-dragging");
      }
    }
    if (drag.axis !== "x") return;  // dominan vertikal = scroll biasa
    e.preventDefault();
    move(drag.c, dx);
  });

  function endDrag(e, cancelled) {
    if (!drag || e.pointerId !== drag.id) return;
    const { c, x, y, axis } = drag;
    drag = null;
    c.classList.remove("is-dragging");
    // Dihitung dari posisi saat dilepas: seret cepat tanpa pointermove pun tetap terbaca.
    const dx = e.clientX - x, dy = e.clientY - y;
    if (!cancelled && axis !== "y" && Math.abs(dx) >= THRESHOLD && Math.abs(dx) > Math.abs(dy)) {
      decide(dx > 0 ? "like" : "nope");
    } else if (axis === "x") {
      reset(c);
    }
  }
  document.addEventListener("pointerup", (e) => endDrag(e, false));
  document.addEventListener("pointercancel", (e) => endDrag(e, true));

  // ---------- tombol & keyboard ----------
  document.addEventListener("click", (e) => {
    const b = e.target instanceof Element ? e.target.closest("[data-swipe]") : null;
    if (b && !b.disabled) decide(b.dataset.swipe);
  });

  document.addEventListener("keydown", (e) => {
    if (!stage() || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const t = e.target;
    if (t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (document.querySelector("dialog[open]")) return;
    const action = { ArrowRight: "like", ArrowLeft: "nope", Backspace: "undo" }[e.key];
    if (!action) return;
    e.preventDefault();
    decide(action);
  });

  // ---------- penghitung sesi (hanya di halaman ini, hilang saat reload) ----------
  const tally = { like: 0, nope: 0 };
  const history = [];
  document.addEventListener("htmx:afterRequest", (e) => {
    const m = /^\/swipe\/\d+\/(like|nope|undo)$/.exec(e.detail.pathInfo?.requestPath || "");
    if (!m || !e.detail.successful) return;
    if (m[1] === "undo") { const a = history.pop(); if (a) tally[a] = Math.max(0, tally[a] - 1); }
    else { tally[m[1]] += 1; history.push(m[1]); }
    const box = document.getElementById("swipe-tally");
    if (!box) return;
    box.hidden = tally.like + tally.nope === 0;
    box.querySelector('[data-n="like"]').textContent = tally.like;
    box.querySelector('[data-n="nope"]').textContent = tally.nope;
  });

  // ---------- kartu baru ----------
  document.addEventListener("htmx:afterSettle", () => {
    const st = stage();
    if (!st || st.dataset.ready) return;
    st.dataset.ready = "1";
    const c = card();
    if (c && !reduced()) c.classList.add("is-entering");
    window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
    syncButtons();
  });

  const init = () => { const st = stage(); if (st) { st.dataset.ready = "1"; syncButtons(); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
