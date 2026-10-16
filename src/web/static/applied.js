// The Applied page (#40, #42): every application sent, where it stands, and
// the messages around it - referral, outreach, follow-up, thank-you - with a
// small chat that edits them. Drafts only: you send them, then Mark sent.
//
// Built with DOM calls (app.js's el), never innerHTML: company names, notes
// and drafts are text from outside.

const APL_STAGE_NAMES = {
  applied: "Applied", screening: "Screening", interview: "Interview", assessment: "Assessment",
  offer: "Offer", accepted: "Accepted", rejected: "Rejected", withdrawn: "Withdrawn",
};
const APL_KIND_NAMES = { referral: "Referral", outreach: "Outreach", follow_up: "Follow-up", thank_you: "Thank you" };
const APL_LAYOUT_KEY = "oea.applied.layout";
const APL_RUN_KEY = "oea.applied.run";
// The run an application came from: "" is every run, NO_RUN one recorded
// without a run (marked by hand outside a run's table).
const APL_ALL = "";
const APL_NO_RUN = "__none__";

let aplData = { applications: [], summary: {}, stages: [], ends: [], kinds: [] };
let aplOpen = "";            // job_id in the side panel
let aplKind = "outreach";    // message tab in the side panel
let aplChat = [];            // this message's chat, this visit
let aplSort = { key: "applied_at", desc: true };

let aplDueOnly = false;

// Which run is on screen. Unchosen, the newest run with applications - all of
// them at once was too much to take in (Oct 2026); a choice is remembered.
function aplRunKey(app) {
  return app.stamp || APL_NO_RUN;
}

function aplChosenRun() {
  let saved = null;
  try { saved = localStorage.getItem(APL_RUN_KEY); } catch { /* per-viewer nicety only */ }
  const keys = new Set(aplData.applications.map(aplRunKey));
  if (saved !== null && (saved === APL_ALL || keys.has(saved))) return saved;
  const newest = [...keys].filter((k) => k !== APL_NO_RUN).sort().reverse()[0];
  return newest || (keys.size ? [...keys][0] : APL_ALL);
}

function aplSetRun(value) {
  try { localStorage.setItem(APL_RUN_KEY, value); } catch { /* per-viewer nicety only */ }
  aplRender();
}

// The chosen run's applications.
function aplVisibleRun() {
  const run = aplChosenRun();
  return aplData.applications.filter((a) => run === APL_ALL || aplRunKey(a) === run);
}

// What the page shows: the chosen run, and only those waiting on a
// follow-up when that box is ticked.
function aplVisible() {
  return aplVisibleRun().filter((a) => !aplDueOnly || a.follow_up_due);
}

function aplSummary(apps) {
  const counts = {};
  [...(aplData.stages || []), ...(aplData.ends || [])].forEach((s) => (counts[s] = 0));
  apps.forEach((a) => (counts[a.stage] = (counts[a.stage] || 0) + 1));
  return {
    counts, total: apps.length,
    replied: apps.filter((a) => a.stage !== "applied" && a.stage !== "withdrawn").length,
    follow_ups_due: apps.filter((a) => a.follow_up_due).length,
  };
}

function aplRenderRunPicker() {
  const select = $("aplrun");
  const counts = {};
  aplData.applications.forEach((a) => (counts[aplRunKey(a)] = (counts[aplRunKey(a)] || 0) + 1));
  const stamps = Object.keys(counts).filter((k) => k !== APL_NO_RUN).sort().reverse();
  const plural = (n) => `${n} application${n === 1 ? "" : "s"}`;
  select.replaceChildren(...[
    el("option", { value: APL_ALL, text: `All runs (${aplData.applications.length})` }),
    ...stamps.map((stamp) => el("option", { value: stamp, title: stamp,
      text: `${runLabel(stamp)} · ${plural(counts[stamp])}` })),
    counts[APL_NO_RUN] ? el("option", { value: APL_NO_RUN, text: `No run · ${plural(counts[APL_NO_RUN])}` }) : null,
  ].filter(Boolean));
  select.value = aplChosenRun();
}

