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
  "experience.mode": { review: "Keep aside for me to look at", drop: "Leave them out" },
  enrich_provider: { claude: "Claude", openai: "OpenAI" },
  "claude.effort": { low: "Low", medium: "Medium", high: "High", xhigh: "Very high", max: "Max" },
  interview_prep: { on_demand: "When I open a job (faster)", run: "For every job, during the run" },
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
    // The words this site is searched for, sent to Apify as its own query
    // (src/scrape/search_terms.py). Indeed matches them in the job title.
    const terms = el("input", { type: "text", class: "rs-text", "data-field": `scrape.search_terms.${id}`,
                                "data-kind": "list", "data-terms": id, "aria-label": `${label}: search for`,
                                placeholder: "e.g. Software Engineer, Backend Engineer, NodeJS" });
    box.append(el("div", { class: "rs-source", "data-source-card": id },
      el("label", { class: "switch-row rs-source-name" },
        el("span", { class: "switch" }, toggle, el("span")),
        el("b", { text: label })),
      el("div", { class: "rs-range" }, slider, number),
      el("span", { class: "rs-hint", text: `${min}–${max} jobs` }),
      el("label", { class: "rs-terms" },
        el("span", { class: "rs-terms-label", text: "Search for" }), terms,
        el("span", { class: "rs-help", text: id === "indeed"
          ? "Comma separated. Indeed looks for each in the job title."
          : "Comma separated. LinkedIn looks for any of them in the posting." }))));
  });
}

// The (i) beside the score bar: how the 100 marks are given, part by part,
// as the server's scorer gives them.
function rsBuildScoreTip() {
  const parts = rsOptions.choices.score_parts || [];
  const name = (p) => p.name.charAt(0).toUpperCase() + p.name.slice(1);
  $("rsscoretip").replaceChildren(
    el("b", { text: "How a job's score out of 100 is made" }),
    el("div", { class: "tipparts" },
      ...parts.flatMap((p) => [el("span", { class: "tipname", "data-part": p.name, text: name(p) }),
                               el("span", { text: p.about }),
                               el("span", { class: "tippts", text: String(p.points) })]),
      el("span", { class: "tipname tiptotal", text: "Total" }), el("span", { class: "tiptotal" }),
      el("span", { class: "tippts tiptotal", text: String(parts.reduce((n, p) => n + p.points, 0)) })),
    el("div", { text: "The AI marks each part on its own; the score is their sum. A job a few points " +
      "under the bar gets a second look from a stronger model, and is kept if either look reaches it." }));
}

// Puts an (i)'s explanation beside it on screen - below when there is room,
// otherwise above - and inside the window either way.
function placeTip(holder) {
  const tip = holder.querySelector(".tipbox");
  const anchor = holder.querySelector("button").getBoundingClientRect();
  const gap = 8;
  const width = tip.offsetWidth, height = tip.offsetHeight;
  const left = Math.max(gap, Math.min(anchor.right - width, window.innerWidth - width - gap));
  const below = anchor.bottom + gap;
  const top = below + height <= window.innerHeight - gap ? below : Math.max(gap, anchor.top - gap - height);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

document.querySelectorAll(".infotip").forEach((holder) => {
  const place = () => requestAnimationFrame(() => placeTip(holder));
  holder.addEventListener("mouseenter", place);
  holder.addEventListener("focusin", place);
});

function rsBuildChoices() {
  rsBuildScoreTip();
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

// What is wrong with the form, in words, and the field it is about - or
// ["", ""] when it can start. Only the visible controls count; the server
// has the last word on all of them.
function rsProblem(values) {
  const c = rsOptions.choices;
  const within = (n, [lo, hi]) => Number.isFinite(n) && (lo == null || n >= lo) && (hi == null || n <= hi);
  const range = ([lo, hi]) => `${lo ?? ""} to ${hi ?? ""}`;
  if (!values.scrape.sources.length) return ["Turn on at least one job source.", ""];
  for (const id of values.scrape.sources) {
    const label = c.sources.find((s) => s.id === id)?.label || id;
    if (!(values.scrape.search_terms?.[id] || []).length) {
      return [`${label}: give it at least one search term.`, `scrape.search_terms.${id}`];
    }
  }
  for (const id of values.scrape.sources) {
    const n = values.scrape.max_jobs[id];
    if (!Number.isInteger(n) || n < c.jobs_min || n > c.jobs_max) {
      const label = c.sources.find((s) => s.id === id)?.label || id;
      return [`${label}: the number of jobs must be ${c.jobs_min}–${c.jobs_max}.`, ""];
    }
  }
  const checks = [["openai.score_concurrency", "Jobs scored at once", c.score_concurrency],
                  ["openai.enrich_concurrency", "Resumes tailored at once", c.enrich_concurrency]];
  if (values.compile_pdf) checks.push(["pdf_concurrency", "PDFs made at once", c.pdf_concurrency]);
  if (values.experience.enabled) {
    checks.push(["experience.tolerance_years", "Years either side of yours", c.tolerance_years]);
    if (values.experience.mode === "review") {
      checks.push(["experience.review_min_score", "The score to show a kept-aside job", c.review_min_score],
                  ["experience.review_max", "Kept-aside jobs per run", c.review_max]);
    }
  }
  for (const [path, name, bounds] of checks) {
    if (!within(rsGet(values, path), bounds)) return [`${name} must be ${range(bounds)}.`, path];
  }
  if (!values.resume.local_path) return ["Name your resume file (under Advanced).", "resume.local_path"];
  return ["", ""];
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
    card.querySelectorAll("[data-cap], [data-terms]").forEach((i) => { i.disabled = !on; });
  });
  rsForm().querySelector("output.rs-out").textContent = `${values.min_score}+`;
  rsExplain(values);

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
  rsShowProblem(...rsProblem(values));
}

