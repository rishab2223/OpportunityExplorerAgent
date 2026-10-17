// The People page (#51): for each job in a run, who can help with it - who
// posted it, likely hiring managers, recruiters, engineers who could refer
// you - and a note to each, drafted for you to send yourself. And the
// Settings page's spending card: what people search may spend on Apify.
//
// Built with DOM calls (app.js's el), never innerHTML: names and headlines
// come from LinkedIn profiles.

const PPL_RUN_KEY = "oea.people.run";
const PPL_KINDS = ["poster", "managers", "recruiters", "engineers"];
const PPL_KIND = {
  poster: { title: "Posted this job", src: "from the posting", button: "Message", drawer: "Note to the poster" },
  managers: { title: "Likely hiring managers", src: "by role, company and city", button: "Note to manager",
              drawer: "Note to the hiring manager" },
  recruiters: { title: "Recruiters", src: "by role, company and city", button: "Note to recruiter",
                drawer: "Note to the recruiter" },
  engineers: { title: "Engineers who could refer you", src: "current company checked", button: "Ask for referral",
               drawer: "Referral request" },
};
const PPL_STATE = { not_applied: "Not applied", applied: "Applied", referral: "Referral asked" };
const PPL_EMPTY = {
  not_applied: "No jobs in this run are waiting to be applied to.",
  applied: "Nothing from this run is marked applied yet.",
  referral: "No referrals asked for from this run yet. Mark sent on a note moves a job here.",
};
const PPL_POSTER_NOTE = {
  busy: "Could not read it while an apply window is open.",
  signin: "LinkedIn asked to sign in. Sign in once in the apply window, then try again.",
  error: "Could not read the posting.",
};
const PPL_NOTE_LIMIT = 300;

let pplRuns = [];
let pplStamp = "";
let pplTab = "not_applied";
let pplJobs = [];
let pplBudget = null;
let pplPage = 0;
let pplLoadToken = 0;
let pplWarmed = false;
const pplBusy = new Set();        // jobs with a search or a posting read in flight
const pplErrors = {};             // job id -> what went wrong last time
const pplPosterNotes = {};        // job id -> busy | signin | error, from the last read
let pplDrawer = null;             // { job, person, which, follow, texts, sent_at, follow_up_at, token }

