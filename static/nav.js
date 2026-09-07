async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!res.ok) {
    const detail = data.detail;
    const message = typeof detail === "string" ? detail : (detail && JSON.stringify(detail)) || res.statusText;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return data;
}

const BRAND_LOGO_SRC = "/static/assets/branding/flexavior-logo.png";
const FAVICON_SRC = "/static/assets/branding/favicon.ico";
const FAVICON_PNG_SRC = "/static/assets/branding/favicon.png";

function ensureFavicon() {
  const head = document.head;
  let icon = document.querySelector("link[rel='icon'][sizes='any'], link[rel='icon']:not([type='image/png'])");
  if (!icon) {
    icon = document.createElement("link");
    icon.setAttribute("rel", "icon");
    head.appendChild(icon);
  }
  icon.setAttribute("href", FAVICON_SRC);

  let pngIcon = document.querySelector("link[rel='icon'][type='image/png']");
  if (!pngIcon) {
    pngIcon = document.createElement("link");
    pngIcon.setAttribute("rel", "icon");
    pngIcon.setAttribute("type", "image/png");
    head.appendChild(pngIcon);
  }
  pngIcon.setAttribute("href", FAVICON_PNG_SRC);
}

ensureFavicon();

async function requireAuth(roles) {
  let me;
  try {
    me = await api("/api/me");
  } catch (err) {
    if (err.status === 401) {
      location.href = "/login";
      throw err;
    }
    throw err;
  }
  if (roles && !roles.includes(me.role)) {
    location.href = me.role === "student" ? "/student" : "/dashboard";
    throw new Error("role");
  }
  renderNav(me);
  return me;
}

function normalizeText(value) {
  return String(value || "").toLowerCase().trim();
}

function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function getNavSearch() {
  return "";
}

function onNavSearch(fn) {
  fn("");
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  const focus = modal.querySelector("input, select, textarea, button");
  if (focus) focus.focus();
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  if (!document.querySelector(".modal:not(.hidden)")) {
    document.body.classList.remove("modal-open");
  }
}

function bindModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", () => closeModal(id));
  });
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal(id);
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const open = document.querySelector(".modal:not(.hidden)");
  if (open) closeModal(open.id);
});

function filterCollection(inputId, selector) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const run = () => {
    const needle = normalizeText(input.value);
    document.querySelectorAll(selector).forEach((node) => {
      const hay = normalizeText(node.getAttribute("data-search") || node.textContent);
      node.classList.toggle("hidden", !!needle && !hay.includes(needle));
    });
  };
  input.addEventListener("input", run);
  run();
}

function toggleAll(buttonId, selector) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  let selected = false;
  btn.addEventListener("click", () => {
    selected = !selected;
    document.querySelectorAll(selector).forEach((el) => {
      el.checked = selected;
    });
    btn.textContent = selected ? "Deselect all" : "Select all";
  });
}

function renderNav(me) {
  const host = document.getElementById("app-nav");
  if (!host) return;
  const links = me.role === "student"
    ? [["/student", "Dashboard"]]
    : [
        ["/dashboard", "Dashboard"],
        ["/assess", "Assess"],
        ["/jobs", "Recent Job Worker"],
        ["/files", "Files"],
        ["/review", "Review queue"],
      ];
  if (me.role === "student" && me.can_self_grade) {
    links.push(["/student/assess", "Assess"]);
  }
  if (me.role === "supervisor") {
    links.push(["/accounts", "Accounts"]);
  }

  const here = location.pathname;
  let settingsHash = (location.hash || "#deadlines").replace("#", "") || "deadlines";
  if (settingsHash === "audit") settingsHash = "log";
  const settingsOnPage = here === "/settings";
  const settingsItems = [
    ["deadlines", "Edit Schedule"],
    ["slides", "Course slides"],
    ["textbook", "Textbook"],
    ["rubric", "Rubric"],
    ["log", "System Log"],
  ];

  const navLinks = links.map(([href, label]) =>
    `<a href="${href}" class="${here === href ? "active" : ""}">${label}</a>`
  ).join("");

  const settingsMenu = me.role === "supervisor"
    ? `<div class="nav-dropdown" id="settings-menu">
        <button type="button" class="nav-dropdown__toggle ${settingsOnPage ? "active" : ""}" id="settings-toggle" aria-expanded="false" aria-haspopup="true">
          Settings
        </button>
        <div class="nav-dropdown__panel" role="menu">
          ${settingsItems.map(([id, label]) =>
            `<a href="/settings#${id}" class="nav-dropdown__item ${settingsOnPage && settingsHash === id ? "active" : ""}" role="menuitem">${label}</a>`
          ).join("")}
        </div>
      </div>`
    : "";

  host.innerHTML = `
    <div class="nav-shell">
      <div class="nav-bar">
        <div class="brand">
          <span class="brand-row">
            <img src="${BRAND_LOGO_SRC}" alt="Flexavior" class="brand-logo" onerror="this.style.display='none'">
          </span>
          <div class="brand-title">Flexavior Assessment Portal</div>
          <small>${me.role}</small>
        </div>
        <nav>${navLinks}${settingsMenu}</nav>
        <div class="nav-actions">
          <button type="button" id="logout-btn" class="btn-secondary nav-logout">Sign out</button>
        </div>
      </div>
    </div>`;

  document.getElementById("logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: "{}" });
    location.href = "/login";
  });

  const toggle = document.getElementById("settings-toggle");
  const menu = document.getElementById("settings-menu");
  if (toggle && menu) {
    const closeMenu = () => {
      menu.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    };
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = menu.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    menu.querySelectorAll(".nav-dropdown__item").forEach((item) => {
      item.addEventListener("click", () => {
        closeMenu();
      });
    });
    document.addEventListener("click", (e) => {
      if (!menu.contains(e.target)) {
        closeMenu();
      }
    });
  }
}