function aplLayout() {
  try { return localStorage.getItem(APL_LAYOUT_KEY) || "board"; } catch { return "board"; }
}

function aplSetLayout(layout) {
  try { localStorage.setItem(APL_LAYOUT_KEY, layout); } catch { /* per-viewer nicety only */ }
  aplRender();
}

function aplDate(iso) {
  if (!iso) return "-";
  const when = new Date(iso);
  return isNaN(when) ? "-" : when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function aplScore(app) {
  if (app.relevance == null) return "-";
  return Number(app.score_scale || 10) === 100 ? String(app.relevance) : `${app.relevance}/10`;
}

function aplFollowText(app) {
  if (app.follow_up_due) return app.follow_up_at ? `Due ${aplDate(app.follow_up_at)}` : "Follow up?";
  return app.follow_up_at ? aplDate(app.follow_up_at) : "-";
}

let aplWarmed = false;

async function loadApplied() {
  if (!aplWarmed) {
    aplWarmed = true;
    // The message model takes ~20 s to start; start it while you look.
    fetch("/api/applied/warm", { method: "POST" }).catch(() => {});
  }
  try {
    aplData = await getJSON("/api/applied");
  } catch (err) {
    $("aplinfo").textContent = `Could not load: ${err.message}`;
    return;
  }
  aplRender();
  if (aplOpen) aplOpenPanel(aplOpen, false);
}

function aplRender() {
  const layout = aplLayout();
  $("apllayoutboard").setAttribute("aria-pressed", String(layout === "board"));
  $("apllayouttable").setAttribute("aria-pressed", String(layout === "table"));
  $("aplboardview").hidden = layout !== "board";
  $("apltableview").hidden = layout !== "table";
  aplRenderRunPicker();
  $("apldue").checked = aplDueOnly;
  // The tab counts every application; everything below counts what is shown.
  const everything = (aplData.summary || {}).total || 0;
  $("nav-applied").textContent = everything ? `Applied (${everything})` : "Applied";
  const inRun = aplVisibleRun();
  const s = aplSummary(aplVisible());
  const counts = s.counts || {};
  const due = inRun.filter((a) => a.follow_up_due).length;
  $("aplinfo").textContent = !everything ? "Nothing marked applied yet."
    : aplDueOnly ? `${s.total} follow-up(s) due` + (inRun.length !== s.total ? ` of ${inRun.length}` : "")
    : `${s.total} application(s)` + (due ? ` · ${due} follow-up(s) due` : "");
  $("aplstages").replaceChildren(...(aplData.stages || []).map((stage) => el("div",
    { class: "aplstage" + (counts[stage] ? " on" : "") },
    el("div", { class: "aplbar" }),
    el("div", { class: "aplname", text: APL_STAGE_NAMES[stage] }),
    el("div", { class: "aplcount", text: counts[stage] ? String(counts[stage]) : "—" }))));
  const rate = s.total ? Math.round((100 * (s.replied || 0)) / s.total) : 0;
  $("aplends").textContent = `Rejected: ${counts.rejected || 0} · Withdrawn: ${counts.withdrawn || 0}` +
    (s.total ? ` · Moved past Applied: ${s.replied || 0} of ${s.total} (${rate}%)` : "");
  if (layout === "board") aplRenderBoard(); else aplRenderTable();
}

function aplCard(app) {
  const card = el("div", { class: "aplcard", tabindex: "0", "data-job-id": app.job_id },
    el("div", { class: "aplco", text: app.company || "?" }),
    el("div", { class: "aplti", text: app.title || "" }),
    el("div", { class: "aplmeta" },
      el("span", { class: "aplchip score", text: aplScore(app), title: "Score when it was found" }),
      el("span", { class: "aplchip", text: app.source || "-" }),
      el("span", { class: "aplchip", text: `${app.days_since_applied ?? "?"}d`, title: `Applied ${aplDate(app.applied_at)}` }),
      app.follow_up_due || app.follow_up_at
        ? el("span", { class: "aplchip" + (app.follow_up_due ? " due" : ""), text: aplFollowText(app) }) : null));
  card.addEventListener("click", () => aplOpenPanel(app.job_id));
  card.addEventListener("keydown", (ev) => { if (ev.key === "Enter") aplOpenPanel(app.job_id); });
  return card;
}

function aplEmptyText() {
  if (!aplData.applications.length) return "Nothing marked applied yet.";
  return aplDueOnly ? "No follow-ups due here. Untick 'Follow-up due only' to see the rest."
    : "No applications from this run.";
}

function aplRenderBoard() {
  const columns = [...(aplData.stages || []), "rejected"];
  const shown = aplVisible();
  $("aplboardempty").hidden = shown.length > 0;
  $("aplboardempty").textContent = aplEmptyText();
  $("aplboard").replaceChildren(...columns.map((stage) => {
    const apps = shown.filter((a) => a.stage === stage);
    return el("div", { class: "aplcol" },
      el("h3", {}, el("span", { text: APL_STAGE_NAMES[stage] }), el("span", { text: String(apps.length) })),
      ...apps.map(aplCard));
  }));
}

function aplSortValue(app, key) {
  if (key === "score") return app.relevance == null ? -1 : Number(app.relevance) * (100 / Number(app.score_scale || 10));
  if (key === "stage") return [...aplData.stages, ...aplData.ends].indexOf(app.stage);
  if (key === "follow_up") return app.follow_up_due ? 0 : app.follow_up_at ? 1 : 2;
  const v = app[key];
  return typeof v === "number" ? v : String(v || "").toLowerCase();
}

function aplRenderTable() {
  const stageSel = $("aplstagefilter");
  const sourceSel = $("aplsourcefilter");
  if (!stageSel.options.length) {
    stageSel.append(el("option", { value: "", text: "All stages" }),
      ...[...aplData.stages, ...aplData.ends].map((s) => el("option", { value: s, text: APL_STAGE_NAMES[s] })));
  }
  const shown = aplVisible();
  const sources = [...new Set(shown.map((a) => a.source || "-"))].sort();
  const keep = sourceSel.value;
  sourceSel.replaceChildren(el("option", { value: "", text: "All sources" }),
    ...sources.map((s) => el("option", { value: s, text: s })));
  sourceSel.value = sources.includes(keep) ? keep : "";
  const query = $("aplsearch").value.trim().toLowerCase();
  const rows = shown
    .filter((a) => !stageSel.value || a.stage === stageSel.value)
    .filter((a) => !sourceSel.value || (a.source || "-") === sourceSel.value)
    .filter((a) => !query || `${a.company} ${a.title}`.toLowerCase().includes(query))
    .sort((a, b) => {
      const x = aplSortValue(a, aplSort.key), y = aplSortValue(b, aplSort.key);
      const order = x < y ? -1 : x > y ? 1 : 0;
      return aplSort.desc ? -order : order;
    });
  document.querySelectorAll("#apltable th[data-sort]").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.sort === aplSort.key ? (aplSort.desc ? "descending" : "ascending") : "none");
  });
  if (!rows.length) {
    $("apltable").querySelector("tbody").replaceChildren(el("tr", {},
      el("td", { colspan: "9", class: "muted", text: shown.length ? "No applications match the search or filters."
        : aplEmptyText() })));
    return;
  }
  $("apltable").querySelector("tbody").replaceChildren(...rows.map((app) => {
    const stage = el("select", { "aria-label": `Stage of ${app.company}` },
      ...[...aplData.stages, ...aplData.ends].map((s) => el("option", { value: s, text: APL_STAGE_NAMES[s] })));
    stage.value = app.stage;
    stage.addEventListener("click", (ev) => ev.stopPropagation());
    stage.addEventListener("change", () => aplSetStage(app.job_id, stage.value));
    const row = el("tr", { "data-job-id": app.job_id },
      el("td", {}, el("b", { text: app.company || "?" })),
      el("td", { text: app.title || "" }),
      el("td", {}, stage),
      el("td", { text: aplDate(app.applied_at) }),
      el("td", { text: String(app.days_since_applied ?? "-") }),
      el("td", { text: app.source || "-" }),
      el("td", {}, el("span", { class: "aplchip score", text: aplScore(app) })),
      el("td", {}, app.follow_up_due || app.follow_up_at
        ? el("span", { class: "aplchip" + (app.follow_up_due ? " due" : ""), text: aplFollowText(app) }) : "-"),
      el("td", { text: aplDate(app.last_update) }));
    row.addEventListener("click", () => aplOpenPanel(app.job_id));
    return row;
  }));
}