// The plain-words line under each choice, rewritten as the choice changes,
// so a first-time user can read what a setting will actually do.
function rsExplain(values) {
  const score = values.min_score;
  let min = `The AI scores every job out of 100 for how well it fits your resume: skills, ` +
    `experience, the kind of role, stack, company and location, each marked on its own. ` +
    `Jobs scoring ${score} or more get a tailored resume and appear in your shortlist; ` +
    `ones just below get a second look from a stronger model.`;
  if (score <= 55) min += " A low bar means many more jobs and a much longer run.";
  else if (score >= 85) min += " A high bar keeps only near-perfect fits; you may get very few.";
  else min += " 70 is a good place to start.";
  $("rsminhint").textContent = min;

  const years = rsOptions.your_years;
  const e = values.experience;
  let exp = "Some postings ask for far more, or far fewer, years than you have.";
  if (years == null) {
    exp += " Add your total years of experience in Profile (top right) for this to work.";
  } else if (e.enabled) {
    const slack = Number.isFinite(e.tolerance_years) ? e.tolerance_years : 0;
    exp += ` You have ${years} years (from your Profile), so jobs asking for more than ${years + slack} ` +
      `years, or for a range that tops out below ${years - slack}, count as outside your range. ` +
      "Postings that do not say are always kept.";
  } else {
    exp += ` Turned off: every job is scored whatever it asks for (you have ${years} years, from your Profile).`;
  }
  $("rsexphint").textContent = exp;
  $("rsmodehint").textContent = e.mode === "drop"
    ? "Left out: they are never scored and you never see them."
    : "Kept aside: they are scored, and the strongest show in the Held back tab instead of your shortlist.";

  $("rsprephint").textContent = values.interview_prep === "run"
    ? "Likely interview questions and talking points are written for every job that gets a " +
      "tailored resume. This makes the run much slower."
    : "Likely interview questions and talking points are written only for the jobs you want them " +
      "for, which keeps the run fast. After the run, click a job's row in the Shortlist, press " +
      "Job info (right end of the bar above the table), then Write interview prep.";
}

function rsShowProblem(problem, field = "") {
  $("rserror").textContent = problem;
  $("rserror").hidden = !problem;
  $("rsstart").disabled = Boolean(problem);
  // A problem inside the closed Advanced part opens it, so it can be fixed.
  const where = field && rsForm().querySelector(`[data-field="${field}"]`);
  const fold = where && where.closest("details");
  if (problem && fold) fold.open = true;
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
  const [problem, field] = rsProblem(values);
  if (problem) {
    rsShowProblem(problem, field);
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
