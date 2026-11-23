// Pop-up konfirmasi kustom (pengganti confirm() bawaan browser).
//
// htmx  : <button hx-post="…" hx-confirm="Pesan" data-confirm-title="Judul" data-confirm-ok="Ya" data-confirm-tone="danger">
// form  : <form data-confirm="Pesan" …>  atau  <button type="submit" data-confirm="Pesan" formaction="…">
// opsional: data-busy="Memproses…" → teks tombol setelah dikonfirmasi (form biasa).
(function () {
  const ICONS = {
    primary: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    danger: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 8v5m0 3.5v.01" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M10.3 3.9L2.6 17.5A2 2 0 004.3 20.5h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
  };
  let dialog = null;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "modal";
    dialog.setAttribute("aria-labelledby", "modal-title");
    dialog.setAttribute("aria-describedby", "modal-msg");
    dialog.innerHTML = `
      <form method="dialog" class="modal-card">
        <div class="modal-icon"></div>
        <div class="modal-body">
          <h2 id="modal-title" class="modal-title"></h2>
          <p id="modal-msg" class="modal-msg"></p>
        </div>
        <div class="modal-actions">
          <button class="btn" value="cancel" type="submit" data-act="cancel"></button>
          <button class="btn btn-primary" value="ok" type="submit" data-act="ok"></button>
        </div>
      </form>`;
    document.body.appendChild(dialog);
    // klik area gelap di luar kartu = batal
    dialog.addEventListener("mousedown", (e) => { if (e.target === dialog) dialog._backdrop = true; });
    dialog.addEventListener("mouseup", (e) => {
      if (e.target === dialog && dialog._backdrop) finish("cancel");
      dialog._backdrop = false;
    });
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish("cancel"); }); // tombol Esc
    dialog.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); finish(e.submitter?.value || "cancel"); });
  }

  function finish(value) {
    if (!dialog?.open || dialog.classList.contains("is-closing")) return;
    dialog.classList.add("is-closing");
    const resolve = dialog._resolve;
    setTimeout(() => { dialog.classList.remove("is-closing"); dialog.close(); dialog._opener?.focus?.(); }, 150);
    resolve?.(value === "ok");
  }

  // Bisa dipakai dari kode lain: await window.uiConfirm({message, title, ok, cancel, tone})
  window.uiConfirm = function ({ message, title = "Lanjutkan?", ok = "Ya, lanjutkan", cancel = "Batal", tone = "primary", opener = null }) {
    if (!dialog) build();
    if (dialog.open) finish("cancel");
    dialog.dataset.tone = tone;
    dialog.querySelector(".modal-icon").innerHTML = ICONS[tone] || ICONS.primary;
    dialog.querySelector(".modal-title").textContent = title;
    dialog.querySelector(".modal-msg").textContent = message || "";
    dialog.querySelector(".modal-msg").hidden = !message;
    dialog.querySelector('[data-act="ok"]').textContent = ok;
    const okBtn = dialog.querySelector('[data-act="ok"]');
    okBtn.className = "btn " + (tone === "danger" ? "btn-danger" : "btn-primary");
    dialog.querySelector('[data-act="cancel"]').textContent = cancel;
    dialog._opener = opener || document.activeElement;
    return new Promise((resolve) => {
      dialog._resolve = resolve;
      dialog.showModal();
      okBtn.focus();
    });
  };

  function optionsFrom(el, message) {
    const d = el.dataset;
    const danger = d.confirmTone === "danger" || el.classList.contains("text-danger");
    return {
      message,
      title: d.confirmTitle || (danger ? "Yakin?" : "Lanjutkan?"),
      ok: d.confirmOk || (danger ? "Ya, hapus" : "Ya, lanjutkan"),
      cancel: d.confirmCancel || "Batal",
      tone: danger ? "danger" : (d.confirmTone || "primary"),
      opener: el,
    };
  }

  // htmx: hx-confirm → pop-up kustom
  document.addEventListener("htmx:confirm", (e) => {
    const question = e.detail.question;
    if (!question) return;
    e.preventDefault();
    window.uiConfirm(optionsFrom(e.detail.elt, question)).then((yes) => { if (yes) e.detail.issueRequest(true); });
  });

  // form biasa: data-confirm di form atau di tombol submit
  document.addEventListener("submit", (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.method === "dialog") return;
    const btn = e.submitter;
    const source = btn?.dataset.confirm ? btn : form;
    const message = source.dataset.confirm;
    const markBusy = () => {
      const busy = btn?.dataset.busy || form.dataset.busy;
      const target = btn || form.querySelector('button[type="submit"], button:not([type])');
      if (busy && target) { target.textContent = busy; target.disabled = true; }
    };
    if (!message || form._confirmed) {
      form._confirmed = false;
      // tunda supaya nilai tombol (formaction/name) tetap ikut terkirim sebelum tombol dinonaktifkan
      setTimeout(markBusy);
      return;
    }
    e.preventDefault();
    window.uiConfirm(optionsFrom(source, message)).then((yes) => {
      if (!yes) return;
      form._confirmed = true;
      form.requestSubmit(btn || undefined);
    });
  });
})();
