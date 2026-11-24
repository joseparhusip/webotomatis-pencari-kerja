// Pratinjau PDF di dalam pop-up (bukan tab baru). Link: <a href="…pdf" data-pdf-viewer="Judul">.
// Tanpa JS / klik tengah / Ctrl+klik tetap membuka tab baru seperti biasa.
(function () {
  let dialog = null;

  const ICON = {
    doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M14 3v5h5M10 13h6M10 17h6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    external: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M17 14v5H5V7h5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  };

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "pdf-modal";
    dialog.setAttribute("aria-labelledby", "pdf-modal-title");
    dialog.innerHTML = `
      <div class="pdf-card">
        <header class="pdf-head">
          <span class="pdf-icon">${ICON.doc}</span>
          <div class="pdf-title"><strong id="pdf-modal-title"></strong><span class="meta">Pratinjau CV · PDF</span></div>
          <div class="pdf-actions">
            <a class="btn pdf-btn" data-act="download" title="Unduh PDF">${ICON.download}<span>Unduh</span></a>
            <a class="btn pdf-btn" data-act="open" target="_blank" rel="noopener" title="Buka di tab baru">${ICON.external}<span>Tab baru</span></a>
            <button class="btn-close pdf-close" type="button" data-act="close" aria-label="Tutup pratinjau">${ICON.close}</button>
          </div>
        </header>
        <div class="pdf-body">
          <div class="pdf-loading" role="status"><span class="spinner"></span>Memuat PDF…</div>
          <iframe class="pdf-frame" title="Pratinjau PDF"></iframe>
        </div>
      </div>`;
    document.body.appendChild(dialog);
    const frame = dialog.querySelector(".pdf-frame");
    frame.addEventListener("load", () => dialog.classList.add("is-loaded"));
    dialog.querySelector('[data-act="close"]').addEventListener("click", close);
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(); });   // tombol Esc
    dialog.addEventListener("mousedown", (e) => { dialog._down = e.target === dialog; });
    dialog.addEventListener("mouseup", (e) => { if (e.target === dialog && dialog._down) close(); });
  }

  function open(url, title, opener) {
    if (!dialog) build();
    dialog.classList.remove("is-loaded", "is-closing");
    dialog._opener = opener;
    dialog.querySelector("#pdf-modal-title").textContent = title || "CV";
    dialog.querySelector('[data-act="open"]').href = url;
    const dl = dialog.querySelector('[data-act="download"]');
    dl.href = url + (url.includes("?") ? "&" : "?") + "download=1";
    // #view=FitH: lebar halaman pas dengan jendela di penampil PDF bawaan browser.
    dialog.querySelector(".pdf-frame").src = url + "#view=FitH";
    dialog.showModal();
    document.documentElement.classList.add("has-modal");
    dialog.querySelector('[data-act="close"]').focus();
  }

  function close() {
    if (!dialog?.open || dialog.classList.contains("is-closing")) return;
    dialog.classList.add("is-closing");
    setTimeout(() => {
      dialog.close();
      dialog.classList.remove("is-closing");
      dialog.querySelector(".pdf-frame").src = "about:blank";  // hentikan pemuatan & bebaskan memori
      document.documentElement.classList.remove("has-modal");
      dialog._opener?.focus?.();
    }, 160);
  }

  document.addEventListener("click", (e) => {
    const link = e.target.closest?.("a[data-pdf-viewer]");
    if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    open(link.getAttribute("href"), link.dataset.pdfViewer, link);
  });
})();
