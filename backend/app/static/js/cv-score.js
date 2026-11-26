// Skor CV: area tarik-lepas file (PDF/Word, cek ukuran sebelum diunggah) dan animasi angka skor.
(function () {
  const OK_EXT = /\.(pdf|docx)$/i;

  function setup(form) {
    const zone = form.querySelector(".dropzone");
    const input = zone.querySelector('input[type="file"]');
    const nameEl = zone.querySelector(".dz-file");
    const errEl = zone.querySelector(".dz-error");
    const maxMb = Number(form.dataset.maxMb || 5);

    const check = () => {
      const f = input.files && input.files[0];
      let err = "";
      if (f && !OK_EXT.test(f.name)) err = f.name.toLowerCase().endsWith(".doc")
        ? "Format .doc lama belum didukung — simpan ulang sebagai .docx atau PDF."
        : "File harus PDF atau Word (.docx).";
      else if (f && f.size > maxMb * 1024 * 1024) err = `File ${(f.size / 1048576).toFixed(1)} MB — maksimal ${maxMb} MB.`;
      nameEl.hidden = !f || !!err;
      if (f) nameEl.textContent = `${f.name} · ${f.size < 1048576 ? Math.max(1, Math.round(f.size / 1024)) + " KB" : (f.size / 1048576).toFixed(1) + " MB"}`;
      errEl.hidden = !err;
      errEl.textContent = err;
      zone.classList.toggle("has-file", !!f && !err);
      zone.classList.toggle("has-error", !!err);
      input.setCustomValidity(err);
      return !err;
    };
    input.addEventListener("change", check);
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("is-drag"); }));
    ["dragleave", "dragend", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("is-drag")));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!e.dataTransfer?.files?.length) return;
      input.files = e.dataTransfer.files;
      check();
    });
    // Dipasang lebih awal dari scan-loading.js: file salah → layar loading tidak muncul.
    form.addEventListener("submit", (e) => {
      if (e.submitter && e.submitter.formNoValidate) return;   // "Pakai CV utama": tidak perlu file
      if (!check()) { e.preventDefault(); input.reportValidity(); }
    }, true);
  }

  function countUp(el) {
    const end = Number(el.dataset.countup) || 0;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const start = performance.now();
    const dur = 900;
    const step = (t) => {
      const k = Math.min(1, (t - start) / dur);
      el.textContent = Math.round(end * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    el.textContent = "0";
    requestAnimationFrame(step);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form[data-cv-drop]").forEach(setup);
    document.querySelectorAll("[data-countup]").forEach(countUp);
    // Penghitung karakter teks lowongan
    document.querySelectorAll("textarea[data-count]").forEach((ta) => {
      const out = ta.closest("label")?.querySelector(`[data-count-for="${ta.name}"]`);
      const upd = () => { if (out) out.textContent = ta.value.length.toLocaleString("id-ID"); };
      ta.addEventListener("input", upd);
      upd();
    });
  });
})();
