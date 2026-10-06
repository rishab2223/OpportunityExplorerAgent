// The run settings popup: Start run opens it, and the run starts with what
// it says. Every control carries data-field="a.b" naming its place in the
// settings object the server takes (src/web/run_options.py), so reading and
// writing the form is one loop rather than forty lines of ids.
//
// A control that is hidden (its section is switched off) is not read: its
// loaded value is sent back unchanged, so a half-typed number in a hidden
// box can never fail the run on the server. The server still checks
// everything, and its answer is shown in the popup, which stays open.

const RS_LABELS = {
  "scrape.posted_within": { "24h": "24 hours", "3d": "3 days", "7d": "7 days" },
  "experience.mode": { review: "Hold back for review", drop: "Drop them" },
  enrich_provider: { claude: "Claude", openai: "OpenAI" },
  interview_prep: { on_demand: "On demand (faster)", run: "During the run" },
};

let rsOptions = null;   // the last /api/run-options answer
let rsValues = null;    // what the form last showed, per field
let rsLastEstimate = "";

function rsGet(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function rsSet(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  let node = obj;
  keys.forEach((k) => { node = node[k] = node[k] || {}; });
  node[last] = value;
}

const rsForm = () => $("runmodal");
const rsControls = () => rsForm().querySelectorAll("[data-field]");
const rsHidden = (node) => Boolean(node.closest("[hidden]"));

// ------------------------------------------------------------------ build

function rsBuildSources() {
  const box = $("rssources");
  box.replaceChildren();
  const { jobs_min: min, jobs_max: max } = rsOptions.choices;
  rsOptions.choices.sources.forEach(({ id, label }) => {
    const toggle = el("input", { type: "checkbox", "data-source": id, "aria-label": `Scrape ${label}` });
    const slider = el("input", { type: "range", min, max, step: 1, "data-cap": id,
                                 "aria-label": `${label}: jobs to fetch, slider` });
    const number = el("input", { type: "number", min, max, step: 1, "data-cap": id,
                                 class: "rs-num", "aria-label": `${label}: jobs to fetch` });
    box.append(el("div", { class: "rs-source", "data-source-card": id },
      el("label", { class: "switch-row rs-source-name" },
        el("span", { class: "switch" }, toggle, el("span")),
        el("b", { text: label })),
      el("div", { class: "rs-range" }, slider, number),
      el("span", { class: "rs-hint", text: `${min}–${max} jobs` })));
  });
}

function rsBuildChoices() {
  const seg = (path, ids) => {
    const holder = rsForm().querySelector(`.rs-seg[data-field="${path}"]`);
    holder.replaceChildren();
    ids.forEach((id) => {
      const text = (RS_LABELS[path] || {})[id] || id;
      holder.append(el("button", { type: "button", role: "radio", "data-value": id,
                                   "aria-checked": "false", tabindex: "-1", text }));
    });
  };
  seg("scrape.posted_within", rsOptions.choices.posted_within);
  seg("experience.mode", ["review", "drop"]);
  seg("enrich_provider", ["claude", "openai"]);
  seg("claude.effort", rsOptions.choices.efforts);
  seg("interview_prep", ["on_demand", "run"]);

  rsForm().querySelectorAll("select[data-models]").forEach((select) => {
    select.replaceChildren();
    (rsOptions.choices.models[select.dataset.models] || []).forEach((m) => {
      select.append(el("option", { value: m.id, text: m.note ? `${m.id} — ${m.note}` : m.id }));
    });
  });
  // Every numeric limit comes from the server's own config models.
  rsForm().querySelectorAll("input[data-bounds]").forEach((input) => {
    const [lo, hi] = rsOptions.choices[input.dataset.bounds];
    if (lo != null) input.min = lo;
    if (hi != null) input.max = hi;
    input.title = `${lo ?? ""}–${hi ?? ""}`;
  });
}

// ------------------------------------------------------------------ values

function rsShow(values) {
  rsValues = JSON.parse(JSON.stringify(values));
  rsControls().forEach((node) => {
    const value = rsGet(values, node.dataset.field);
    if (node.classList.contains("rs-seg")) {
      const options = [...node.querySelectorAll("[role=radio]")];
      options.forEach((b) => {
        const on = b.dataset.value === String(value);
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
      });
      if (!options.some((b) => b.tabIndex === 0) && options[0]) options[0].tabIndex = 0;
    } else if (node.type === "checkbox") {
      node.checked = Boolean(value);
    } else if (node.tagName === "SELECT") {
      // A model typed into settings.yaml that the list does not offer stays
      // selectable rather than silently becoming the first option.
      if (value && ![...node.options].some((o) => o.value === value)) {
        node.append(el("option", { value, text: `${value} (from settings.yaml)` }));
      }
      node.value = value ?? "";
    } else if (node.dataset.kind === "list") {
      node.value = (value || []).join(", ");
    } else {
      node.value = value ?? "";
    }
  });
  const on = new Set(values.scrape.sources);
  rsForm().querySelectorAll("[data-source]").forEach((t) => { t.checked = on.has(t.dataset.source); });
  rsForm().querySelectorAll("[data-cap]").forEach((i) => { i.value = values.scrape.max_jobs[i.dataset.cap]; });
  rsRefresh();
}

function rsRead() {
  const values = JSON.parse(JSON.stringify(rsValues));
  rsControls().forEach((node) => {
    if (rsHidden(node)) return;           // keeps the loaded value
    let value;
    if (node.classList.contains("rs-seg")) {
      value = node.querySelector('[aria-checked="true"]')?.dataset.value;
    } else if (node.type === "checkbox") {
      value = node.checked;
    } else if (node.dataset.kind === "list") {
      value = node.value.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (node.dataset.kind === "int") {
      value = parseInt(node.value, 10);
    } else if (node.dataset.kind === "float") {
      value = parseFloat(node.value);
    } else {
      value = node.value.trim();
    }
    rsSet(values, node.dataset.field, value);
  });
  values.scrape.sources = [...rsForm().querySelectorAll("[data-source]")]
    .filter((t) => t.checked).map((t) => t.dataset.source);
  rsForm().querySelectorAll("input[type=number][data-cap]").forEach((i) => {
    // A switched-off source keeps its loaded cap, whatever its box says.
    if (values.scrape.sources.includes(i.dataset.cap)) {
      values.scrape.max_jobs[i.dataset.cap] = parseInt(i.value, 10);
    }
  });
  return values;
}

// What is wrong with the form, in words, or "" when it can start. Only the
// visible controls count; the server has the last word on all of them.
function rsProblem(values) {
  const c = rsOptions.choices;
  const within = (n, [lo, hi]) => Number.isFinite(n) && (lo == null || n >= lo) && (hi == null || n <= hi);
  const range = ([lo, hi]) => `${lo ?? ""}–${hi ?? ""}`;
  if (!values.scrape.sources.length) return "Turn on at least one job source.";
  for (const id of values.scrape.sources) {
    const n = values.scrape.max_jobs[id];
    if (!Number.isInteger(n) || n < c.jobs_min || n > c.jobs_max) {
      const label = c.sources.find((s) => s.id === id)?.label || id;
      return `${label}: jobs to fetch must be ${c.jobs_min}–${c.jobs_max}.`;
    }
  }
  const checks = [["openai.score_concurrency", "Scoring calls at once", c.score_concurrency],
                  ["openai.enrich_concurrency", "Tailoring calls at once", c.enrich_concurrency]];
  if (values.compile_pdf) checks.push(["pdf_concurrency", "PDFs compiled at once", c.pdf_concurrency]);
  if (values.experience.enabled) {
    checks.push(["experience.tolerance_years", "Years over yours", c.tolerance_years]);
    if (values.experience.mode === "review") {
      checks.push(["experience.review_min_score", "Held-back score", c.review_min_score],
                  ["experience.review_max", "Held-back jobs per run", c.review_max]);
    }
  }
  for (const [path, name, bounds] of checks) {
    if (!within(rsGet(values, path), bounds)) return `${name} must be ${range(bounds)}.`;
  }
  if (!values.resume.local_path) return "Name the base resume file.";
  return "";
}

function rsMinutes(seconds) {
  if (!seconds) return "";
  return seconds < 90 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
}

function rsRefresh() {
  // Conditional rows first, so rsRead sees what is hidden.
  const shown = rsRead();
  rsForm().querySelectorAll("[data-show-if]").forEach((node) => {
    const [path, want] = node.dataset.showIf.split("=");
    const have = rsGet(shown, path);
    node.hidden = want === undefined ? !have : String(have) !== want;
  });
  const values = rsRead();
  rsForm().querySelectorAll("[data-source-card]").forEach((card) => {
    const on = values.scrape.sources.includes(card.dataset.sourceCard);
    card.classList.toggle("off", !on);
    card.querySelectorAll("[data-cap]").forEach((i) => { i.disabled = !on; });
  });
  rsForm().querySelector("output.rs-out").textContent = values.min_score;
  $("rsminhint").textContent =
    `Jobs scoring ${values.min_score} or more out of 10 get a tailored resume.` +
    (values.min_score <= 6 ? " A low bar tailors many more jobs, and tailoring is the slow step." : "");

  const total = values.scrape.sources.reduce((n, id) => n + (values.scrape.max_jobs[id] || 0), 0);
  const last = rsOptions.last_run || {};
  let lastLine = "";
  if (last.matched) {
    const t = last.timings || {};
    const took = Object.values(t).reduce((a, b) => a + b, 0);
    lastLine = `Last run: ${last.scraped} fetched, ${last.matched} tailored`;
    if (took) lastLine += `, ${rsMinutes(took)} in all`;
    if (t.enrich) lastLine += ` (tailoring ${rsMinutes(t.enrich)})`;
  }
  // Rebuilt only when it changes: it is a live region, and every keystroke
  // would otherwise be read out again.
  const estimate = `${total}|${lastLine}`;
  if (estimate !== rsLastEstimate) {
    rsLastEstimate = estimate;
    $("rsestimate").replaceChildren(
      el("span", {}, "Up to ", el("b", { text: String(total) }), " jobs fetched and scored"),
      lastLine ? el("span", { class: "muted", text: lastLine }) : null);
  }
  rsShowProblem(rsProblem(values));
}

function rsShowProblem(problem) {
  $("rserror").textContent = problem;
  $("rserror").hidden = !problem;
  $("rsstart").disabled = Boolean(problem);
}

function rsShowSaved() {
  $("rssaved").hidden = !rsOptions.saved;
}

// ------------------------------------------------------------------ open / close

async function openRunSettings() {
  try {
    rsOptions = await getJSON("/api/run-options");
  } catch (err) {
    $("status").textContent = `could not load run settings: ${err.message}`;
    return;
  }
  rsBuildSources();
  rsBuildChoices();
  rsLastEstimate = "";
  rsShow(rsOptions.values);
  rsShowSaved();
  $("rsremember").checked = false;
  $("runback").hidden = false;
  document.querySelector("main").inert = true;   // keyboard focus stays in the dialog
  rsForm().querySelector("[data-source]").focus();
}

function closeRunSettings() {
  $("runback").hidden = true;
  document.querySelector("main").inert = false;
  $("start").focus();
}

async function submitRunSettings(ev) {
  ev.preventDefault();
  const values = rsRead();
  const problem = rsProblem(values);
  if (problem) {
    rsShowProblem(problem);
    return;
  }
  $("rsstart").disabled = true;
  try {
    // The popup stays until the server has taken the run, so a refusal is
    // shown here with the form still filled in.
    const stamp = await startRun({ settings: values, remember: $("rsremember").checked });
    if (stamp) closeRunSettings();
    else rsShowProblem($("status").textContent);
  } finally {
    $("rsstart").disabled = Boolean($("rserror").textContent) && !$("rserror").hidden;
  }
}

async function forgetRunDefaults() {
  try {
    rsOptions = await sendJSON("DELETE", "/api/run-options");
    rsShow(rsOptions.values);
    rsShowSaved();
  } catch (err) {
    rsShowProblem(`Could not forget the saved defaults: ${err.message}`);
  }
}

// ------------------------------------------------------------------ wiring

rsForm().addEventListener("submit", submitRunSettings);
rsForm().addEventListener("input", (ev) => {
  // Slider and number box for one source move together.
  const cap = ev.target.dataset.cap;
  if (cap) {
    rsForm().querySelectorAll(`[data-cap="${cap}"]`).forEach((i) => {
      if (i !== ev.target) i.value = ev.target.value;
    });
  }
  rsRefresh();
});
rsForm().addEventListener("click", (ev) => {
  const option = ev.target.closest(".rs-seg [role=radio]");
  if (option) {
    option.parentElement.querySelectorAll("[role=radio]").forEach((b) => {
      const on = b === option;
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    rsRefresh();
    return;
  }
  const action = ev.target.closest("[data-rs]")?.dataset.rs;
  if (action === "cancel") closeRunSettings();
  if (action === "reset") rsShow(rsOptions.defaults);
  if (action === "forget") forgetRunDefaults();
});
rsForm().addEventListener("keydown", (ev) => {
  // Enter in a text box must not start a run; Enter on Start does.
  if (ev.key === "Enter" && ev.target.matches("input:not([type=checkbox]):not([type=range])")) {
    ev.preventDefault();
    return;
  }
  // Arrow keys move along a segmented choice, as in a radio group.
  const option = ev.target.closest(".rs-seg [role=radio]");
  if (!option || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(ev.key)) return;
  const all = [...option.parentElement.querySelectorAll("[role=radio]")];
  const i = all.indexOf(option);
  const next = ev.key === "Home" ? all[0] : ev.key === "End" ? all[all.length - 1]
    : all[(i + (["ArrowRight", "ArrowDown"].includes(ev.key) ? 1 : all.length - 1)) % all.length];
  next.click();
  next.focus();
  ev.preventDefault();
});
$("runback").addEventListener("click", (ev) => {
  if (ev.target === $("runback")) closeRunSettings();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("runback").hidden) closeRunSettings();
});
