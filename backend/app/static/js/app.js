// Perilaku kecil di halaman Lowongan: buka/tutup drawer detail & shortcut keyboard.
(function () {
  // Layar kecil: filter tertutup dulu supaya daftar lowongan langsung terlihat.
  if (window.matchMedia("(max-width: 1023px)").matches) {
    document.querySelector(".filters-toggle")?.removeAttribute("open");
  }

  const layout = () => document.querySelector(".layout");

  function closeDrawer() {
    const drawer = document.getElementById("drawer");
    if (!drawer) return;
    drawer.innerHTML = "";
    layout()?.classList.remove("has-drawer");
    document.querySelectorAll(".job-row.is-selected").forEach((r) => r.classList.remove("is-selected"));
  }

  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id === "drawer") {
      layout()?.classList.add("has-drawer");
      const link = e.detail.requestConfig?.elt?.closest?.(".job-row");
      if (link) {
        document.querySelectorAll(".job-row.is-selected").forEach((r) => r.classList.remove("is-selected"));
        link.classList.add("is-selected");
        link.querySelector(".tag-new")?.remove();
      }
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-drawer]")) closeDrawer();

    // Mode portal: salin surat ke clipboard lalu buka halaman lowongan.
    const copyBtn = e.target.closest("[data-copy-open]");
    if (copyBtn) {
      const text = document.getElementById("letter")?.value || "";
      const url = copyBtn.dataset.copyOpen;
      const done = () => { copyBtn.textContent = "Surat tersalin · lowongan dibuka ↗"; window.open(url, "_blank", "noopener"); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, done); else done();
    }

    if (e.target.closest("[data-clear-selection]")) {
      document.querySelectorAll('input[name="job_id"][form="apply-form"]:checked').forEach((c) => (c.checked = false));
      updateSelection();
    }
  });

  // Pilihan lowongan → action bar "Siapkan lamaran".
  function updateSelection() {
    const bar = document.getElementById("apply-form");
    if (!bar) return;
    const n = document.querySelectorAll('input[name="job_id"][form="apply-form"]:checked').length;
    document.getElementById("sel-count").textContent = n;
    bar.hidden = n === 0;
  }
  document.addEventListener("change", (e) => {
    if (e.target.matches('input[name="job_id"][form="apply-form"]')) updateSelection();
  });
  document.addEventListener("htmx:afterSwap", (e) => { if (e.detail.target.id === "job-results") updateSelection(); });

  // j/k = pindah baris, Enter = buka, Esc = tutup, / = cari
  document.addEventListener("keydown", (e) => {
    const typing = e.target.matches("input, select, textarea");
    if (e.key === "Escape") { closeDrawer(); return; }
    if (typing) return;
    if (e.key === "/") { e.preventDefault(); document.getElementById("q")?.focus(); return; }
    const rows = [...document.querySelectorAll(".job-row")];
    if (!rows.length) return;
    const cur = rows.findIndex((r) => r.classList.contains("is-selected"));
    let next = null;
    if (e.key === "j") next = rows[Math.min(cur + 1, rows.length - 1)];
    if (e.key === "k") next = rows[Math.max(cur - 1, 0)];
    if (next) { next.querySelector(".job-link")?.click(); next.scrollIntoView({ block: "nearest" }); }
  });
})();
