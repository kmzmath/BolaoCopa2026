const state = {
  user: null,
  authMode: "login",
  activeTab: "predictions",
  scope: "upcoming",
  scopeAnimation: null,
  expandedRankingId: null,
  adminView: "results",
  matches: [],
  ranking: [],
  adminUsers: [],
  resultAudits: [],
  publicPredictions: new Map(),
  serverOffsetMs: 0,
  details: {
    matchId: null,
    tab: "predictions",
  },
  filters: {
    group: "all",
    phase: "all",
    status: "all",
    sort: "time",
  },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const FLAG_CODES = {
  "África do Sul": "za",
  Alemanha: "de",
  "Arábia Saudita": "sa",
  Argélia: "dz",
  Argentina: "ar",
  Austrália: "au",
  Áustria: "at",
  Bélgica: "be",
  "Bósnia e Herzegovina": "ba",
  Brasil: "br",
  "Cabo Verde": "cv",
  Canadá: "ca",
  Catar: "qa",
  Colômbia: "co",
  "Coreia do Sul": "kr",
  "República da Coreia": "kr",
  "Costa do Marfim": "ci",
  Croácia: "hr",
  Curaçao: "cw",
  Egito: "eg",
  Equador: "ec",
  Escócia: "gb-sct",
  Espanha: "es",
  "Estados Unidos": "us",
  França: "fr",
  Gana: "gh",
  Haiti: "ht",
  Holanda: "nl",
  Inglaterra: "gb-eng",
  Irã: "ir",
  Iraque: "iq",
  Japão: "jp",
  Jordânia: "jo",
  Marrocos: "ma",
  México: "mx",
  Noruega: "no",
  "Nova Zelândia": "nz",
  Panamá: "pa",
  Paraguai: "py",
  Portugal: "pt",
  "RD Congo": "cd",
  "República Tcheca": "cz",
  Tchéquia: "cz",
  Senegal: "sn",
  Suécia: "se",
  Suíça: "ch",
  Tunísia: "tn",
  Turquia: "tr",
  Uruguai: "uy",
  Uzbequistão: "uz",
};

const FILTER_GROUPS = [
  {
    key: "group",
    title: "Grupos",
    className: "group-buttons",
    options: [
      { value: "all", label: "Todos" },
      ...Array.from({ length: 12 }, (_, index) => ({ value: String.fromCharCode(65 + index), label: `Grupo ${String.fromCharCode(65 + index)}` })),
    ],
  },
  {
    key: "phase",
    title: "Fases",
    className: "phase-buttons",
    options: [
      { value: "all", label: "Todas" },
      { value: "grupos", label: "Grupos" },
      { value: "segunda_fase", label: "32 avos" },
      { value: "oitavas", label: "Oitavas" },
      { value: "quartas", label: "Quartas" },
      { value: "semifinal", label: "Semifinal" },
      { value: "terceiro_lugar", label: "3º Lugar" },
      { value: "final", label: "Final" },
    ],
  },
  {
    key: "status",
    title: "Status",
    className: "status-buttons",
    options: [
      { value: "all", label: "Todos" },
      { value: "agendado", label: "Agendado" },
      { value: "em andamento", label: "Em andamento" },
      { value: "encerrado", label: "Encerrado" },
    ],
  },
  {
    key: "sort",
    title: "Ordenar",
    className: "sort-buttons",
    options: [
      { value: "time", label: "Horário" },
      { value: "group", label: "Grupo" },
      { value: "phase", label: "Fase" },
    ],
  },
];

document.addEventListener("DOMContentLoaded", () => {
  bindUi();
  registerServiceWorker();
  boot();
});

function bindUi() {
  $$(".auth-tab").forEach((button) => {
    button.addEventListener("click", () => setAuthMode(button.dataset.authMode));
  });

  $("#auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#auth-username").value;
    const password = $("#auth-password").value;
    const endpoint = state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
    try {
      const body = { username, password };
      if (state.authMode === "register") {
        const file = $("#auth-photo").files[0];
        if (!file) {
          showToast("Escolha uma foto para criar a conta.", "error");
          return;
        }
        body.avatar = await resizeAvatar(file);
      }
      const data = await api(endpoint, { method: "POST", body });
      state.user = data.user;
      showToast(data.message || "Tudo certo.");
      $("#auth-photo").value = "";
      await enterApp();
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  $("#auth-photo").addEventListener("change", () => {
    const file = $("#auth-photo").files[0];
    if (!file) {
      renderAvatarElement($("#auth-avatar-preview"), { username: $("#auth-username").value || "?" });
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    renderAvatarElement($("#auth-avatar-preview"), { username: $("#auth-username").value || "?", avatar_url: previewUrl });
  });

  $("#auth-username").addEventListener("input", () => {
    if (state.authMode !== "register" || $("#auth-photo").files[0]) return;
    renderAvatarElement($("#auth-avatar-preview"), { username: $("#auth-username").value || "?" });
  });

  $("#logout-button").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => null);
    state.user = null;
    state.matches = [];
    state.ranking = [];
    showAuth();
  });

  $("#profile-photo").addEventListener("change", () => {
    const file = $("#profile-photo").files[0];
    if (!file) {
      renderProfile();
      return;
    }
    const previewUrl = URL.createObjectURL(file);
    renderAvatarElement($("#profile-avatar"), { username: state.user.username, avatar_url: previewUrl });
  });

  $("#profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("#profile-photo").files[0];
    if (!file) {
      showToast("Selecione uma foto antes de salvar.", "error");
      return;
    }
    try {
      const avatar = await resizeAvatar(file);
      const data = await api("/api/me/avatar", { method: "POST", body: { avatar } });
      state.user = data.user;
      $("#profile-photo").value = "";
      renderUserChrome();
      renderProfile();
      await loadRanking(false);
      showToast(data.message || "Perfil atualizado.");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  $("#remove-avatar-button").addEventListener("click", async () => {
    try {
      const data = await api("/api/me/avatar", { method: "POST", body: { clear: true } });
      state.user = data.user;
      $("#profile-photo").value = "";
      renderUserChrome();
      renderProfile();
      await loadRanking(false);
      showToast("Foto removida.");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  $("#refresh-button").addEventListener("click", () => loadAll(true));
  $("#missing-counter").addEventListener("click", () => {
    state.scopeAnimation = state.scope === "all" ? "right" : "left";
    state.scope = "missing";
    renderScopeTabs();
    renderMatches();
  });

  $$(".main-tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeTab = button.dataset.tab;
      renderTabs();
    });
  });

  $$(".scope-tab").forEach((button) => {
    button.addEventListener("click", () => {
      if (state.scope === button.dataset.scope) return;
      state.scopeAnimation = button.dataset.scope === "all" ? "left" : "right";
      state.scope = button.dataset.scope;
      renderScopeTabs();
      renderMatches();
    });
  });

  $("#filter-buttons").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const key = button.dataset.filterKey;
    if (!key) return;
    state.filters[key] = button.dataset.value;
    if (key === "group" && button.dataset.value !== "all") state.filters.phase = "all";
    if (key === "phase" && button.dataset.value !== "all") state.filters.group = "all";
    if (key !== "sort") state.scope = "all";
    renderScopeTabs();
    renderFilterButtons();
    renderMatches();
  });

  $("#matches-list").addEventListener("click", handleMatchClick);
  $("#matches-list").addEventListener("input", handleScoreInput);
  $("#ranking-list").addEventListener("click", handleRankingClick);
  $("#admin-list").addEventListener("click", handleAdminClick);
  $("#admin-list").addEventListener("input", handleScoreInput);
  $("#panel-admin").addEventListener("click", handleAdminPanelClick);
  $("#details-modal").addEventListener("click", handleDetailsClick);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.details.matchId) closeDetails();
  });

  setInterval(() => {
    renderClock();
  }, 1000);

  setInterval(() => {
    if (!isEditing()) {
      renderMatches();
      renderAdmin();
    }
  }, 30000);

  setInterval(() => {
    if (state.user && !isEditing()) {
      loadAll(false);
    }
  }, 60000);
}