async function aplSetStage(jobId, stage) {
  try {
    await sendJSON("POST", `/api/applied/${encodeURIComponent(jobId)}/stage`, { stage });
  } catch (err) {
    alert(`Could not change the stage: ${err.message}`);
  }
  await loadApplied();
}

// ---------------------------------------------------------------- side panel

async function aplOpenPanel(jobId, fresh = true) {
  if (fresh && aplOpen !== jobId) aplChat = [];
  aplOpen = jobId;
  let app;
  try {
    app = await getJSON(`/api/applied/${encodeURIComponent(jobId)}`);
  } catch (err) {
    if (aplOpen !== jobId) return;
    $("apldrawer").replaceChildren(el("p", { text: `Could not open it: ${err.message}` }));
    $("apldrawer").hidden = false;
    return;
  }
  // Closed (Escape, ×) or another one opened while this was loading: an
  // answer arriving late must not open the panel again.
  if (aplOpen !== jobId) return;
  aplRenderPanel(app);
}

function aplRenderPanel(app) {
  const drawer = $("apldrawer");
  const close = el("button", { type: "button", class: "aplclose", "aria-label": "Close", text: "×" });
  close.addEventListener("click", () => { drawer.hidden = true; aplOpen = ""; });

  const stage = el("select", { "aria-label": "Stage" },
    ...[...aplData.stages, ...aplData.ends].map((s) => el("option", { value: s, text: APL_STAGE_NAMES[s] })));
  stage.value = app.stage;
  stage.addEventListener("change", () => aplSetStage(app.job_id, stage.value));

  const timeline = el("div", { class: "apltl" }, ...(app.timeline || []).map((e) => el("div", {},
    e.kind === "stage" ? `Moved to ${APL_STAGE_NAMES[e.text.split(":")[0]] || e.text}${e.text.includes(":") ? " -" + e.text.split(":").slice(1).join(":") : ""}` : e.text,
    el("br"), el("small", { text: aplDate(e.at) }))));

  // Contact: who the messages go to, and how you know them (the connection line).
  const c = app.contact || {};
  const field = (name, label, value, placeholder) => el("label", { class: "aplfield" }, label,
    el("input", { type: "text", "data-contact": name, value: value || "", placeholder }));
  const contact = el("div", { class: "aplcontact" },
    field("name", "Name", c.name, "e.g. Priya S."),
    field("role", "Role", c.role, "e.g. Engineering Manager"),
    field("link", "Profile link", c.link, "https://www.linkedin.com/in/…"),
    field("relation", "How you know them", c.relation, "e.g. we both worked at Acme; same college"));
  const saveContact = el("button", { type: "button", text: "Save contact" });
  saveContact.addEventListener("click", async () => {
    const payload = {};
    contact.querySelectorAll("[data-contact]").forEach((i) => { payload[i.dataset.contact] = i.value; });
    try {
      const saved = await sendJSON("POST", `/api/applied/${encodeURIComponent(app.job_id)}/contact`, payload);
      if (aplOpen === app.job_id) aplRenderPanel(saved);
    } catch (err) { alert(`Could not save: ${err.message}`); }
  });

  // Messages: one tab per kind, the draft editable in place.
  const tabs = el("div", { class: "apltabs", role: "tablist" }, ...(aplData.kinds || []).map((k) => {
    const sent = (app.messages_sent || {})[k];
    const b = el("button", { type: "button", role: "tab", "aria-selected": String(k === aplKind),
      class: k === aplKind ? "on" : "", text: APL_KIND_NAMES[k] + (sent ? " ✓" : "") });
    b.addEventListener("click", () => { aplKind = k; aplChat = []; aplRenderPanel(app); });
    return b;
  }));
  const message = (app.messages || {})[aplKind] || {};
  const box = el("textarea", { class: "apldraft", rows: "7", "aria-label": `${APL_KIND_NAMES[aplKind]} message`,
    placeholder: "No draft yet - press Draft it, or ask in the chat below." });
  box.value = message.text || "";
  box.addEventListener("change", () => sendJSON("POST",
    `/api/applied/${encodeURIComponent(app.job_id)}/messages/${aplKind}`, { text: box.value }).catch(() => {}));
  const note = el("span", { class: "muted aplsent", text: message.sent_at ? `Sent ${aplDate(message.sent_at)}` : "Not sent yet" });
  const draftBtn = el("button", { type: "button", text: message.text ? "Redraft" : "Draft it" });
  draftBtn.addEventListener("click", async () => {
    draftBtn.disabled = true;
    note.textContent = "Writing…";
    try {
      const out = await sendJSON("POST", `/api/applied/${encodeURIComponent(app.job_id)}/messages/${aplKind}/draft`);
      // The server kept it; the panel shows it without asking again.
      app.messages = { ...(app.messages || {}), [aplKind]: { ...message, text: out.draft } };
      aplChat.push({ role: "assistant", text: out.reply });
      aplRenderPanel(app);
    } catch (err) { note.textContent = `Could not draft: ${err.message}`; draftBtn.disabled = false; }
  });
  const copyBtn = el("button", { type: "button", class: "btn-primary", text: "Copy" });
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(box.value); note.textContent = "Copied - paste it where you send it."; }
    catch { box.select(); note.textContent = "Select-all done: press Ctrl+C."; }
  });
  const sentBtn = el("button", { type: "button", text: "Mark sent" });
  sentBtn.addEventListener("click", async () => {
    try {
      const out = await sendJSON("POST", `/api/applied/${encodeURIComponent(app.job_id)}/messages/${aplKind}/sent`);
      await loadApplied();
      if (aplOpen === app.job_id) aplRenderPanel(out.detail);
    } catch (err) { alert(`Could not mark it: ${err.message}`); }
  });

  // The chat that edits this message; it knows the job and your profile.
  const log = el("div", { class: "aplchat", "aria-live": "polite" },
    el("div", { class: "b ai", text: `Want any changes to the ${APL_KIND_NAMES[aplKind].toLowerCase()} message?` }),
    ...aplChat.map((m) => el("div", { class: "b " + (m.role === "you" ? "me" : "ai"), text: m.text })));
  const input = el("input", { type: "text", "aria-label": "Ask for a change",
    placeholder: "e.g. make it shorter, drop the college line" });
  const send = el("button", { type: "button", class: "btn-primary", text: "Send" });
  const ask = async () => {
    const text = input.value.trim();
    if (!text) return;
    aplChat.push({ role: "you", text });
    log.append(el("div", { class: "b me", text }), el("div", { class: "b ai pending", text: "…" }));
    log.scrollTop = log.scrollHeight;
    input.value = "";
    send.disabled = true;
    try {
      const out = await sendJSON("POST", `/api/applied/${encodeURIComponent(app.job_id)}/messages/${aplKind}/chat`,
        { text, draft: box.value, history: aplChat.slice(0, -1) });
      aplChat.push({ role: "assistant", text: out.reply });
      box.value = out.draft;
      app.messages = { ...(app.messages || {}), [aplKind]: { ...message, text: out.draft } };
    } catch (err) {
      aplChat.push({ role: "assistant", text: `Could not answer: ${err.message}` });
    }
    send.disabled = false;
    log.replaceChildren(...log.children[0] ? [log.children[0]] : [],
      ...aplChat.map((m) => el("div", { class: "b " + (m.role === "you" ? "me" : "ai"), text: m.text })));
    log.scrollTop = log.scrollHeight;
    input.focus();
  };
  send.addEventListener("click", ask);
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") ask(); });

  const notes = el("textarea", { class: "aplnotes", rows: "3", "aria-label": "Notes",
    placeholder: "e.g. recruiter said round two is system design" });
  notes.value = app.notes || "";
  notes.addEventListener("change", () => sendJSON("POST",
    `/api/applied/${encodeURIComponent(app.job_id)}/notes`, { notes: notes.value }).catch(() => {}));

  const links = [app.listing_url && ["Posting", app.listing_url], app.apply_url && ["Application", app.apply_url]]
    .filter(Boolean).map(([label, href]) => el("a", { href, target: "_blank", rel: "noopener", text: label }));

  // replaceChildren writes a null out as the text "null": only real nodes.
  drawer.replaceChildren(...[
    close,
    el("h2", { text: app.company || "?" }),
    el("div", { class: "muted", text: app.title || "" }),
    el("div", { class: "aplrow" }, "Stage ", stage, el("span", { class: "aplchip score", text: `score ${aplScore(app)}` }),
      ...links.flatMap((a) => [" ", a])),
    app.follow_up_due
      ? el("div", { class: "aplbanner", text: app.follow_up_at
        ? `Follow-up due since ${aplDate(app.follow_up_at)} - see the Follow-up message below.`
        : `No update for ${app.days_since_update} days - send a follow-up?` }) : null,
    el("h4", { text: "Timeline" }), timeline,
    el("h4", { text: "Contact" }), contact, saveContact,
    el("h4", { text: "Messages" }), tabs, box,
    el("div", { class: "aplbtns" }, copyBtn, sentBtn, draftBtn, note),
    el("h4", {}, "Edit with AI ", el("small", { class: "muted", text: "(knows this job and your profile)" })),
    log, el("div", { class: "aplchatin" }, input, send),
    el("h4", { text: "Notes" }), notes].filter((node) => node != null));
  drawer.hidden = false;
  log.scrollTop = log.scrollHeight;
}

$("aplrun").addEventListener("change", () => aplSetRun($("aplrun").value));
$("apldue").addEventListener("change", () => { aplDueOnly = $("apldue").checked; aplRender(); });
$("apllayoutboard").addEventListener("click", () => aplSetLayout("board"));
$("apllayouttable").addEventListener("click", () => aplSetLayout("table"));
$("aplsearch").addEventListener("input", aplRenderTable);
$("aplstagefilter").addEventListener("change", aplRenderTable);
$("aplsourcefilter").addEventListener("change", aplRenderTable);
document.querySelectorAll("#apltable th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    aplSort = { key: th.dataset.sort, desc: aplSort.key === th.dataset.sort ? !aplSort.desc : true };
    aplRenderTable();
  });
});
document.addEventListener("keydown", (ev) => {
  // Also while the panel is still loading: Escape then means "never mind".
  if (ev.key === "Escape" && (aplOpen || !$("apldrawer").hidden)) { $("apldrawer").hidden = true; aplOpen = ""; }
});
// The tab's count, before it is opened.
getJSON("/api/applied").then((data) => {
  aplData = data;
  $("nav-applied").textContent = data.summary.total ? `Applied (${data.summary.total})` : "Applied";
}).catch(() => {});
