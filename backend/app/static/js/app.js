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
      const done = () => {
        copyBtn.textContent = "Surat tersalin · lowongan dibuka ↗";
        window.open(url, "_blank", "noopener");
        askAppliedOnReturn();
      };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, done); else done();
    }

    if (e.target.closest("[data-clear-selection]")) {
      document.querySelectorAll('input[name="job_id"][form="apply-form"]:checked').forEach((c) => (c.checked = false));
      updateSelection();
    }
  });

  // Setelah "Salin surat & buka lowongan": saat kembali ke tab ini, tanya apakah sudah melamar di situsnya,
  // supaya lamaran tercatat & lowongan tidak menumpuk lagi di daftar Lowongan.
  function askAppliedOnReturn() {
    const btn = document.querySelector('[hx-post$="/mark-applied"]');
    if (!btn || !window.uiConfirm || !window.htmx) return;
    const leftAt = Date.now();
    const onBack = () => {
      if (document.visibilityState !== "visible" || Date.now() - leftAt < 3000) return;
      document.removeEventListener("visibilitychange", onBack);
      window.removeEventListener("focus", onBack);
      window.uiConfirm({
        title: "Sudah selesai melamar?",
        message: "Kalau lamaran sudah kamu kirim di situs lowongan, tandai sekarang supaya tercatat di Lamaran dan tidak muncul lagi di daftar Lowongan.",
        ok: "Ya, sudah melamar", cancel: "Belum",
      }).then((yes) => { if (yes) window.htmx.ajax("POST", btn.getAttribute("hx-post"), { source: btn }); });
    };
    document.addEventListener("visibilitychange", onBack);
    window.addEventListener("focus", onBack);
  }

  // Lowongan ditandai sudah dilamar dari panel detail → tutup panel.
  document.body.addEventListener("close-drawer", () => closeDrawer());

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

  // Export Excel: lowongan yang dipilih, atau (tombol di judul daftar) semua hasil filter aktif.
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-export-excel]");
    if (!btn || btn.disabled) return;
    const checked = [...document.querySelectorAll('input[name="job_id"][form="apply-form"]:checked')].map((c) => c.value);
    let params;
    if (btn.dataset.exportExcel === "selected" && checked.length) {
      params = new URLSearchParams({ ids: checked.join(",") });
    } else {
      const form = document.getElementById("filter-form");
      params = form ? new URLSearchParams(new FormData(form)) : new URLSearchParams();
      params.delete("page");
    }
    const label = btn.querySelector("span");
    const original = label.textContent;
    btn.disabled = true;
    btn.classList.add("is-busy");
    label.textContent = "Menyiapkan…";
    try {
      const resp = await fetch(`/jobs/export.xlsx?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const name = /filename="([^"]+)"/.exec(resp.headers.get("Content-Disposition") || "")?.[1] || "lowongan.xlsx";
      const url = URL.createObjectURL(await resp.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      label.textContent = "Terunduh ✓";
    } catch (err) {
      label.textContent = "Gagal, coba lagi";
    } finally {
      btn.classList.remove("is-busy");
      setTimeout(() => { btn.disabled = false; label.textContent = original; }, 2200);
    }
  });

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

// Tombol Batalkan scraping: langsung berubah setelah dikonfirmasi (panel progres menyusul di polling berikutnya).
document.addEventListener("htmx:beforeRequest", (e) => {
  const b = e.detail.elt;
  if (b instanceof HTMLElement && b.id === "scrape-cancel-btn") {
    b.disabled = true;
    const lbl = b.querySelector(".lbl");
    if (lbl) lbl.textContent = "Membatalkan…";
  }
});