async function boot() {
  try {
    const data = await api("/api/me");
    state.user = data.user;
    if (state.user) {
      await enterApp();
    } else {
      showAuth();
    }
  } catch {
    showAuth();
  }
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => null);
  });
}

async function enterApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  renderUserChrome();
  $$(".admin-only").forEach((element) => element.classList.toggle("hidden", !state.user?.is_admin));
  if (!state.user?.is_admin && state.activeTab === "admin") {
    state.activeTab = "predictions";
  }
  renderScopeTabs();
  renderTabs();
  await loadAll(false);
}

function renderUserChrome() {
  $("#current-user").textContent = state.user?.is_admin ? `${state.user.username} · admin` : state.user.username;
  renderAvatarElement($("#topbar-avatar"), state.user);
  renderProfile();
}

function showAuth() {
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  $("#auth-password").value = "";
  $("#auth-photo").value = "";
  renderAvatarElement($("#auth-avatar-preview"), { username: $("#auth-username").value || "?" });
}

function setAuthMode(mode) {
  state.authMode = mode;
  $$(".auth-tab").forEach((button) => button.classList.toggle("active", button.dataset.authMode === mode));
  $("#auth-submit-label").textContent = mode === "register" ? "Criar conta" : "Entrar";
  $("#auth-password").autocomplete = mode === "register" ? "new-password" : "current-password";
  $("#auth-avatar-field").classList.toggle("hidden", mode !== "register");
  $("#auth-photo").required = mode === "register";
  if (mode !== "register") {
    $("#auth-photo").value = "";
  }
  renderAvatarElement($("#auth-avatar-preview"), { username: $("#auth-username").value || "?" });
}

async function loadAll(withToast = false) {
  const jobs = [loadMatches(false), loadRanking(false)];
  if (state.user?.is_admin) {
    jobs.push(loadAdminUsers(false), loadResultAudits(false));
  }
  await Promise.all(jobs);
  if (withToast) showToast("Dados atualizados.");
}

async function loadMatches() {
  const data = await api("/api/matches");
  state.matches = data.matches;
  state.serverOffsetMs = new Date(data.server_now).getTime() - Date.now();
  renderFilterButtons();
  renderClock();
  renderMissingCounter();
  renderMatches();
  renderAdmin();
  if (state.details.matchId) renderDetailsModal();
}

async function loadRanking() {
  const data = await api("/api/ranking");
  state.ranking = data.ranking;
  renderRanking();
}

