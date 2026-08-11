/* ---------------------------------------------------------------------------
 * Fluxor — dashboard
 * JavaScript puro, sem framework e sem build: o arquivo que você lê é o que roda.
 * ------------------------------------------------------------------------- */

const REFRESH_MS = 15000;
const state = { statusFilter: "", runs: [], workflows: [], timer: null };

/* --- utilidades ---------------------------------------------------------- */

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) { /* resposta sem JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

function formatDuration(ms) {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60000);
  return `${minutes}m ${Math.round((ms % 60000) / 1000)}s`;
}

function formatRelative(iso) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "agora há pouco";
  if (diff < 3600) return `há ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `há ${Math.floor(diff / 3600)} h`;
  return `há ${Math.floor(diff / 86400)} d`;
}

function toast(message, kind = "ok") {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.className = `toast toast-${kind}`;
  element.hidden = false;
  clearTimeout(element._timer);
  element._timer = setTimeout(() => { element.hidden = true; }, 3600);
}

/* --- renderização -------------------------------------------------------- */

function renderHealth(health) {
  const pill = document.getElementById("health-pill");
  const degraded = health.status !== "ok";
  pill.textContent = degraded ? "atenção: YAML com erro" : `online · v${health.version}`;
  pill.className = `pill ${degraded ? "pill-bad" : "pill-ok"}`;
  pill.title = health.error || "";

  document.getElementById("stat-scheduled").textContent = health.scheduler
    ? `${health.scheduled_jobs} agendado(s)`
    : "agendador desligado";
}

function renderStats(stats) {
  document.getElementById("stat-total").textContent = stats.total_runs;
  document.getElementById("stat-total-hint").textContent =
    `${stats.by_status.failed || 0} falha(s) no período`;

  document.getElementById("stat-success").textContent = `${stats.success_rate}%`;
  const meter = document.getElementById("stat-meter");
  meter.style.width = `${stats.success_rate}%`;
  meter.style.background = stats.success_rate >= 90
    ? "var(--success)"
    : stats.success_rate >= 60 ? "var(--warning)" : "var(--danger)";

  document.getElementById("stat-duration").textContent = formatDuration(stats.avg_duration_ms);
  const slowest = stats.by_workflow[0];
  document.getElementById("stat-duration-hint").textContent = slowest
    ? `mais ativo: ${slowest.workflow}`
    : " ";

  renderChart(stats.by_day);
}

function renderChart(days) {
  const container = document.getElementById("chart");
  if (!days || !days.length) {
    container.innerHTML = '<p class="empty">sem dados no período</p>';
    return;
  }

  const W = 700, H = 170, padX = 12, padTop = 12, padBottom = 26;
  const usableH = H - padTop - padBottom;
  const slot = (W - padX * 2) / days.length;
  const barWidth = Math.min(30, slot * 0.6);
  const max = Math.max(1, ...days.map((day) => day.total));

  const gridLines = [0, 0.5, 1]
    .map((ratio) => {
      const y = padTop + usableH * (1 - ratio);
      return `<line class="grid-line" x1="${padX}" y1="${y}" x2="${W - padX}" y2="${y}" />`;
    })
    .join("");

  const bars = days.map((day, index) => {
    const x = padX + slot * index + (slot - barWidth) / 2;
    const segments = [
      { value: day.success, color: "var(--success)" },
      { value: day.partial, color: "var(--warning)" },
      { value: day.failed, color: "var(--danger)" },
    ];

    let cursor = padTop + usableH;
    const rects = segments
      .filter((segment) => segment.value > 0)
      .map((segment) => {
        const height = (segment.value / max) * usableH;
        cursor -= height;
        return `<rect class="bar" x="${x}" y="${cursor}" width="${barWidth}" height="${height}"
                 fill="${segment.color}" rx="2" />`;
      })
      .join("");

    const label = day.date.slice(8, 10) + "/" + day.date.slice(5, 7);
    const showLabel = days.length <= 10 || index % 2 === 0;
    const text = showLabel
      ? `<text class="axis-label" x="${x + barWidth / 2}" y="${H - 8}" text-anchor="middle">${label}</text>`
      : "";

    const empty = day.total === 0
      ? `<rect x="${x}" y="${padTop + usableH - 2}" width="${barWidth}" height="2"
              fill="var(--border)" rx="1" />`
      : "";

    return `<g><title>${label}: ${day.total} execução(ões) · ${day.success} ok · ${day.failed} falha(s)</title>
              ${empty}${rects}${text}</g>`;
  }).join("");

  container.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Execuções por dia">${gridLines}${bars}</svg>`;
}

function renderWorkflows(payload) {
  state.workflows = payload.items;
  document.getElementById("workflow-count").textContent = payload.total;
  document.getElementById("stat-workflows").textContent = payload.total;

  const container = document.getElementById("workflow-list");
  if (!payload.items.length) {
    container.innerHTML =
      '<p class="empty">nenhum workflow carregado — confira FLUXOR_WORKFLOWS_DIR</p>';
    return;
  }

  container.innerHTML = payload.items.map((workflow) => `
    <div class="row">
      <div class="row-main">
        <div class="row-title">${escapeHtml(workflow.name)}</div>
        <div class="row-sub">${escapeHtml(workflow.description || workflow.file)}</div>
      </div>
      <span class="tag ${workflow.cron ? "" : "tag-muted"}">
        ${escapeHtml(workflow.cron || workflow.trigger)}
      </span>
      <span class="row-meta">${workflow.steps} passos</span>
      <button class="btn btn-sm" data-run="${escapeHtml(workflow.name)}">rodar</button>
    </div>
  `).join("");

  container.querySelectorAll("[data-run]").forEach((button) => {
    button.addEventListener("click", () => runWorkflow(button.dataset.run, button));
  });
}

function renderRuns(payload) {
  state.runs = payload.items;
  const container = document.getElementById("run-list");

  if (!payload.items.length) {
    container.innerHTML = '<p class="empty">nenhuma execução ainda — clique em "rodar"</p>';
    return;
  }

  container.innerHTML = payload.items.map((run) => `
    <div class="row row-clickable" data-run-id="${escapeHtml(run.id)}">
      <span class="status-dot status-${escapeHtml(run.status)}"></span>
      <div class="row-main">
        <div class="row-title">${escapeHtml(run.workflow)}</div>
        <div class="row-sub">
          ${formatRelative(run.started_at)} · ${escapeHtml(run.trigger)}
          ${run.steps_failed ? ` · <span style="color:var(--danger)">${run.steps_failed} falha(s)</span>` : ""}
        </div>
      </div>
      <span class="row-meta">${formatDuration(run.duration_ms)}</span>
      <span class="row-meta">${run.id.slice(0, 8)}</span>
    </div>
  `).join("");

  container.querySelectorAll("[data-run-id]").forEach((row) => {
    row.addEventListener("click", () => openDrawer(row.dataset.runId));
  });
}

/* --- ações --------------------------------------------------------------- */

async function runWorkflow(name, button) {
  button.disabled = true;
  button.textContent = "rodando…";
  try {
    const record = await api(`/workflows/${encodeURIComponent(name)}/run`, {
      method: "POST",
      body: JSON.stringify({ vars: {}, dry_run: false }),
    });
    const ok = record.status === "success";
    toast(
      ok
        ? `${name}: sucesso em ${formatDuration(record.duration_ms)}`
        : `${name}: ${record.error || record.status}`,
      ok ? "ok" : "bad",
    );
    await refresh();
    openDrawer(record.id);
  } catch (error) {
    toast(`falha ao executar: ${error.message}`, "bad");
  } finally {
    button.disabled = false;
    button.textContent = "rodar";
  }
}

async function openDrawer(runId) {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  const body = document.getElementById("drawer-body");

  drawer.hidden = false;
  backdrop.hidden = false;
  body.innerHTML = '<p class="empty">carregando…</p>';

  try {
    const run = await api(`/runs/${encodeURIComponent(runId)}`);
    document.getElementById("drawer-title").textContent = run.workflow;
    document.getElementById("drawer-sub").textContent =
      `${run.id} · ${run.status} · ${formatDuration(run.duration_ms)} · ${run.trigger}`;

    const errorBox = run.error ? `<div class="error-box">${escapeHtml(run.error)}</div>` : "";

    const steps = run.steps.map((step) => {
      const output = step.output !== null && step.output !== undefined
        ? `<pre class="output">${escapeHtml(JSON.stringify(step.output, null, 2))}</pre>`
        : "";
      const failure = step.error ? `<div class="error-box">${escapeHtml(step.error)}</div>` : "";
      const skipped = step.skipped_reason
        ? `<div class="row-sub" style="margin-top:6px">pulado — ${escapeHtml(step.skipped_reason)}</div>`
        : "";
      const attempts = step.attempts > 1 ? ` · ${step.attempts} tentativas` : "";

      return `
        <div class="step">
          <div class="step-head">
            <span class="status-dot status-${escapeHtml(step.status)}"></span>
            <span class="step-name">${escapeHtml(step.step_id)}</span>
            <span class="tag tag-muted">${escapeHtml(step.action)}</span>
            <span class="step-meta">${formatDuration(step.duration_ms)}${attempts}</span>
          </div>
          ${skipped}${failure}${output}
        </div>`;
    }).join("");

    body.innerHTML = errorBox + (steps || '<p class="empty">nenhum passo registrado</p>');
  } catch (error) {
    body.innerHTML = `<div class="error-box">${escapeHtml(error.message)}</div>`;
  }
}

function closeDrawer() {
  document.getElementById("drawer").hidden = true;
  document.getElementById("drawer-backdrop").hidden = true;
}

/* --- ciclo --------------------------------------------------------------- */

async function refresh() {
  try {
    const query = state.statusFilter ? `?limit=25&status=${state.statusFilter}` : "?limit=25";
    const [health, stats, workflows, runs] = await Promise.all([
      api("/health"),
      api("/stats?days=14"),
      api("/workflows"),
      api(`/runs${query}`),
    ]);

    renderHealth(health);
    renderStats(stats);
    renderWorkflows(workflows);
    renderRuns(runs);

    document.getElementById("last-update").textContent =
      `atualizado ${new Date().toLocaleTimeString("pt-BR")}`;
  } catch (error) {
    const pill = document.getElementById("health-pill");
    pill.textContent = "offline";
    pill.className = "pill pill-bad";
    console.error("falha ao atualizar", error);
  }
}

function bindEvents() {
  document.getElementById("refresh-btn").addEventListener("click", refresh);
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });

  document.getElementById("reload-btn").addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      const result = await api("/workflows/reload", { method: "POST" });
      toast(result.error ? result.error : `${result.loaded} workflow(s) recarregado(s)`,
            result.error ? "bad" : "ok");
      await refresh();
    } catch (error) {
      toast(error.message, "bad");
    } finally {
      event.target.disabled = false;
    }
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach((other) => other.classList.remove("chip-active"));
      chip.classList.add("chip-active");
      state.statusFilter = chip.dataset.status;
      refresh();
    });
  });

  // Pausa o polling quando a aba não está visível — não faz sentido consultar
  // o servidor a cada 15s para uma página que ninguém está olhando.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearInterval(state.timer);
    } else {
      refresh();
      state.timer = setInterval(refresh, REFRESH_MS);
    }
  });
}

bindEvents();
refresh();
state.timer = setInterval(refresh, REFRESH_MS);
