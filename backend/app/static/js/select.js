// Dropdown kustom untuk semua <select> (kecuali multiple / data-native).
// <select> asli tetap ada (tersembunyi) sebagai sumber nilai, jadi form & htmx (hx-trigger="change") tetap jalan.
// Tambahkan atribut data-search pada <select> untuk kotak pencarian (mis. dropdown Lokasi).
(function () {
  const CHEVRON = '<svg class="dd-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const COUNT = /\s*\((\d[\d.]*)\)\s*$/;
  let open = null; // {select, wrap, btn, panel, input, list, items, active}
  let uid = 0;

  const norm = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function splitCount(text) {
    const m = text.match(COUNT);
    return m ? [text.slice(0, m.index), m[1]] : [text, ""];
  }

  function syncLabel(select) {
    const btn = select._ddBtn;
    if (!btn) return;
    const opt = select.options[select.selectedIndex];
    btn.querySelector(".dd-value").textContent = opt ? splitCount(opt.text)[0] : "";
    btn.disabled = select.disabled;
  }

  function enhance(select) {
    if (select._ddBtn || select.multiple || select.hasAttribute("data-native")) return;
    const wrap = document.createElement("div");
    wrap.className = "dd" + (select.classList.contains("select-sm") ? " dd-sm" : "");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dd-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML = '<span class="dd-value"></span>' + CHEVRON;
    const lbl = select.labels?.[0];
    const name = lbl ? (lbl.querySelector(".label")?.textContent || lbl.textContent).trim() : select.name;
    if (name) btn.setAttribute("aria-label", name + ": pilih");
    select.parentNode.insertBefore(wrap, select);
    wrap.append(btn, select);
    select.classList.add("dd-native");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    select._ddBtn = btn;
    btn._ddSelect = select;
    syncLabel(select);
    select.addEventListener("change", () => syncLabel(select));
    btn.addEventListener("click", () => (open && open.select === select ? close() : show(select)));
    btn.addEventListener("keydown", (e) => {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) { e.preventDefault(); show(select); }
    });
  }

  function buildItems(select) {
    const rows = [];
    for (const node of select.children) {
      if (node.tagName === "OPTGROUP") {
        rows.push({ group: node.label });
        for (const o of node.children) rows.push({ opt: o, group: node.label });
      } else if (node.tagName === "OPTION") rows.push({ opt: node, group: "" });
    }
    return rows;
  }

  function show(select) {
    close();
    const searchable = select.hasAttribute("data-search");
    const id = "dd-" + ++uid;
    const panel = document.createElement("div");
    panel.className = "dd-panel";
    panel.innerHTML =
      (searchable ? `<div class="dd-search"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="text" placeholder="${esc(select.dataset.search || "Cari…")}" aria-label="Cari pilihan" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="${id}"></div>` : "") +
      `<ul class="dd-list" role="listbox" id="${id}" tabindex="-1"></ul><p class="dd-empty" hidden>Tidak ditemukan</p>`;
    document.body.appendChild(panel);
    const list = panel.querySelector(".dd-list");
    const rows = buildItems(select);
    let html = "";
    rows.forEach((r, i) => {
      if (!r.opt) { html += `<li class="dd-group" role="presentation" data-group="${esc(r.group)}">${esc(r.group)}</li>`; return; }
      const [text, count] = splitCount(r.opt.text);
      const sel = r.opt.selected;
      html += `<li class="dd-opt${r.group ? " in-group" : ""}${sel ? " is-selected" : ""}" role="option" id="${id}-${i}" data-i="${i}" aria-selected="${sel}"${r.opt.disabled ? ' aria-disabled="true"' : ""}>` +
        `<span class="dd-text">${esc(text)}</span>${count ? `<span class="dd-count">${count}</span>` : ""}` +
        `<svg class="dd-check" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></li>`;
    });
    list.innerHTML = html;
    const items = [...list.querySelectorAll(".dd-opt")];
    items.forEach((li) => {
      const r = rows[+li.dataset.i];
      li._opt = r.opt;
      li._key = norm(splitCount(r.opt.text)[0] + " " + r.group);
    });
    const input = panel.querySelector("input");
    open = { select, btn: select._ddBtn, panel, input, list, items, active: null };
    select._ddBtn.setAttribute("aria-expanded", "true");
    select._ddBtn.closest(".dd").classList.add("is-open");
    place();
    setActive(items.find((li) => li._opt.selected) || items[0], true);
    requestAnimationFrame(() => panel.classList.add("is-open"));

    list.addEventListener("mousemove", (e) => { const li = e.target.closest(".dd-opt"); if (li && li !== open?.active) setActive(li); });
    list.addEventListener("mousedown", (e) => e.preventDefault()); // fokus tetap di input/tombol
    list.addEventListener("click", (e) => { const li = e.target.closest(".dd-opt"); if (li) choose(li); });
    (input || list).addEventListener("keydown", onKey);
    if (input) {
      input.addEventListener("input", () => filter(input.value));
      input.focus();
    } else list.focus();
  }

  function filter(q) {
    const words = norm(q).split(" ").filter(Boolean);
    let shown = 0;
    const groupsWithHits = new Set();
    for (const li of open.items) {
      const hit = words.every((w) => li._key.includes(w));
      li.hidden = !hit;
      if (hit) { shown++; groupsWithHits.add(li.previousGroup ?? groupOf(li)); }
    }
    open.list.querySelectorAll(".dd-group").forEach((g) => (g.hidden = !groupsWithHits.has(g)));
    open.panel.querySelector(".dd-empty").hidden = shown > 0;
    setActive(open.items.find((li) => !li.hidden) || null, true);
  }

  function groupOf(li) {
    if (li.previousGroup !== undefined) return li.previousGroup;
    let p = li.previousElementSibling;
    while (p && !p.classList.contains("dd-group")) p = p.previousElementSibling;
    li.previousGroup = p || null;
    return li.previousGroup;
  }

  function setActive(li, scroll) {
    if (!open) return;
    open.active?.classList.remove("is-active");
    open.active = li;
    const host = open.input || open.list;
    if (!li) { host.removeAttribute("aria-activedescendant"); return; }
    li.classList.add("is-active");
    host.setAttribute("aria-activedescendant", li.id);
    if (scroll) li.scrollIntoView({ block: "nearest" });
  }

  function move(step) {
    const vis = open.items.filter((li) => !li.hidden && li.getAttribute("aria-disabled") !== "true");
    if (!vis.length) return;
    let i = vis.indexOf(open.active) + step;
    if (step === Infinity) i = vis.length - 1;
    if (step === -Infinity) i = 0;
    setActive(vis[Math.max(0, Math.min(vis.length - 1, i))], true);
  }

  let typed = "", typedAt = 0;
  function onKey(e) {
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); move(1); break;
      case "ArrowUp": e.preventDefault(); move(-1); break;
      case "PageDown": e.preventDefault(); move(8); break;
      case "PageUp": e.preventDefault(); move(-8); break;
      case "Home": if (!open.input) { e.preventDefault(); move(-Infinity); } break;
      case "End": if (!open.input) { e.preventDefault(); move(Infinity); } break;
      case "Enter": e.preventDefault(); if (open.active) choose(open.active); break;
      case "Escape": e.preventDefault(); close(true); break;
      case "Tab": close(); break;
      default:
        if (!open.input && e.key.length === 1) { // ketik huruf → lompat ke pilihan yang cocok
          typed = (Date.now() - typedAt > 700 ? "" : typed) + norm(e.key);
          typedAt = Date.now();
          const li = open.items.find((x) => x._key.startsWith(typed));
          if (li) setActive(li, true);
        }
    }
  }

  function choose(li) {
    if (li.getAttribute("aria-disabled") === "true") return;
    const { select } = open;
    const changed = !li._opt.selected;
    li._opt.selected = true;
    close(true);
    syncLabel(select);
    if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function close(focusBtn) {
    if (!open) return;
    const { panel, btn } = open;
    btn.setAttribute("aria-expanded", "false");
    btn.closest(".dd")?.classList.remove("is-open");
    panel.classList.remove("is-open");
    panel.classList.add("is-closing");
    setTimeout(() => panel.remove(), 160);
    open = null;
    if (focusBtn) btn.focus();
  }

  function place() {
    if (!open) return;
    const r = open.btn.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight) return close();
    const p = open.panel;
    const width = Math.max(r.width, open.input ? 280 : 0);
    const left = Math.min(r.left, innerWidth - width - 8);
    p.style.width = width + "px";
    p.style.left = Math.max(8, left) + "px";
    const below = innerHeight - r.bottom - 8, above = r.top - 8;
    const up = below < 240 && above > below;
    const max = Math.min(open.input ? 380 : 300, (up ? above : below) - 6);
    p.style.maxHeight = Math.max(140, max) + "px";
    p.classList.toggle("is-up", up);
    p.style.top = up ? "" : r.bottom + 6 + "px";
    p.style.bottom = up ? innerHeight - r.top + 6 + "px" : "";
  }

  document.addEventListener("mousedown", (e) => {
    if (open && !open.panel.contains(e.target) && !open.btn.contains(e.target)) close();
  });
  addEventListener("resize", place);
  addEventListener("scroll", place, true);

  function enhanceAll(root) {
    (root.querySelectorAll ? root : document).querySelectorAll("select").forEach(enhance);
    if (root.tagName === "SELECT") enhance(root);
  }
  enhanceAll(document);
  document.addEventListener("htmx:load", (e) => enhanceAll(e.detail.elt));
  document.addEventListener("htmx:beforeSwap", () => close());
  // form.reset() mengembalikan nilai <select> tanpa event change → perbarui tampilan tombol
  document.addEventListener("reset", (e) => setTimeout(() => e.target.querySelectorAll?.("select").forEach(syncLabel)), true);
})();