async function loadAdminUsers() {
  if (!state.user?.is_admin) return;
  const data = await api("/api/admin/users");
  state.adminUsers = data.users;
  renderAdminUsers();
}

async function loadResultAudits() {
  if (!state.user?.is_admin) return;
  const data = await api("/api/admin/result-audits");
  state.resultAudits = data.audits;
  renderAdminAudit();
}

async function api(path, options = {}) {
  const init = {
    method: options.method || "GET",
    credentials: "same-origin",
    headers: {},
  };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Não foi possível concluir a ação.");
  }
  return data;
}

function renderTabs() {
  $$(".main-tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === state.activeTab));
  $$(".panel").forEach((panel) => panel.classList.remove("active"));
  const panel = $(`#panel-${state.activeTab}`);
  if (panel) panel.classList.add("active");
  if (state.activeTab === "profile") renderProfile();
  if (state.activeTab === "admin") renderAdmin();
}

function renderScopeTabs() {
  $$(".scope-tab").forEach((button) => button.classList.toggle("active", button.dataset.scope === state.scope));
  const tabs = $(".scope-tabs");
  tabs.classList.toggle("scope-all", state.scope === "all");
  tabs.classList.toggle("scope-upcoming", state.scope === "upcoming");
  tabs.classList.toggle("scope-missing", state.scope === "missing");
  $("#filter-buttons").classList.toggle("hidden", state.scope !== "all");
}

function renderProfile() {
  if (!state.user || !$("#profile-avatar")) return;
  $("#profile-username").textContent = state.user.username;
  renderAvatarElement($("#profile-avatar"), state.user);
  $("#remove-avatar-button").disabled = !state.user.avatar_url;
}

function avatarHtml(user, className = "avatar") {
  if (user?.avatar_url) {
    return `<span class="${className} has-image"><img src="${escapeHtml(user.avatar_url)}" alt="Foto de ${escapeHtml(user.username)}" /></span>`;
  }
  return `<span class="${className}">${escapeHtml(userInitials(user?.username || "?"))}</span>`;
}

function renderAvatarElement(element, user) {
  if (!element) return;
  element.classList.toggle("has-image", Boolean(user?.avatar_url));
  element.innerHTML = user?.avatar_url
    ? `<img src="${escapeHtml(user.avatar_url)}" alt="Foto de ${escapeHtml(user.username)}" />`
    : escapeHtml(userInitials(user?.username || "?"));
}

function userInitials(name) {
  const words = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return "?";
  return ((words[0][0] || "") + (words[1]?.[0] || "")).toUpperCase();
}

function resizeAvatar(file) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    return Promise.reject(new Error("Use uma imagem JPG, PNG ou WebP."));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Não foi possível ler a imagem."));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error("A imagem enviada está inválida."));
      image.onload = () => {
        const size = 384;
        const sourceSize = Math.min(image.width, image.height);
        const sourceX = Math.max(0, (image.width - sourceSize) / 2);
        const sourceY = Math.max(0, (image.height - sourceSize) / 2);
        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext("2d");
        context.drawImage(image, sourceX, sourceY, sourceSize, sourceSize, 0, 0, size, size);
        resolve(canvas.toDataURL("image/jpeg", 0.86));
      };
      image.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function renderFilterButtons() {
  const container = $("#filter-buttons");
  container.innerHTML = FILTER_GROUPS.map((group) => {
    const buttons = group.options
      .map((option) => {
        const active = state.filters[group.key] === option.value;
        const allClass = option.value === "all" ? " all-filter" : "";
        return `<button type="button" class="filter-button${allClass} ${active ? "active" : ""}" data-filter-key="${group.key}" data-value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</button>`;
      })
      .join("");
    return `
      <section class="filter-category ${group.className}">
        <h3>${escapeHtml(group.title)}</h3>
        <div class="filter-grid">${buttons}</div>
      </section>
    `;
  }).join("");
}

function renderClock() {
  const now = getNow();
  $("#server-clock").textContent = `Horário de Brasília: ${formatClockDateTime(now)}`;
  renderMissingCounter();
}

function missingMatches() {
  return state.matches.filter((match) => !match.my_prediction && !isLocked(match));
}

function renderMissingCounter() {
  const counter = $("#missing-counter");
  if (!counter) return;
  const count = missingMatches().length;
  counter.innerHTML = `
    <button type="button" class="missing-chip ${count ? "" : "complete"}" title="Ver jogos sem palpite">
      <span>Faltam</span>
      <strong>${count}</strong>
    </button>
  `;
}

function renderMatches() {
  const list = $("#matches-list");
  const matches = filteredMatches();
  if (!matches.length) {
    const message =
      state.scope === "upcoming"
        ? "Nenhuma partida nas próximas 24 horas."
        : state.scope === "missing"
          ? "Você já preencheu todos os palpites disponíveis."
          : "Nenhuma partida encontrada.";
    list.innerHTML = `<div class="empty-state">${message}</div>`;
    animateMatchList(list);
    return;
  }
  const sections = groupedMatches(matches);
  list.innerHTML = sections
    .map(
      (section) => `
        <section class="match-section">
          <h3>${escapeHtml(section.title)}</h3>
          <div class="match-grid">
            ${section.matches.map((match) => matchCard(match)).join("")}
          </div>
        </section>
      `
    )
    .join("");
  animateMatchList(list);
}

function animateMatchList(list) {
  if (!state.scopeAnimation) return;
  list.classList.remove("slide-in-left", "slide-in-right");
  void list.offsetWidth;
  list.classList.add(state.scopeAnimation === "left" ? "slide-in-left" : "slide-in-right");
  state.scopeAnimation = null;
}

function groupedMatches(matches) {
  if (state.scope === "upcoming") {
    return [{ title: "Próximas 24hrs", matches }];
  }
  if (state.scope === "missing") {
    return [{ title: "Faltam palpites", matches }];
  }
  const map = new Map();
  matches.forEach((match) => {
    const key = match.group_code ? `Grupo ${match.group_code}` : match.round_label || match.phase;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(match);
  });
  return Array.from(map.entries()).map(([title, grouped]) => ({ title, matches: grouped }));
}

function filteredMatches() {
  const now = getNow();
  const limit = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  let matches = state.matches.slice();
  if (state.scope === "upcoming") {
    matches = matches.filter((match) => {
      const start = new Date(match.start_at);
      return start >= now && start <= limit;
    });
  }
  if (state.scope === "missing") {
    matches = missingMatches();
  }
  if (state.filters.group !== "all") {
    matches = matches.filter((match) => match.group_code === state.filters.group);
  }
  if (state.filters.phase !== "all") {
    matches = matches.filter((match) => match.phase_slug === state.filters.phase);
  }
  if (state.filters.status !== "all") {
    matches = matches.filter((match) => currentStatus(match) === state.filters.status);
  }
  const sortMode = state.scope === "upcoming" || state.scope === "missing" ? "time" : state.filters.sort;
  matches.sort((a, b) => {
    if (sortMode === "group") {
      return `${a.group_code || "Z"}-${a.start_at}`.localeCompare(`${b.group_code || "Z"}-${b.start_at}`);
    }
    if (sortMode === "phase") {
      return `${a.phase_slug}-${a.start_at}`.localeCompare(`${b.phase_slug}-${b.start_at}`);
    }
    return new Date(a.start_at) - new Date(b.start_at) || a.fifa_number - b.fifa_number;
  });
  return matches;
}

function matchCard(match) {
  const prediction = match.my_prediction || {};
  const locked = isLocked(match);
  const homeValue = prediction.home_score ?? "";
  const awayValue = prediction.away_score ?? "";
  const needsAdvance = match.is_knockout && homeValue !== "" && awayValue !== "" && String(homeValue) === String(awayValue);
  const status = currentStatus(match);
  const statusClass = status === "encerrado" ? "gold" : status === "em andamento" ? "blue" : "";
  const points = prediction.points ? ` · ${prediction.points} pts` : "";
  return `
    <article class="match-card ${locked ? "locked" : ""}" data-match-id="${match.id}">
      <div class="match-top">
        <div>
          <div class="match-meta">
            <span class="pill stage-pill">${escapeHtml(matchStageLabel(match))}</span>
            <span class="pill ${statusClass}">${escapeHtml(status)}</span>
          </div>
          <div class="match-time">${formatDateTime(match.start_at)}</div>
        </div>
        <button type="button" class="details-icon open-details" title="Detalhes" aria-label="Abrir detalhes da partida">
          <span aria-hidden="true">?</span>
        </button>
      </div>
      <div class="match-body">
        <div class="score-row">
          ${teamBlock(match.team_a)}
          <input class="score-input" data-home type="number" min="0" max="99" inputmode="numeric" value="${escapeHtml(homeValue)}" ${locked ? "disabled" : ""} aria-label="Placar do time A" />
          <span class="versus">x</span>
          <input class="score-input" data-away type="number" min="0" max="99" inputmode="numeric" value="${escapeHtml(awayValue)}" ${locked ? "disabled" : ""} aria-label="Placar do time B" />
          ${teamBlock(match.team_b, true)}
        </div>
        <div class="advance-row ${needsAdvance ? "" : "hidden"}">
          <label>
            <span>Quem avança</span>
            <select data-advances ${locked ? "disabled" : ""}>
              ${advanceOptions(match, prediction.advances)}
            </select>
          </label>
        </div>
        ${resultLine(match)}
        <div class="actions-row">
          <button type="button" class="primary-action save-prediction" ${locked ? "disabled" : ""}>
            <span aria-hidden="true">✓</span><span>Salvar${points}</span>
          </button>
          <button type="button" class="small-action clear-prediction" ${locked || !match.my_prediction ? "disabled" : ""}>
            <span aria-hidden="true">×</span><span>Limpar</span>
          </button>
        </div>
      </div>
    </article>
  `;
}

function teamBlock(team, right = false) {
  const code = flagCodeForTeam(team.name);
  const flag = code
    ? `<img class="team-flag" src="https://flagcdn.com/w80/${code}.png" srcset="https://flagcdn.com/w160/${code}.png 2x" alt="Bandeira de ${escapeHtml(team.name)}" loading="lazy" />`
    : `<span class="team-flag placeholder" aria-hidden="true">${escapeHtml(teamInitials(team.name))}</span>`;
  return `
    <div class="team ${right ? "right" : ""}">
      ${flag}
      <strong>${escapeHtml(team.name)}</strong>
      ${team.reference ? `<small>${escapeHtml(team.reference)}</small>` : ""}
    </div>
  `;
}

function flagCodeForTeam(name) {
  return FLAG_CODES[name] || null;
}

function teamInitials(name) {
  const words = String(name || "")
    .replace(/\d+/g, "")
    .split(/\s+/)
    .filter((word) => word && !["de", "do", "da", "dos", "das"].includes(word.toLowerCase()));
  return (words[0]?.[0] || "?") + (words[1]?.[0] || "");
}

function advanceOptions(match, selected) {
  return `
    <option value="">Selecione</option>
    <option value="A" ${selected === "A" ? "selected" : ""}>${escapeHtml(match.team_a.name)} avança</option>
    <option value="B" ${selected === "B" ? "selected" : ""}>${escapeHtml(match.team_b.name)} avança</option>
  `;
}

function resultLine(match) {
  if (match.result_home === null || match.result_home === undefined) {
    return isLocked(match)
      ? `<div class="reveal-line">Palpites fechados desde ${formatDateTime(match.start_at)}.</div>`
      : "";
  }
  const winner =
    match.penalty_winner === "A"
      ? ` · ${escapeHtml(match.team_a.name)} avançou nos pênaltis`
      : match.penalty_winner === "B"
        ? ` · ${escapeHtml(match.team_b.name)} avançou nos pênaltis`
        : "";
  const myPoints = match.my_prediction ? ` · seu palpite: ${match.my_prediction.points || 0} pts` : "";
  return `<div class="result-line">Resultado: ${match.result_home} x ${match.result_away}${winner}${myPoints}</div>`;
}

function publicPredictionRow(row, match) {
  const advance =
    row.advances === "A"
      ? ` · ${escapeHtml(match.team_a.name)} avança`
      : row.advances === "B"
        ? ` · ${escapeHtml(match.team_b.name)} avança`
        : "";
  return `
    <div class="public-prediction">
      ${avatarHtml(row, "avatar user-avatar")}
      <strong>${escapeHtml(row.username)}</strong>
      <span class="prediction-score">${row.home_score} × ${row.away_score}${advance}</span>
    </div>
  `;
}

function matchStageLabel(match) {
  if (match.group_code) {
    return `Grupo ${match.group_code} · Rodada ${groupRound(match)}`;
  }
  return match.round_label || match.phase;
}

function groupRound(match) {
  const groupMatches = state.matches
    .filter((item) => item.phase_slug === "grupos" && item.group_code === match.group_code)
    .sort((a, b) => new Date(a.start_at) - new Date(b.start_at) || a.fifa_number - b.fifa_number);
  const index = groupMatches.findIndex((item) => item.id === match.id);
  return index >= 0 ? Math.floor(index / 2) + 1 : 1;
}

async function handleMatchClick(event) {
  const button = event.target.closest("button");
  if (!button) return;
  const card = button.closest(".match-card");
  if (!card) return;
  const matchId = Number(card.dataset.matchId);
  if (button.classList.contains("save-prediction")) {
    pulseButton(button);
    await savePrediction(card, matchId);
  }
  if (button.classList.contains("clear-prediction")) {
    await clearPrediction(matchId);
  }
  if (button.classList.contains("open-details")) {
    await openDetails(matchId);
  }
}

function pulseButton(button) {
  button.classList.remove("button-bump");
  void button.offsetWidth;
  button.classList.add("button-bump");
}

async function savePrediction(card, matchId) {
  const body = {
    home_score: $("[data-home]", card).value,
    away_score: $("[data-away]", card).value,
    advances: $("[data-advances]", card)?.value || null,
  };
  try {
    const data = await api(`/api/predictions/${matchId}`, { method: "POST", body });
    const match = state.matches.find((item) => item.id === matchId);
    if (match) match.my_prediction = data.prediction;
    showToast(data.message || "Palpite aceito.");
    await loadRanking(false);
    renderMissingCounter();
    renderMatches();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function clearPrediction(matchId) {
  try {
    const data = await api(`/api/predictions/${matchId}`, { method: "POST", body: { clear: true } });
    const match = state.matches.find((item) => item.id === matchId);
    if (match) match.my_prediction = null;
    showToast(data.message || "Palpite removido.");
    await loadRanking(false);
    renderMissingCounter();
    renderMatches();
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function openDetails(matchId) {
  const match = state.matches.find((item) => item.id === matchId);
  if (!match) return;
  try {
    if (match.public_predictions_visible && !state.publicPredictions.has(matchId)) {
      const data = await api(`/api/matches/${matchId}/predictions`);
      state.publicPredictions.set(matchId, data.predictions);
    }
    state.details.matchId = matchId;
    state.details.tab = "predictions";
    renderDetailsModal();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function closeDetails() {
  state.details.matchId = null;
  $("#details-modal").classList.add("hidden");
}

function handleDetailsClick(event) {
  if (event.target.closest("[data-close-details]")) {
    closeDetails();
    return;
  }
  const tab = event.target.closest("[data-details-tab]");
  if (!tab) return;
  state.details.tab = tab.dataset.detailsTab;
  renderDetailsModal();
}

function renderDetailsModal() {
  const modal = $("#details-modal");
  const match = state.matches.find((item) => item.id === state.details.matchId);
  if (!match) {
    closeDetails();
    return;
  }
  const rows = state.publicPredictions.get(match.id) || [];
  $("#details-title").textContent = `${match.team_a.name} × ${match.team_b.name}`;
  $$(".details-tab", modal).forEach((button) => button.classList.toggle("active", button.dataset.detailsTab === state.details.tab));
  $("#details-content").innerHTML = `
    <div class="details-meta">
      <span class="pill stage-pill">${escapeHtml(matchStageLabel(match))}</span>
      <span>${formatDateTime(match.start_at)}</span>
    </div>
    ${state.details.tab === "stats" ? detailsStats(match, rows) : detailsPredictions(match, rows)}
  `;
  modal.classList.remove("hidden");
}

function detailsPredictions(match, rows) {
  if (!match.public_predictions_visible) {
    return detailsHiddenMessage(match);
  }
  if (!rows.length) {
    return `<div class="empty-state">Ainda não há palpites salvos para esta partida.</div>`;
  }
  return `
    <div class="details-list">
      ${rows.map((row) => publicPredictionRow(row, match)).join("")}
    </div>
  `;
}

function detailsStats(match, rows) {
  if (!match.public_predictions_visible) {
    return detailsHiddenMessage(match);
  }
  if (!rows.length) {
    return `<div class="empty-state">Ainda não há estatísticas para esta partida.</div>`;
  }
  const total = rows.length;
  const buckets = [
    { key: "A", label: match.team_a.name, count: 0 },
    { key: "D", label: "Empate", count: 0 },
    { key: "B", label: match.team_b.name, count: 0 },
  ];
  rows.forEach((row) => {
    const key = Number(row.home_score) > Number(row.away_score) ? "A" : Number(row.home_score) < Number(row.away_score) ? "B" : "D";
    buckets.find((bucket) => bucket.key === key).count += 1;
  });
  const exactMap = new Map();
  rows.forEach((row) => {
    const score = `${row.home_score} × ${row.away_score}`;
    exactMap.set(score, (exactMap.get(score) || 0) + 1);
  });
  const topScores = Array.from(exactMap.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 5);
  return `
    <div class="stats-block">
      <h3>Distribuição de palpites</h3>
      ${buckets
        .map((bucket) => {
          const percent = Math.round((bucket.count / total) * 100);
          return `
            <div class="stat-row">
              <div class="stat-label">
                <strong>${escapeHtml(bucket.label)}</strong>
                <span>${percent}%</span>
              </div>
              <div class="stat-bar"><span style="width: ${percent}%"></span></div>
            </div>
          `;
        })
        .join("")}
    </div>
    <div class="stats-block">
      <h3>Placares mais escolhidos</h3>
      <div class="score-distribution">
        ${topScores.map(([score, count]) => `<div><span>${escapeHtml(score)}</span><strong>${count}</strong></div>`).join("")}
      </div>
    </div>
  `;
}

function detailsHiddenMessage(match) {
  return `
    <div class="details-warning">
      <strong>Palpites ainda ocultos</strong>
      <span>Os palpites dos jogadores serão liberados em ${formatDateTime(match.reveal_at)}.</span>
    </div>
  `;
}

function handleScoreInput(event) {
  const card = event.target.closest(".match-card");
  if (!card) return;
  updateAdvanceVisibility(card);
}

function updateAdvanceVisibility(card) {
  const match = state.matches.find((item) => item.id === Number(card.dataset.matchId));
  if (!match) return;
  const admin = card.dataset.admin === "true";
  const home = admin ? $(".admin-home", card)?.value : $("[data-home]", card)?.value;
  const away = admin ? $(".admin-away", card)?.value : $("[data-away]", card)?.value;
  const row = admin ? $(".penalty-row", card) : $(".advance-row", card);
  if (!row) return;
  row.classList.toggle("hidden", !(match.is_knockout && home !== "" && away !== "" && home === away));
}

function renderRanking() {
  const list = $("#ranking-list");
  if (!state.ranking.length) {
    list.innerHTML = `<div class="empty-state">O ranking aparece quando houver jogadores cadastrados.</div>`;
    return;
  }
  list.innerHTML = `
    <div class="ranking-head">
      <span>Pos.</span>
      <span>Foto</span>
      <span>Jogador</span>
      <span>Palp.</span>
      <span>Pts</span>
    </div>
    ${state.ranking
    .map(
      (row) => `
        <div class="ranking-item">
          <button type="button" class="ranking-row ${state.expandedRankingId === row.id ? "expanded" : ""}" data-user-id="${row.id}">
            <div class="rank-position">${row.position}</div>
            ${avatarHtml(row, "avatar rank-avatar")}
            <div>
              <strong>${escapeHtml(row.username)}</strong>
            </div>
            <span class="rank-predictions">${row.prediction_count}</span>
            <div class="rank-stats">
              <strong>${row.points}</strong>
            </div>
          </button>
          <div class="ranking-details ${state.expandedRankingId === row.id ? "" : "hidden"}">
            ${rankingBreakdown(row)}
          </div>
        </div>
      `
    )
    .join("")}
  `;
}

function handleRankingClick(event) {
  const row = event.target.closest(".ranking-row");
  if (!row) return;
  const userId = Number(row.dataset.userId);
  state.expandedRankingId = state.expandedRankingId === userId ? null : userId;
  renderRanking();
}

function rankingBreakdown(row) {
  const breakdown = row.breakdown || { settled_predictions: 0, rules: [] };
  const rules = breakdown.rules || [];
  return `
    <div class="ranking-details-head">
      <strong>${escapeHtml(row.username)}</strong>
      <span>${breakdown.settled_predictions || 0} palpites com resultado encerrado</span>
    </div>
    <div class="breakdown-grid">
      ${rules
        .map(
          (rule) => `
            <div class="breakdown-row">
              <span>${escapeHtml(rule.label)}</span>
              <strong>${rule.count || 0}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function renderAdmin() {
  if (!state.user?.is_admin) return;
  $$(".admin-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.adminView === state.adminView);
  });
  const list = $("#admin-list");
  const users = $("#admin-users");
  const audit = $("#admin-audit");
  list.classList.toggle("hidden", state.adminView !== "results");
  users.classList.toggle("hidden", state.adminView !== "users");
  audit.classList.toggle("hidden", state.adminView !== "audit");
  renderAdminUsers();
  renderAdminAudit();
  if (state.adminView !== "results") return;
  const matches = state.matches.slice().sort((a, b) => new Date(a.start_at) - new Date(b.start_at));
  list.innerHTML = matches.map((match) => adminCard(match)).join("");
}

function renderAdminUsers() {
  const container = $("#admin-users");
  if (!container || !state.user?.is_admin) return;
  if (!state.adminUsers.length) {
    container.innerHTML = `<div class="empty-state">Nenhum usuário cadastrado.</div>`;
    return;
  }
  container.innerHTML = state.adminUsers
    .map((user) => {
      const isSelf = user.id === state.user.id;
      return `
        <article class="admin-user-card ${user.active ? "" : "inactive"}" data-user-id="${user.id}">
          <div class="admin-user-main">
            ${avatarHtml(user, "avatar rank-avatar")}
            <div>
              <strong>${escapeHtml(user.username)}</strong>
              <span>${user.is_admin ? "Admin" : "Jogador"} · ${user.active ? "Ativo" : "Desativado"}</span>
            </div>
            <div class="admin-user-stats">
              <strong>${user.points}</strong>
              <span>${user.prediction_count} palpites</span>
            </div>
          </div>
          <div class="admin-user-form">
            <label>
              <span>Nome</span>
              <input class="admin-user-name" maxlength="40" value="${escapeHtml(user.username)}" />
            </label>
            <button type="button" class="small-action" data-admin-user-action="rename">Renomear</button>
            <label>
              <span>Nova senha</span>
              <input class="admin-user-password" type="password" autocomplete="new-password" placeholder="Mín. 6 caracteres" />
            </label>
            <button type="button" class="small-action" data-admin-user-action="reset-password">Resetar senha</button>
            <button type="button" class="${user.active ? "danger-action" : "small-action"}" data-admin-user-action="toggle-active" data-next-active="${user.active ? "false" : "true"}" ${isSelf && user.active ? "disabled" : ""}>
              ${user.active ? "Desativar" : "Ativar"}
            </button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderAdminAudit() {
  const container = $("#admin-audit");
  if (!container || !state.user?.is_admin) return;
  if (!state.resultAudits.length) {
    container.innerHTML = `<div class="empty-state">Nenhuma alteração de resultado registrada.</div>`;
    return;
  }
  container.innerHTML = `
    <div class="audit-list">
      ${state.resultAudits
        .map(
          (audit) => `
            <article class="audit-row">
              <div>
                <strong>${escapeHtml(audit.team_a)} x ${escapeHtml(audit.team_b)}</strong>
                <span>${formatDateTime(audit.changed_at)} · ${escapeHtml(audit.admin_username)} · ${audit.action === "clear_result" ? "reabriu" : "salvou"}</span>
              </div>
              <div class="audit-change">
                <span>${formatAuditResult(audit, "old")}</span>
                <strong>→</strong>
                <span>${formatAuditResult(audit, "new")}</span>
              </div>
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function formatAuditResult(audit, prefix) {
  const home = audit[`${prefix}_result_home`];
  const away = audit[`${prefix}_result_away`];
  const winner = audit[`${prefix}_penalty_winner`];
  if (home === null || home === undefined || away === null || away === undefined) return "sem resultado";
  return `${home} x ${away}${winner ? ` · pênaltis ${winner}` : ""}`;
}

function adminCard(match) {
  const hasResult = match.result_home !== null && match.result_home !== undefined;
  const needsPenalty = match.is_knockout && hasResult && String(match.result_home) === String(match.result_away);
  return `
    <article class="match-card" data-match-id="${match.id}" data-admin="true">
      <div class="match-top">
        <div>
          <div class="match-meta">
            <span class="pill stage-pill">${escapeHtml(matchStageLabel(match))}</span>
            <span class="pill ${currentStatus(match) === "encerrado" ? "gold" : ""}">${escapeHtml(currentStatus(match))}</span>
          </div>
          <div class="match-time">${formatDateTime(match.start_at)}</div>
        </div>
      </div>
      <div class="match-body">
        <div class="score-row">
          ${teamBlock(match.team_a)}
          <input class="score-input admin-home" type="number" min="0" max="99" inputmode="numeric" value="${escapeHtml(match.result_home ?? "")}" aria-label="Resultado do time A" />
          <span class="versus">x</span>
          <input class="score-input admin-away" type="number" min="0" max="99" inputmode="numeric" value="${escapeHtml(match.result_away ?? "")}" aria-label="Resultado do time B" />
          ${teamBlock(match.team_b, true)}
        </div>
        <div class="penalty-row ${needsPenalty ? "" : "hidden"}">
          <label>
            <span>Classificado nos pênaltis</span>
            <select class="admin-penalty">
              ${advanceOptions(match, match.penalty_winner)}
            </select>
          </label>
        </div>
        <div class="actions-row">
          <button type="button" class="primary-action save-result">
            <span aria-hidden="true">✓</span><span>${hasResult ? "Atualizar resultado" : "Encerrar partida"}</span>
          </button>
          <button type="button" class="danger-action clear-result" ${hasResult ? "" : "disabled"}>
            <span aria-hidden="true">×</span><span>Reabrir</span>
          </button>
        </div>
      </div>
    </article>
  `;
}

async function handleAdminClick(event) {
  const button = event.target.closest("button");
  if (!button) return;
  const card = button.closest(".match-card");
  if (!card) return;
  const matchId = Number(card.dataset.matchId);
  if (button.classList.contains("save-result")) {
    await saveResult(card, matchId);
  }
  if (button.classList.contains("clear-result")) {
    await clearResult(matchId);
  }
}

async function handleAdminPanelClick(event) {
  const viewButton = event.target.closest("[data-admin-view]");
  if (viewButton) {
    state.adminView = viewButton.dataset.adminView;
    renderAdmin();
    return;
  }

  const exportButton = event.target.closest("[data-export-format]");
  if (exportButton) {
    pulseButton(exportButton);
    await downloadAdminExport(exportButton.dataset.exportFormat);
    return;
  }

  const actionButton = event.target.closest("[data-admin-user-action]");
  if (!actionButton) return;
  const card = actionButton.closest(".admin-user-card");
  if (!card) return;
  const userId = Number(card.dataset.userId);
  const action = actionButton.dataset.adminUserAction;
  if (action === "rename") {
    await renameAdminUser(userId, card);
  }
  if (action === "reset-password") {
    await resetAdminUserPassword(userId, card);
  }
  if (action === "toggle-active") {
    await setAdminUserActive(userId, actionButton.dataset.nextActive === "true");
  }
}

async function renameAdminUser(userId, card) {
  const username = $(".admin-user-name", card).value;
  try {
    const data = await api(`/api/admin/users/${userId}/rename`, { method: "POST", body: { username } });
    state.adminUsers = data.users;
    syncCurrentUserFromAdminList();
    await loadRanking(false);
    renderUserChrome();
    renderAdminUsers();
    showToast(data.message || "Usuário renomeado.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function resetAdminUserPassword(userId, card) {
  const input = $(".admin-user-password", card);
  const password = input.value;
  try {
    const data = await api(`/api/admin/users/${userId}/reset-password`, { method: "POST", body: { password } });
    input.value = "";
    showToast(data.message || "Senha redefinida.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function setAdminUserActive(userId, active) {
  try {
    const data = await api(`/api/admin/users/${userId}/active`, { method: "POST", body: { active } });
    state.adminUsers = data.users;
    await loadRanking(false);
    renderAdminUsers();
    showToast(data.message || "Usuário atualizado.");
  } catch (error) {
    showToast(error.message, "error");
  }
}

function syncCurrentUserFromAdminList() {
  const updated = state.adminUsers.find((user) => user.id === state.user?.id);
  if (!updated) return;
  state.user = {
    ...state.user,
    username: updated.username,
    active: updated.active,
    avatar_url: updated.avatar_url,
  };
}

async function downloadAdminExport(format) {
  try {
    const response = await fetch(`/api/admin/export/${format}`, { credentials: "same-origin" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || "Não foi possível exportar os dados.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `bolao-copa-2026-export.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast(`Exportação ${format.toUpperCase()} gerada.`);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function saveResult(card, matchId) {
  const body = {
    result_home: $(".admin-home", card).value,
    result_away: $(".admin-away", card).value,
    penalty_winner: $(".admin-penalty", card)?.value || null,
  };
  try {
    const data = await api(`/api/admin/matches/${matchId}/result`, { method: "POST", body });
    showToast(data.message || "Resultado salvo.");
    await loadAll(false);
  } catch (error) {
    showToast(error.message, "error");
  }
}

async function clearResult(matchId) {
  try {
    const data = await api(`/api/admin/matches/${matchId}/clear-result`, { method: "POST" });
    showToast(data.message || "Resultado reaberto.");
    await loadAll(false);
  } catch (error) {
    showToast(error.message, "error");
  }
}

function currentStatus(match) {
  if (match.stored_status === "encerrado" || match.status === "encerrado") return "encerrado";
  if (new Date(match.start_at) <= getNow()) return "em andamento";
  return "agendado";
}

function isLocked(match) {
  return new Date(match.start_at) <= getNow();
}

function getNow() {
  return new Date(Date.now() + state.serverOffsetMs);
}

function formatDateTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatClockDateTime(value) {
  const date = value instanceof Date ? value : new Date(value);
  return new Intl.DateTimeFormat("pt-BR", {
    timeZone: "America/Sao_Paulo",
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function showToast(message, type = "success") {
  const area = $("#toast-area");
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "error" : ""}`;
  toast.textContent = message;
  area.appendChild(toast);
  setTimeout(() => toast.remove(), 4200);
}

function isEditing() {
  const active = document.activeElement;
  return active && ["INPUT", "SELECT"].includes(active.tagName);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
