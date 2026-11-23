// Layar loading scan CV: tahap yang tampil diambil dari server (/profile/scan-status), bukan tebakan waktu.
// Form yang memicu scan diberi atribut data-scan="Judul". Dipasang setelah confirm.js: form yang masih
// menunggu konfirmasi (submit dibatalkan) tidak memunculkan layar ini.
(function () {
  const STEPS = [
    ["upload", "Mengunggah & menyiapkan file"],
    ["text", "Mengambil teks dari PDF"],
    ["ocr", "Membaca halaman dengan OCR (PDF berupa gambar)"],
    ["map", "Memetakan data CV: data diri, pengalaman, pendidikan, skill"],
    ["save", "Menyimpan ke profil"],
  ];
  const ORDER = STEPS.map((s) => s[0]);

  function token() {
    const raw = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return raw.replace(/[^a-z0-9-]/gi, "").slice(0, 60);
  }

  function show(tok, title) {
    const el = document.createElement("div");
    el.className = "scan-overlay";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = `
      <div class="scan-card">
        <h2><span class="spinner" aria-hidden="true"></span><span></span></h2>
        <span class="meta">Berjalan <b class="scan-time">0</b> dtk · jangan tutup halaman ini</span>
        <ol class="scan-steps">${STEPS.map(([k, label]) =>
          `<li data-step="${k}"${k === "ocr" ? " hidden" : ""}><span class="mark" aria-hidden="true"></span>${label}</li>`).join("")}</ol>
        <p class="hint scan-foot">Biasanya beberapa detik; PDF berupa gambar (OCR) bisa ±30 detik.
          Setelah ini, ringkasan, bukti &amp; kalimat surat disusun AI di latar belakang — statusnya tampil di halaman Profil.</p>
      </div>`;
    el.querySelector("h2 span:last-child").textContent = title;
    document.body.appendChild(el);

    const started = Date.now();
    let current = "upload";
    const paint = () => {
      const idx = ORDER.indexOf(current);
      el.querySelectorAll("[data-step]").forEach((li) => {
        const i = ORDER.indexOf(li.dataset.step);
        li.classList.toggle("is-done", i < idx);
        li.classList.toggle("is-active", i === idx);
        li.querySelector(".mark").textContent = i < idx ? "✓" : "";
        if (li.dataset.step === "ocr" && current === "ocr") li.hidden = false;
      });
    };
    paint();
    const tick = setInterval(() => { el.querySelector(".scan-time").textContent = Math.round((Date.now() - started) / 1000); }, 500);
    const poll = setInterval(async () => {
      try {
        const r = await fetch(`/profile/scan-status?token=${encodeURIComponent(tok)}`, { cache: "no-store" });
        const data = await r.json();
        if (data.stage && data.stage !== "done" && ORDER.includes(data.stage)) { current = data.stage; paint(); }
      } catch (_) { /* koneksi sesaat putus: tetap tampilkan tahap terakhir */ }
    }, 700);
    // Browser kembali dari cache (tombol Back): bersihkan layar.
    window.addEventListener("pageshow", (e) => { if (e.persisted) { clearInterval(tick); clearInterval(poll); el.remove(); } }, { once: true });
  }

  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-scan") || e.defaultPrevented) return;
    const tok = token();
    let input = form.querySelector('input[name="scan_token"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = "scan_token";
      form.appendChild(input);
    }
    input.value = tok;
    show(tok, form.dataset.scan || "Memindai CV");
  });
})();