function pplDate(iso) {
  if (!iso) return "";
  const when = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return isNaN(when) ? "" : when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function pplMoney(usd) {
  return usd == null ? "?" : `$${Number(usd).toFixed(2)}`;
}

function pplCents(usd) {
  return `${Math.max(1, Math.round(Number(usd || 0) * 100))}¢`;
}

// Profile links come from search results: only LinkedIn's, only https.
function pplProfileUrl(url) {
  return /^https:\/\/([a-z]{2,3}\.)?linkedin\.com\/in\//i.test(url || "") ? url : "";
}

function pplPostingUrl(url) {
  return /^https?:\/\//i.test(url || "") ? url : "";
}

function pplSetNav(due) {
  $("nav-people").textContent = due ? `People (${due} due)` : "People";
  $("nav-people").title = due
    ? `${due} note(s) sent 5+ days ago with no reply marked: time to follow up`
    : "Who can help with each job: who posted it, hiring managers, recruiters, engineers who could refer you";
}

async function loadPeople() {
  if (!pplWarmed) {
    pplWarmed = true;
    // The message model takes ~20 s to start; start it while you look.
    fetch("/api/people/warm", { method: "POST" }).catch(() => {});
  }
  await Promise.all([pplLoadRuns(), pplLoadBudget()]);
  await pplLoadRun();
}

async function pplLoadRuns() {
  try {
    const data = await getJSON("/api/people/runs");
    pplRuns = data.runs || [];
    pplSetNav(data.follow_ups_due || 0);
  } catch (err) {
    pplRuns = [];
    pplInfo(`Could not load the runs: ${err.message}`);
  }
  let saved = null;
  try { saved = localStorage.getItem(PPL_RUN_KEY); } catch { /* per-viewer nicety only */ }
  const stamps = pplRuns.map((r) => r.stamp);
  if (!stamps.includes(pplStamp)) {
    const busiest = pplRuns.find((r) => r.not_applied + r.applied + r.referral > 0);
    pplStamp = stamps.includes(saved) ? saved : (busiest || pplRuns[0] || {}).stamp || "";
  }
  const select = $("pplrun");
  select.replaceChildren(...pplRuns.map((r) => el("option", {
    value: r.stamp, title: r.stamp,
    text: `${runLabel(r.stamp)} · not applied ${r.not_applied} · applied ${r.applied} · referral ${r.referral}`,
  })));
  select.value = pplStamp;
  select.disabled = !pplRuns.length;
}

async function pplLoadBudget(fresh = false) {
  try {
    pplBudget = await getJSON(`/api/people/budget${fresh ? "?fresh=true" : ""}`);
  } catch (err) {
    pplBudget = { mode: "unknown", error: err.message };
  }
  pplRenderAlert();
}

async function pplLoadRun() {
  const token = ++pplLoadToken;
  if (!pplStamp) {
    pplJobs = [];
    pplRender();
    return;
  }
  try {
    const data = await getJSON(`/api/people/runs/${pplStamp}`);
    if (token !== pplLoadToken) return;      // another run was picked meanwhile
    pplJobs = data.jobs || [];
  } catch (err) {
    if (token !== pplLoadToken) return;
    pplJobs = [];
    pplRender();
    pplInfo(`Could not load this run: ${err.message}`);
    return;
  }
  pplRender();
}

function pplInfo(text) {
  $("ppljobs").replaceChildren(el("p", { class: "muted pplempty", text }));
}

function pplRenderAlert() {
  const box = $("pplalert");
  const b = pplBudget || {};
  const settingsLink = () => {
    const link = el("button", { type: "button", class: "linklike", text: "Spending settings" });
    link.addEventListener("click", () => showView("settings"));
    return link;
  };
  let parts = null;
  if (b.mode === "paused") {
    parts = [el("b", { text: `People search is paused${b.resets ? " until " + pplDate(b.resets) : ""}.` }),
      ` Your Apify account is at ${pplMoney(b.account_used)}; it pauses at ${pplMoney(b.pause_at)} so the rest still pays for job fetches. People already found stay, and a LinkedIn posting's hiring team can still be read (free). `,
      settingsLink()];
  } else if (b.mode === "small") {
    parts = [el("b", { text: `Over this month's ${pplMoney(b.soft)} for people search.` }),
      ` Searches now find ${pplSizes("small")}, about ${pplCents(b.next_cost)} a job. Job fetches are not affected. `,
      settingsLink()];
  } else if (b.mode === "unknown") {
    parts = [el("b", { text: "Could not read your Apify spending." }),
      ` ${b.error || ""} Find people is off until it can be read. `, settingsLink()];
  }
  box.hidden = !parts;
  if (parts) box.replaceChildren(...parts);
}

function pplCounts() {
  const counts = { not_applied: 0, applied: 0, referral: 0 };
  pplJobs.forEach((j) => { counts[j.bucket] = (counts[j.bucket] || 0) + 1; });
  return counts;
}

function pplRender() {
  const counts = pplCounts();
  Object.keys(counts).forEach((k) => { $("ppln-" + k).textContent = String(counts[k]); });
  document.querySelectorAll("[data-ppltab]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.ppltab === pplTab)));
  if (!pplStamp) {
    $("pplpager").hidden = true;
    pplInfo("No runs yet. Start a run on the Jobs tab; its jobs show here.");
    return;
  }
  const jobs = pplJobs.filter((j) => j.bucket === pplTab);
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  pplPage = Math.min(Math.max(pplPage, 0), pages - 1);
  renderPager($("pplpager"), pplPage, pages, jobs.length, "job(s)", (p) => {
    pplPage = p;
    pplRender();
    $("view-people").scrollIntoView({ block: "start" });
  });
  if (!jobs.length) {
    pplInfo(PPL_EMPTY[pplTab]);
    return;
  }
  $("ppljobs").replaceChildren(...jobs.slice(pplPage * PAGE_SIZE, (pplPage + 1) * PAGE_SIZE).map(pplCard));
}

function pplSizes(mode) {
  const b = pplBudget || {};
  const [m, r, e] = (mode === "small" ? b.small : b.full) || [3, 3, 4];
  return `${m} manager${m === 1 ? "" : "s"}, ${r} recruiter${r === 1 ? "" : "s"} and ${e} engineer${e === 1 ? "" : "s"}`;
}

function pplCanSearch() {
  const mode = (pplBudget || {}).mode;
  return mode === "full" || mode === "small";
}

function pplCard(job) {
  const meta = [`score ${scoreText(job)}`,
    job.posted_at ? `posted ${pplDate(job.posted_at)}` : "",
    job.applied_at ? `applied ${pplDate(job.applied_at)}` : "",
    job.bucket === "referral" && job.referral_contact ? `asked ${job.referral_contact}` : ""].filter(Boolean).join(" · ");
  const posting = pplPostingUrl(job.listing_url);
  const head = el("div", { class: "pplhead" },
    el("span", { class: "pplti", text: job.title || "?" }),
    el("span", { class: "muted", text: [job.company, job.location].filter(Boolean).join(" · ") }),
    el("span", { class: "pplright" },
      el("span", { class: `pplstate ${job.bucket}`, text: PPL_STATE[job.bucket] }),
      el("span", { class: "muted pplmeta", text: meta }),
      posting ? el("a", { href: posting, target: "_blank", rel: "noopener", text: "Posting ↗" }) : null));
  const card = el("article", { class: "ppljob", "data-job-id": job.job_id }, head);
  if (pplErrors[job.job_id]) card.append(el("div", { class: "pplerror", role: "alert", text: pplErrors[job.job_id] }));
  if (pplBusy.has(job.job_id)) {
    card.append(el("div", { class: "pplsearching" }, el("span", { class: "pplspin", "aria-hidden": "true" }),
      " Finding people… about 20 seconds."));
    return card;
  }
  if (!job.searched) {
    card.append(pplUnsearched(job));
    // Who posted it may be known without a search (read while paused).
    if ((job.poster || {}).people && job.poster.people.length) {
      card.append(el("div", { class: "pplgroups" }, pplGroup(job, "poster")));
    }
    return card;
  }
  if (job.team) card.append(el("div", { class: "pplhint" }, "The posting names the team: ", el("b", { text: job.team })));
  card.append(el("div", { class: "pplgroups" }, ...PPL_KINDS.map((kind) => pplGroup(job, kind))), pplFoot(job));
  return card;
}

function pplUnsearched(job) {
  const b = pplBudget || {};
  const box = el("div", { class: "pplunsearched" });
  if (b.mode === "paused") {
    if (job.linkedin_posting && !(job.poster && job.poster.result === "found")) {
      box.append(pplButton("Read the posting's hiring team · free", "", () => pplReadPoster(job)));
    }
    box.append(el("span", { class: "muted", text: `People search is paused${b.resets ? " until " + pplDate(b.resets) : ""}.` }));
    return box;
  }
  const find = pplButton(`Find people · ~${pplCents(b.next_cost)}`, "btn-primary", () => pplFind(job, {}));
  find.disabled = !pplCanSearch();
  box.append(find, el("span", { class: "muted", text: pplCanSearch()
    ? `${job.linkedin_posting ? "Who posted it, " : ""}${pplSizes(b.mode)} at ${job.company}${job.city ? " in " + job.city : ""}.`
    : "Find people is off until your Apify spending can be read." }));
  return box;
}

function pplButton(text, cls, onClick) {
  const b = el("button", { type: "button", class: cls || null, text });
  b.addEventListener("click", onClick);
  return b;
}

function pplGroup(job, kind) {
  const info = PPL_KIND[kind];
  const wide = kind !== "poster" && (job.wide || {})[kind];
  const people = kind === "poster" ? ((job.poster || {}).people || []) : ((job.groups || {})[kind] || []);
  const list = el("div", { class: "ppllist" });
  if (people.length) {
    people.forEach((p, i) => list.append(pplPerson(job, p, kind, i)));
  } else {
    list.append(pplGroupEmpty(job, kind));
  }
  return el("section", { class: "pplgroup", "aria-label": info.title },
    el("h3", {}, info.title, el("span", { class: "pplsrc", text: ` · ${info.src}${wide ? " · any city" : ""}` })), list);
}

function pplGroupEmpty(job, kind) {
  const box = el("div", { class: "pplnone" });
  if (kind === "poster") {
    const poster = job.poster || null;
    const note = PPL_POSTER_NOTE[pplPosterNotes[job.job_id]];
    if (!job.linkedin_posting || (poster && poster.result === "not_linkedin")) {
      box.append("Only LinkedIn postings show who posted them.");
    } else if (note) {
      box.append(note + " ", pplButton("Try again", "linklike", () => pplReadPoster(job)));
    } else if (poster && poster.result === "none") {
      box.append("This posting does not show its hiring team.");
    } else {
      box.append("Not read yet. ", pplButton("Read it · free", "linklike", () => pplReadPoster(job)));
    }
    return box;
  }
  if (job.city && job.searched_city && !job.searched_anywhere) {
    box.append(`None found in ${job.city}. `);
    const wider = pplButton(`Search without the city · ~${pplCents((pplBudget || {}).next_cost)}`, "linklike",
      () => pplFind(job, { without_city: true }));
    wider.disabled = !pplCanSearch();
    box.append(wider);
  } else {
    box.append(`None found at ${job.company}.`);
  }
  return box;
}

function pplPerson(job, p, kind, i) {
  const profile = pplProfileUrl(p.profile_url);
  const label = p.sent_at ? (p.follow_up_due ? "Follow up" : "Sent ✓") : PPL_KIND[kind].button;
  const primary = (kind === "engineers" && i === 0 && !p.sent_at) || p.follow_up_due;
  const open = pplButton(label, primary ? "btn-primary" : "", () => pplOpenDrawer(job, p));
  open.setAttribute("aria-label", `${label}: ${p.name}`);
  const sent = p.sent_at
    ? el("span", { class: "pplsent" + (p.follow_up_due ? " due" : ""),
      text: `Note sent ${pplDate(p.sent_at)} · ` + (p.follow_up_due
        ? `no reply marked; follow-up due since ${pplDate(p.follow_up_at)}`
        : `follow-up on ${pplDate(p.follow_up_at)}`) })
    : null;
  return el("div", { class: "pplperson" },
    el("span", { class: "ppln", text: p.name }),
    el("span", { class: "pplacts" },
      profile ? el("a", { class: "btnlike", href: profile, target: "_blank", rel: "noopener", text: "LinkedIn ↗",
        "aria-label": `${p.name} on LinkedIn` }) : null,
      open),
    p.headline ? el("span", { class: "pplh", text: p.headline }) : null,
    p.flag ? el("span", { class: "pplflag", text: `⚠ ${p.flag}` }) : null,
    sent);
}

function pplFoot(job) {
  const s = job.search || {};
  const b = pplBudget || {};
  const again = pplButton(`Search again · ~${pplCents(b.next_cost)}`, "linklike", () => {
    if (window.confirm(`Search ${job.company} again? It costs about ${pplCents(b.next_cost)} and replaces this list.`)) {
      pplFind(job, { again: true });
    }
  });
  again.disabled = !pplCanSearch();
  const age = s.old
    ? `found ${pplDate(s.found_at)}, over 14 days ago: people may have moved on`
    : `found ${pplDate(s.found_at)} for ${pplMoney(s.cost_usd)}, reused for ${job.company} jobs until ${pplDate(s.reused_until)}`;
  return el("div", { class: "pplfoot" },
    el("span", { class: "muted" + (s.old ? " pplold" : ""), text: (s.size === "small" ? "Smaller search, " : "") + age + " · " }),
    again,
    s.note ? el("div", { class: "pplwarn", text: `Part of the search did not finish: ${s.note}. Search again to retry.` }) : null);
}

async function pplFind(job, options) {
  if (pplBusy.has(job.job_id)) return;
  pplBusy.add(job.job_id);
  delete pplErrors[job.job_id];
  pplRender();
  try {
    const out = await sendJSON("POST", `/api/people/runs/${job.stamp}/jobs/${encodeURIComponent(job.job_id)}/find`, options);
    if (out.error) pplErrors[job.job_id] = out.error;
    pplNotePoster(job.job_id, out.poster);
  } catch (err) {
    pplErrors[job.job_id] = `Could not search: ${err.message}`;
  }
  pplBusy.delete(job.job_id);
  await Promise.all([pplLoadBudget(), pplLoadRuns()]);
  await pplLoadRun();
}

function pplNotePoster(jobId, result) {
  if (PPL_POSTER_NOTE[result]) pplPosterNotes[jobId] = result;
  else delete pplPosterNotes[jobId];
}

async function pplReadPoster(job) {
  if (pplBusy.has(job.job_id)) return;
  pplBusy.add(job.job_id);
  pplRender();
  try {
    const out = await sendJSON("POST", `/api/people/runs/${job.stamp}/jobs/${encodeURIComponent(job.job_id)}/poster`);
    pplNotePoster(job.job_id, out.result);
    if (out.error) pplErrors[job.job_id] = `Could not read the posting: ${out.error}`;
  } catch (err) {
    pplErrors[job.job_id] = `Could not read the posting: ${err.message}`;
  }
  pplBusy.delete(job.job_id);
  await pplLoadRun();
}

// ---------------------------------------------------------------- the note

function pplApi(job, action) {
  return `/api/people/runs/${job.stamp}/jobs/${encodeURIComponent(job.job_id)}/${action}`;
}

function pplWho(person) {
  return { name: person.name, headline: person.headline, profile_url: person.profile_url, kind: person.kind };
}

async function pplOpenDrawer(job, person) {
  const token = Symbol("drawer");
  pplDrawer = { job, person, which: "note", follow: !!person.follow_up_due, texts: { note: "", message: "" },
                sent_at: person.sent_at, follow_up_at: person.follow_up_at, token, status: "" };
  pplRenderDrawer();
  try {
    const held = await sendJSON("POST", pplApi(job, "message"), { person: pplWho(person) });
    if (!pplDrawer || pplDrawer.token !== token) return;
    pplDrawer.texts = { note: held.note || "", message: held.message || "" };
    pplDrawer.sent_at = held.sent_at || "";
    pplDrawer.follow_up_at = held.follow_up_at || "";
  } catch (err) {
    if (!pplDrawer || pplDrawer.token !== token) return;
    pplDrawer.status = `Could not load the saved note: ${err.message}`;
  }
  pplRenderDrawer();
  // A first look writes the note; a sent one waits for Write a follow-up.
  if (!pplDrawer.texts.note && !pplDrawer.sent_at) pplDraft();
}

function pplCloseDrawer() {
  pplDrawer = null;
  $("ppldrawer").hidden = true;
}

async function pplDraft() {
  const d = pplDrawer;
  if (!d || d.drafting) return;
  const { token, which } = d;
  d.drafting = true;
  d.status = pplWarmed ? "Writing…" : "Writing… the first draft takes about 20 seconds while the model starts.";
  pplRenderDrawer();
  try {
    const out = await sendJSON("POST", pplApi(d.job, "draft"),
      { person: pplWho(d.person), which, follow_up: d.follow });
    if (!pplDrawer || pplDrawer.token !== token) return;
    d.texts[which] = out.text || "";
    d.status = out.reply || "";
  } catch (err) {
    if (!pplDrawer || pplDrawer.token !== token) return;
    d.status = `Could not draft: ${err.message}. You can write it yourself here.`;
  }
  d.drafting = false;
  pplRenderDrawer();
}

function pplRenderDrawer() {
  const d = pplDrawer;
  const drawer = $("ppldrawer");
  if (!d) { drawer.hidden = true; return; }
  const { job, person } = d;
  const close = el("button", { type: "button", class: "aplclose", "aria-label": "Close", text: "×" });
  close.addEventListener("click", pplCloseDrawer);

  const seg = el("div", { class: "aplseg", role: "group", "aria-label": "Which message" },
    ...[["note", "Connection note"], ["message", "Message after they accept"]].map(([which, text]) => {
      const b = el("button", { type: "button", "aria-pressed": String(d.which === which), text });
      b.addEventListener("click", () => {
        d.which = which;
        d.status = "";
        pplRenderDrawer();
        if (!d.texts[which] && !d.drafting && (!d.sent_at || d.follow)) pplDraft();
      });
      return b;
    }));
  const count = el("span", { class: "pplcount" });
  const box = el("textarea", { class: "apldraft ppldraft", rows: "9", "aria-label": d.which === "note" ? "Connection note" : "Message",
    placeholder: d.drafting ? "Writing…" : "Write it here, or press Draft it." });
  box.value = d.texts[d.which] || "";
  box.disabled = !!d.drafting;
  const recount = () => {
    const n = box.value.length;
    count.textContent = d.which === "note" ? `${n} / ${PPL_NOTE_LIMIT}` : `${n} characters`;
    count.classList.toggle("over", d.which === "note" && n > PPL_NOTE_LIMIT);
  };
  recount();
  box.addEventListener("input", () => { d.texts[d.which] = box.value; recount(); });
  box.addEventListener("change", () => sendJSON("POST", pplApi(job, "text"),
    { person: pplWho(person), which: d.which, text: box.value }).catch(() => {}));

  const status = el("div", { class: "muted pplstatus", role: "status", text: d.status || "" });
  const copy = pplButton("Copy", "", async () => {
    try { await navigator.clipboard.writeText(box.value); status.textContent = "Copied. Paste it into LinkedIn."; }
    catch { box.select(); status.textContent = "Selected: press Ctrl+C to copy."; }
  });
  const profile = pplProfileUrl(person.profile_url);
  const draftBtn = pplButton(d.texts[d.which] ? "Redraft" : "Draft it", "", pplDraft);
  draftBtn.disabled = !!d.drafting;
  const sent = pplButton(d.sent_at ? "Mark sent again" : "Mark sent", "btn-primary", () => pplMarkSent(d));
  const followBtn = d.sent_at && !d.follow ? pplButton("Write a follow-up", "", () => {
    d.follow = true;
    d.texts = { note: "", message: "" };
    pplDraft();
  }) : null;

  const sentLine = d.sent_at ? el("div", { class: "aplbanner pplsentline" },
    `Sent ${pplDate(d.sent_at)}; follow-up ${new Date(d.follow_up_at) <= new Date() ? "due since" : "on"} ${pplDate(d.follow_up_at)}. `,
    pplButton("Not sent? Undo", "linklike", () => pplUnsend(job, person))) : null;

  drawer.replaceChildren(...[
    close,
    el("h2", { id: "ppldh", text: d.follow ? "Follow-up" : PPL_KIND[person.kind].drawer }),
    el("div", { class: "muted", text: `To ${person.name} · ${job.title}, ${job.company}` }),
    person.headline ? el("div", { class: "muted pplh", text: person.headline }) : null,
    person.flag ? el("div", { class: "pplflag", text: `⚠ ${person.flag}` }) : null,
    sentLine,
    el("div", { class: "aplrow" }, seg, count),
    box,
    el("div", { class: "aplbtns" }, copy,
      profile ? el("a", { class: "btnlike", href: profile, target: "_blank", rel: "noopener", text: "Open their LinkedIn ↗" }) : null,
      draftBtn, followBtn, sent),
    status,
    el("p", { class: "muted pplhelp", text: "LinkedIn allows 300 characters in a connection note. Mark sent records who and when, " +
      (job.bucket === "not_applied" ? "moves the job to the Referral tab, " : "") +
      "and reminds you after 5 days without a reply. You send it yourself." }),
  ].filter((node) => node != null));
  drawer.hidden = false;
}

async function pplMarkSent(d) {
  const name = d.person.name;
  try {
    const out = await sendJSON("POST", pplApi(d.job, "sent"), { person: pplWho(d.person) });
    const job = d.job, person = d.person;
    pplCloseDrawer();
    pplNotice(`Marked sent to ${name}.` + (out.moved === "referral" ? " The job moved to the Referral tab." : "") +
      ` Reminder on ${pplDate(out.follow_up_at)}.`, () => pplUnsend(job, person));
  } catch (err) {
    pplDrawer && (pplDrawer.status = `Could not mark it: ${err.message}`);
    pplRenderDrawer();
    return;
  }
  await pplLoadRuns();
  await pplLoadRun();
}

async function pplUnsend(job, person) {
  try {
    const out = await sendJSON("POST", pplApi(job, "unsent"), { person: pplWho(person) });
    pplNotice(`Marked not sent to ${person.name}.` + (out.moved_back ? " The job is back under Not applied." : ""));
  } catch (err) {
    pplNotice(`Could not undo: ${err.message}`);
  }
  if (pplDrawer) pplCloseDrawer();
  await pplLoadRuns();
  await pplLoadRun();
}

function pplNotice(text, undo) {
  const box = $("pplnotice");
  box.replaceChildren(el("span", { text }),
    undo ? pplButton("Undo", "", () => { box.hidden = true; undo(); }) : null,
    pplButton("Dismiss", "linklike", () => { box.hidden = true; }));
  box.hidden = false;
}

// --------------------------------------------------------------- settings

let setDirty = false;

async function loadSettings(fresh = false) {
  $("setstate").textContent = "";
  try {
    pplBudget = await getJSON(`/api/people/budget${fresh ? "?fresh=true" : ""}`);
  } catch (err) {
    pplBudget = { mode: "unknown", error: err.message };
  }
  setRender();
}

function setRender() {
  const b = pplBudget || {};
  const spent = Number(b.people_spent || 0);
  $("setmeter").style.width = `${Math.min(100, b.soft ? (spent / b.soft) * 100 : 0)}%`;
  $("setbudget").classList.toggle("over", b.mode === "small" || b.mode === "paused");
  const account = b.account_used == null ? "Apify account: could not be read"
    : `Apify account: ${pplMoney(b.account_used)}${b.account_limit ? " of " + pplMoney(b.account_limit) : ""}`;
  $("setbudgettext").textContent = `People search this month: ${pplMoney(spent)} of ${pplMoney(b.soft)} · ${account} · ` +
    (b.mode === "paused" ? "paused" : b.mode === "unknown" ? "searches off" : `next search about ${pplCents(b.next_cost)}`) +
    (b.resets ? ` · resets ${pplDate(b.resets)}` : "");
  const alert = $("setalert");
  alert.hidden = !["small", "paused", "unknown"].includes(b.mode);
  alert.textContent = b.mode === "paused"
    ? `People search is paused: the account is at ${pplMoney(b.account_used)}, and it pauses at ${pplMoney(b.pause_at)}.`
    : b.mode === "small"
      ? `Over this month's ${pplMoney(b.soft)}: searches find ${pplSizes("small")} (about ${pplCents(b.next_cost)} a job). Job fetches are not affected.`
      : `Could not read your Apify spending: ${b.error || "no answer"}. Find people stays off until it can be read.`;
  if (!setDirty) {
    if (b.soft != null) $("setsoft").value = Number(b.soft).toFixed(2);
    if (b.pause_at != null) $("setpause").value = Number(b.pause_at).toFixed(2);
  }
  if (b.account_limit) $("setpause").max = String(b.account_limit);
  $("setsizes").textContent = `${pplSizes("full")} per job; above the smaller-searches limit, ${pplSizes("small")}.`;
}

async function setSave() {
  const soft = parseFloat($("setsoft").value), pause = parseFloat($("setpause").value);
  if (!Number.isFinite(soft) || !Number.isFinite(pause)) {
    $("setstate").textContent = "Both limits need a dollar amount.";
    return;
  }
  try {
    pplBudget = await sendJSON("PUT", "/api/people/settings", { soft, pause_at: pause });
    setDirty = false;
    setRender();
    $("setstate").textContent = "Saved.";
    pplRenderAlert();
  } catch (err) {
    $("setstate").textContent = `Not saved: ${err.message}`;
  }
}

$("pplrun").addEventListener("change", () => {
  pplStamp = $("pplrun").value;
  try { localStorage.setItem(PPL_RUN_KEY, pplStamp); } catch { /* per-viewer nicety only */ }
  pplPage = 0;
  pplJobs = [];
  pplInfo("Loading…");
  pplLoadRun();
});
document.querySelectorAll("[data-ppltab]").forEach((b) => b.addEventListener("click", () => {
  pplTab = b.dataset.ppltab;
  pplPage = 0;
  pplRender();
}));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && pplDrawer) pplCloseDrawer();
});
$("setsave").addEventListener("click", setSave);
$("setrefresh").addEventListener("click", () => loadSettings(true));
["setsoft", "setpause"].forEach((id) => $(id).addEventListener("input", () => {
  setDirty = true;
  $("setstate").textContent = "Not saved yet.";
}));
// The tab's reminder count, before it is opened.
getJSON("/api/people/due").then((data) => pplSetNav(data.follow_ups_due || 0)).catch(() => {});
