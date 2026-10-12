// Outrider's shared UI: the (i) tooltip (hover, focus or tap), a wallet's avatar, toasts and underlined tabs.
(() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const tip = document.createElement("div");
  tip.id = "tip"; tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  const hover = matchMedia("(hover: hover)");
  let owner = null;

  function show(el) {
    if (owner && owner !== el) owner.setAttribute("aria-expanded", "false");
    owner = el; el.setAttribute("aria-expanded", "true");
    tip.textContent = el.dataset.tip;
    tip.style.maxWidth = Math.min(300, innerWidth - 24) + "px";
    const r = el.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    const x = Math.max(12, Math.min(r.left + r.width / 2 - tw / 2, innerWidth - tw - 12));
    const y = r.bottom + th + 22 > innerHeight ? r.top - th - 10 : r.bottom + 10;
    tip.style.left = x + "px"; tip.style.top = y + "px";
    tip.classList.add("on");
  }
  function hide() { if (owner) owner.setAttribute("aria-expanded", "false"); owner = null; tip.classList.remove("on"); }

  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-tip]"); if (el && hover.matches) show(el); });
  document.addEventListener("mouseout", (e) => { const el = e.target.closest("[data-tip]"); if (el && el === owner && hover.matches) hide(); });
  document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-tip]"); if (el) show(el); });
  document.addEventListener("focusout", (e) => { if (e.target.closest("[data-tip]")) hide(); });
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-tip]");
    if (el) { e.preventDefault(); e.stopPropagation(); owner === el && !hover.matches ? hide() : show(el); } else hide();
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  addEventListener("scroll", hide, { passive: true, capture: true });

  // A wallet's avatar: two colours drawn from its address, the same everywhere it appears.
  function avatar(addr, size = 26) {
    const a = String(addr || "").toLowerCase(), h1 = parseInt(a.slice(2, 8) || "0", 16) % 360,
      h2 = (h1 + 40 + parseInt(a.slice(-6) || "0", 16) % 120) % 360;
    return `<span class="av" style="width:${size}px;height:${size}px;background:linear-gradient(135deg,hsl(${h1} 75% 58%),hsl(${h2} 70% 42%))"></span>`;
  }

  let toastTimer = null;
  function toast(text) {
    let t = document.getElementById("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "glass"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = text; t.classList.add("on");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
  }

  // Underlined tabs: buttons with data-v inside `el`; calls onPick(value) and keeps the underline on the choice.
  function tabs(el, onPick) {
    if (!el) return;
    el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      el.querySelectorAll("button").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
      onPick(b.dataset.v);
    }));
  }

  window.UI = {
    esc, avatar, toast, tabs, hide,
    info: (text) => `<button type="button" class="i" aria-label="What this means" aria-expanded="false" data-tip="${esc(text)}">i</button>`,
  };
})();
