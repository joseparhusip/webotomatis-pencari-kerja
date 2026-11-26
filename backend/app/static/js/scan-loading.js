// Layar loading scan CV: tahap yang tampil diambil dari server (/profile/scan-status), bukan tebakan waktu.
// Form yang memicu scan diberi atribut data-scan="Judul". Dipasang setelah confirm.js: form yang masih
// menunggu konfirmasi (submit dibatalkan) tidak memunculkan layar ini.
(function () {
  // data-scan-steps="score" → tahapan Skor CV; tanpa atribut → tahapan unggah CV di Profil.
  const SETS = {
    profile: {
      steps: [
        ["upload", "Mengunggah & menyiapkan file"],
        ["text", "Mengambil teks dari PDF"],
        ["ocr", "Membaca halaman dengan OCR (PDF berupa gambar)"],
        ["map", "Memetakan data CV: data diri, pengalaman, pendidikan, skill"],
        ["save", "Menyimpan ke profil"],
      ],
      foot: `Biasanya beberapa detik; PDF berupa gambar (OCR) bisa ±30 detik.
        Setelah ini, ringkasan, bukti &amp; kalimat surat disusun AI di latar belakang — statusnya tampil di halaman Profil.`,
    },
    rescore: {
      steps: [
        ["score", "Menilai ulang dari teks CV yang sudah terbaca"],
        ["save", "Menyimpan hasil"],
      ],
      foot: "Tidak perlu unggah ulang. Kesimpulan AI ikut disusun ulang di latar belakang.",
    },
    job: {
      steps: [
        ["score", "Membaca syarat lowongan & mencocokkan dengan CV"],
        ["save", "Menyimpan hasil"],
      ],
      foot: "Skill, pengalaman, pendidikan, jurusan, dan bahasa dicocokkan oleh kode. Kesimpulan AI menyusul di latar belakang.",
    },
    score: {
      steps: [
        ["upload", "Mengunggah & memeriksa file (PDF / Word, maks. 5 MB)"],
        ["text", "Mengambil teks dari file"],
        ["ocr", "Membaca halaman dengan OCR (CV berupa gambar)"],
        ["map", "Memetakan isi CV: kontak, pengalaman, pendidikan, skill"],
        ["score", "Menilai 23 kriteria & membandingkan dengan lowongan di database"],
        ["save", "Menyimpan hasil"],
      ],
      foot: `Skor dihitung dengan aturan tetap (bukan AI), jadi CV yang sama selalu mendapat skor yang sama.
        Setelah ini, kesimpulan &amp; prioritas perbaikan disusun AI di latar belakang.`,
    },
  };

  function token() {
    const raw = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return raw.replace(/[^a-z0-9-]/gi, "").slice(0, 60);
  }

  function show(tok, title, set) {
    const STEPS = set.steps;
    const ORDER = STEPS.map((s) => s[0]);
    const el = document.createElement("div");
    el.className = "scan-overlay";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = `
      <div class="scan-card">
        <header class="scan-head">
          <span class="scan-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M14 3v5h5M8.5 13h7M8.5 16.5h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
            <span class="scan-beam"></span>
          </span>
          <div class="scan-title">
            <h2></h2>
            <span class="scan-sub"><b class="scan-time">0</b> dtk · jangan tutup halaman ini</span>
          </div>
          <span class="scan-pct">0%</span>
        </header>
        <div class="scan-progress" aria-hidden="true"><span></span></div>
        <p class="scan-stepno">Tahap <b>1</b> dari <b>${STEPS.length}</b></p>
        <ol class="scan-steps">${STEPS.map(([k, label]) =>
          `<li data-step="${k}"${k === "ocr" ? " hidden" : ""}><span class="mark" aria-hidden="true"></span>` +
          `<span class="txt"><span class="lbl">${label}</span><span class="now">sedang berjalan…</span></span></li>`).join("")}</ol>
        <p class="scan-foot">${set.foot}</p>
      </div>`;
    el.querySelector(".scan-title h2").textContent = title;
    document.body.appendChild(el);
    document.documentElement.classList.add("has-modal");

    const started = Date.now();
    let current = ORDER[0];
    const paint = () => {
      const idx = ORDER.indexOf(current);
      el.querySelectorAll("[data-step]").forEach((li) => {
        const i = ORDER.indexOf(li.dataset.step);
        li.classList.toggle("is-done", i < idx);
        li.classList.toggle("is-active", i === idx);
        li.querySelector(".mark").textContent = i < idx ? "✓" : "";
        if (li.dataset.step === "ocr" && current === "ocr") li.hidden = false;
      });
      const visible = ORDER.filter((k) => k !== "ocr" || current === "ocr");
      const pos = Math.max(0, visible.indexOf(current));
      const pct = Math.round(((pos + 0.5) / visible.length) * 100);
      el.querySelector(".scan-progress > span").style.width = `${pct}%`;
      el.querySelector(".scan-pct").textContent = `${pct}%`;
      const no = el.querySelectorAll(".scan-stepno b");
      no[0].textContent = pos + 1;
      no[1].textContent = visible.length;
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
    window.addEventListener("pageshow", (e) => {
      if (e.persisted) { clearInterval(tick); clearInterval(poll); el.remove(); document.documentElement.classList.remove("has-modal"); }
    }, { once: true });
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
    show(tok, form.dataset.scan || "Memindai CV", SETS[form.dataset.scanSteps] || SETS.profile);
  });
})();
