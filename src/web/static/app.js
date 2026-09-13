let currentStamp = "";
let currentJobId = "";
let jobsById = {};
let lastJobs = [];
let shortlistPage = 0;
let referralPage = 0;
const PAGE_SIZE = 15;

// Jobs ticked for a queue, by job_id. Survives a table re-render (a refresh,
// a page change) so a tick made on page 1 is still there after visiting
// page 2. Cleared when the queue actually starts.
const queueTicks = new Set();
let queueState = { current: null, pending: [], parked: [], done: [], note: "", active: false };

const $ = (id) => document.getElementById(id);
// The queue and job-info controls sit in a toolbar ABOVE and BELOW the table,
// so a long shortlist never needs scrolling back up. Both copies are driven
// together, which is why these are attributes rather than ids.
const act = (name) => document.querySelectorAll(`[data-act="${name}"]`);
const role = (name) => document.querySelectorAll(`[data-role="${name}"]`);

// Which page numbers to show either side of the jump box: the first three
// and the last two. Everything between them is reached by typing a number,
// which beats a strip of thirty buttons once a shortlist gets long.
const PAGER_HEAD = 3;
const PAGER_TAIL = 2;
const PAGER_ALL_UPTO = 7;   // few enough pages: show every one, no box

function pageNumbers(page, pages) {
  if (pages <= PAGER_ALL_UPTO) {
    return Array.from({ length: pages }, (_, i) => i);
  }
  const head = Array.from({ length: PAGER_HEAD }, (_, i) => i);
  const tail = Array.from({ length: PAGER_TAIL }, (_, i) => pages - PAGER_TAIL + i);
  return [...head, "box", ...tail];
}

// Prev / 1 2 3 … [go to page] … 9 10 / Next for a table. `go(page)` re-renders.
function renderPager(el, page, pages, total, noun, go) {
  el.replaceChildren();
  el.hidden = total <= PAGE_SIZE;
  if (el.hidden) return;
  const button = (text, target, opts = {}) => {
    const b = document.createElement("button");
    b.textContent = text;
    b.type = "button";
    if (opts.title) b.title = opts.title;
    if (opts.current) {
      b.classList.add("current");
      b.setAttribute("aria-current", "page");
    }
    b.disabled = opts.disabled || opts.current || false;
    b.addEventListener("click", () => go(target));
    el.appendChild(b);
    return b;
  };
  const gap = () => {
    const span = document.createElement("span");
    span.className = "muted";
    span.textContent = "…";
    el.appendChild(span);
  };

  button("‹ Prev", page - 1, { disabled: page === 0, title: "Previous page" });
  pageNumbers(page, pages).forEach((n) => {
    if (n !== "box") {
      button(String(n + 1), n, { current: n === page });
      return;
    }
    gap();
    const box = document.createElement("input");
    box.type = "number";
    box.className = "pagebox";
    box.min = "1";
    box.max = String(pages);
    box.value = String(page + 1);
    box.title = `Type a page between 1 and ${pages}, then press Enter`;
    box.setAttribute("aria-label", `Page number, 1 to ${pages}`);
    // A page inside the gap is still the current one: show it as such, so
    // the box doubles as "you are here" rather than only a jump.
    if (page >= PAGER_HEAD && page < pages - PAGER_TAIL) box.classList.add("current");
    const jump = () => {
      // Not `|| page + 1`: a typed 0 is falsy, so it was read as "nothing
      // typed" and the box sat there instead of clamping to page 1.
      const typed = parseInt(box.value, 10);
      const wanted = Number.isFinite(typed)
        ? Math.min(pages, Math.max(1, typed))
        : page + 1;
      box.value = String(wanted);
      if (wanted - 1 !== page) go(wanted - 1);
    };
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        jump();
      }
    });
    box.addEventListener("change", jump);
    el.appendChild(box);
    gap();
  });
  button("Next ›", page + 1, { disabled: page >= pages - 1, title: "Next page" });
  const info = document.createElement("span");
  info.className = "muted";
  info.textContent = `Page ${page + 1} of ${pages} - ${total} ${noun}`;
  el.appendChild(info);
}

function appendLog(box, text) {
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent += (box.textContent ? "\n" : "") + text;
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// The apply transcript is read while answering, so its lines are rendered as
// elements: what the agent asks stands out, routine fills recede, and errors
// are obvious. A long line keeps its full text in the tooltip.
const APPLY_LINE_KINDS = [
  [/^AGENT ASKS: /, "ask"],
  [/^AGENT ASKED: /, "asked"],
  [/^YOU: /, "you"],
  [/^(ERROR: |SESSION |Error: |Could not |Model error)/, "bad"],
  [/^(--- |\(reconnected)/, "meta"],
  [/^CHECK YOUR CONTACT DETAILS/, "warn"],
  [/^Left empty \(optional\)/, "warn"],
  [/^\[(profile|saved|resume|letter|estimate|again|corrected|llm|redo)\]/, "fill"],
  [/^(Asking the model|Model returned|Model calls|Compiling|Drafting|Redrafting|Estimating)/, "quiet"],
];

function applyLineKind(text) {
  for (const [pattern, kind] of APPLY_LINE_KINDS) {
    if (pattern.test(text)) return kind;
  }
  return "step";
}

// What a line starts with is what you scan for, so it is set in capitals:
// the source in brackets ([PROFILE], [SAVED]) and the action word (FILLED,
// SELECTED). The text itself is untouched - the CSS does the shouting.
const LINE_HEAD_RE =
  /^(\[[a-z]+\]\s*)?((?:could not [a-z]+|filled|selected|checked|unchecked|uploaded|clicked|removed|switched|opened|left empty|drafting|redrafting|compiling|estimating|asking the model|model returned|model calls|recorded as applied|page dumped)\b)?/i;

// `at` is seconds since the session began, and it is rendered as its own
// element rather than pushed into the text: every line kind is matched from
// the start of the line, so a prefix inside the string would break all of
// them. Only the first line of a multi-line event is stamped.
function appendApply(text, at) {
  const box = $("applylog");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  let first = true;
  for (const piece of String(text).split("\n")) {
    const line = document.createElement("div");
    line.className = `logline ${applyLineKind(piece)}`;
    if (first && typeof at === "number") {
      const stamp = document.createElement("span");
      stamp.className = "at";
      stamp.textContent = `${at.toFixed(1)}s`;
      stamp.title = "seconds since the session started";
      line.appendChild(stamp);
    }
    first = false;
    const head = LINE_HEAD_RE.exec(piece);
    const tag = (head && head[1]) || "";
    const verb = (head && head[2]) || "";
    if (tag || verb) {
      if (tag) {
        const el = document.createElement("span");
        el.className = "tag";
        el.textContent = tag;
        line.appendChild(el);
      }
      if (verb) {
        const el = document.createElement("span");
        el.className = "verb";
        el.textContent = verb;
        line.appendChild(el);
      }
      line.appendChild(document.createTextNode(piece.slice(tag.length + verb.length)));
    } else {
      line.textContent = piece;
    }
    if (piece.length > 160) line.title = piece;
    box.appendChild(line);
  }
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function resetApply(text) {
  const box = $("applylog");
  box.textContent = "";
  if (text) appendApply(text);
}

// The Apply card sits below the shortlist table and its two toolbars, so a
// session started from a row down the page begins off-screen. Only the two
// deliberate starts scroll; a background resync must never move the page
// under someone who is reading something else.
function showApplyCard() {
  const card = $("applycard");
  if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
}

function setLog(box, text) {
  box.textContent = text;
  box.scrollTop = box.scrollHeight;
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

function scoreClass(score) {
  if (score >= 9) return "score-high";
  if (score >= 8) return "score-mid";
  return "";
}

function link(url, label) {
  if (!url) return "";
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = label;
  return a;
}

function pathCell(job) {
  const cell = document.createElement("td");
  const pdfPath = job.resume_pdf_path || "";
  const text = document.createElement("div");
  text.className = "path";
  if (pdfPath) {
    text.textContent = pdfPath;
  } else if (job.tex_path) {
    text.textContent = job.tex_path + "  (tex only)";
  } else {
    text.textContent = "-";
  }
  text.title = text.textContent;   // clipped to two lines; hover for the rest
  cell.appendChild(text);
  const value = pdfPath || job.tex_path;
  if (value) {
    const copy = document.createElement("button");
    copy.textContent = "Copy";
    copy.addEventListener("click", (ev) => {
      ev.stopPropagation();
      navigator.clipboard.writeText(value).then(() => {
        copy.textContent = "Copied";
        setTimeout(() => (copy.textContent = "Copy"), 1200);
      });
    });
    cell.appendChild(copy);
  }
  return cell;
}

function cellButton(cell, label, title, onClick) {
  const btn = document.createElement("button");
  btn.textContent = label;
  if (title) btn.title = title;
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    onClick();
  });
  cell.appendChild(btn);
  return btn;
}

function actionsCell(job) {
  const cell = document.createElement("td");
  const hist = job.history_status || "";
  const blocked = hist === "applied" || hist === "closed";
  const apply = cellButton(cell, "Start apply", "", () => startApply(job.job_id));
  apply.disabled = blocked;
  if (hist === "applied") apply.title = "Already applied (see history); Unmark to re-enable";
  if (hist === "closed") apply.title = "This job stopped accepting applications; Unmark to re-enable";
  if (blocked) {
    cellButton(
      cell,
      "Unmark",
      "Remove from history so future runs process this job again",
      () => setHistory(job.job_id, "")
    );
    return cell;
  }
  cellButton(cell, "Skip", "", () => decide(job.job_id, "no"));
  cellButton(
    cell,
    "Mark applied",
    "Record as applied across runs; future scrapes will drop this job",
    () => setHistory(job.job_id, "applied")
  );
  cellButton(
    cell,
    "Referral",
    "Chase a referral instead of applying; moves this job to the Referrals table",
    () => {
      const who = window.prompt(`Who are you asking for a referral at ${job.company}?`, "");
      if (who === null) return; // cancelled - record nothing
      setHistory(job.job_id, "referral_pending", who.trim());
    }
  );
  cellButton(
    cell,
    "Closed",
    "This posting no longer accepts applications; future scrapes will drop it",
    () => setHistory(job.job_id, "closed")
  );
  return cell;
}

function referralActionsCell(job) {
  const cell = document.createElement("td");
  if (job.history_status === "referral_pending") {
    cellButton(cell, "Referral sent", "Your application went in via this referral", () =>
      setHistory(job.job_id, "referral_sent", job.history_contact || "")
    );
    cellButton(
      cell,
      "Referral failed",
      "Clear the referral; the job returns to the shortlist and to future runs",
      () => setHistory(job.job_id, "")
    );
  } else {
    cellButton(
      cell,
      "Clear",
      "Forget this referral; the job returns to the shortlist and to future runs",
      () => setHistory(job.job_id, "")
    );
  }
  return cell;
}

async function setHistory(jobId, status, contact) {
  try {
    await postJSON(`/api/runs/${currentStamp}/jobs/${encodeURIComponent(jobId)}/history`, {
      status,
      contact: contact || "",
    });
    await loadJobs(currentStamp);
  } catch (err) {
    alert(`Could not update history: ${err.message}`);
  }
}

const REFERRAL_STATES = ["referral_pending", "referral_sent"];

function emptyRow(body, colSpan, text) {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = colSpan;
  cell.className = "muted";
  cell.textContent = text;
  row.appendChild(cell);
  body.appendChild(row);
}

function rowCellAdder(row) {
  return (content, className) => {
    const cell = document.createElement("td");
    if (className) cell.className = className;
    if (content instanceof Node) cell.appendChild(content);
    else cell.textContent = content == null ? "" : String(content);
    row.appendChild(cell);
    return cell;
  };
}

function tickCell(job) {
  const cell = document.createElement("td");
  cell.className = "tick";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = queueTicks.has(job.job_id);
  box.title = "Queue this job";
  box.addEventListener("click", (ev) => ev.stopPropagation());  // not a row click
  box.addEventListener("change", () => {
    if (box.checked) queueTicks.add(job.job_id);
    else queueTicks.delete(job.job_id);
    box.closest("tr").classList.toggle("queued", box.checked);
    refreshQueueButton();
  });
  cell.appendChild(box);
  return cell;
}

function linksCell(job) {
  const links = document.createElement("td");
  const applyLink = link(job.apply_url, "apply");
  const listingLink = link(job.listing_url, "listing");
  if (applyLink) links.appendChild(applyLink);
  if (applyLink && listingLink) links.appendChild(document.createTextNode(" / "));
  if (listingLink) links.appendChild(listingLink);
  links.addEventListener("click", (ev) => ev.stopPropagation());
  return links;
}

function renderJobs(jobs) {
  lastJobs = jobs;
  jobsById = {};
  jobs.forEach((job) => (jobsById[job.job_id] = job));
  renderShortlist(jobs.filter((j) => !REFERRAL_STATES.includes(j.history_status)));
  renderReferrals(jobs.filter((j) => REFERRAL_STATES.includes(j.history_status)));
  if (currentJobId && jobsById[currentJobId]) selectJob(currentJobId);
  refreshQueueButton();
}

function renderShortlist(jobs) {
  const body = document.querySelector("#jobs tbody");
  body.replaceChildren();
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  shortlistPage = Math.min(Math.max(shortlistPage, 0), pages - 1);
  renderPager($("jobspager"), shortlistPage, pages, jobs.length, "job(s)", (p) => {
    shortlistPage = p;
    renderJobs(lastJobs);
  });
  if (!jobs.length) {
    emptyRow(body, 10, "No shortlisted jobs left in this run.");
    return;
  }
  const start = shortlistPage * PAGE_SIZE;
  jobs.slice(start, start + PAGE_SIZE).forEach((job) => {
    const row = document.createElement("tr");
    row.dataset.jobId = job.job_id;
    if (queueTicks.has(job.job_id)) row.classList.add("queued");
    const add = rowCellAdder(row);

    row.appendChild(tickCell(job));
    add(job.company);
    add(job.title);
    add(job.relevance, scoreClass(job.relevance));
    add(job.location);
    add(job.source || "-", "source");
    row.appendChild(linksCell(job));
    row.appendChild(pathCell(job));
    const hist = job.history_status || "";
    const shownStatus =
      hist === "applied" ? "applied ✓" : hist === "closed" ? "closed ✗" : job.status || "pending";
    const statusCell = add(
      shownStatus,
      "status-" + (hist === "applied" || hist === "closed" ? hist : job.status || "pending")
    );
    if (job.history_how === "similar") {
      statusCell.title = "Matched by company+title from your history";
    }
    row.appendChild(actionsCell(job));

    // Selecting is all a row click does: the details panel (and its 780px
    // PDF preview) opens from the Job info button, so the apply pane stays
    // directly under the table.
    row.addEventListener("click", () => selectJob(job.job_id));
    body.appendChild(row);
  });
}

function renderReferrals(jobs) {
  const body = document.querySelector("#referrals tbody");
  body.replaceChildren();
  const pending = jobs.filter((j) => j.history_status === "referral_pending").length;
  $("referralinfo").textContent = jobs.length
    ? `Run ${currentStamp}: ${pending} pending, ${jobs.length - pending} sent`
    : "";
  $("nav-referrals").textContent = jobs.length ? `Referrals (${jobs.length})` : "Referrals";
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  referralPage = Math.min(Math.max(referralPage, 0), pages - 1);
  renderPager($("referralspager"), referralPage, pages, jobs.length, "referral(s)", (p) => {
    referralPage = p;
    renderJobs(lastJobs);
  });
  if (!jobs.length) {
    emptyRow(body, 7, "No referrals yet - use Referral on a shortlist row.");
    return;
  }
  const start = referralPage * PAGE_SIZE;
  jobs.slice(start, start + PAGE_SIZE).forEach((job) => {
    const row = document.createElement("tr");
    row.dataset.jobId = job.job_id;
    const add = rowCellAdder(row);

    add(job.company);
    add(job.title);
    add(job.history_contact || "-");
    const asked = add(
      job.history_marked_at ? new Date(job.history_marked_at).toLocaleDateString() : "-"
    );
    if (job.history_marked_at) asked.title = job.history_marked_at;
    const state = add(
      job.history_status === "referral_sent" ? "sent ✓" : "pending",
      "status-" + job.history_status
    );
    if (job.history_how === "similar") {
      state.title = "Matched by company+title from your history";
    }
    row.appendChild(linksCell(job));
    row.appendChild(referralActionsCell(job));

    row.addEventListener("click", () => selectJob(job.job_id));
    body.appendChild(row);
  });
}

function selectJob(jobId) {
  const job = jobsById[jobId];
  if (!job) return;
  currentJobId = jobId;
  // Name the collapsed panel; open/closed state is the user's, changed only
  // by a direct row click (see the row listeners).
  $("jobdetailname").textContent = ` — ${job.company} · ${job.title}`;
  act("jobinfo").forEach((button) => (button.disabled = false));
  document.querySelectorAll("#jobs tbody tr, #referrals tbody tr").forEach((row) => {
    row.classList.toggle("selected", row.dataset.jobId === jobId);
  });

  const parts = [
    `${job.company} - ${job.title}`,
    `Relevance ${job.relevance}: ${job.why_score || ""}`,
  ];
  if (job.resume_pdf_error) parts.push(`PDF compile failed: ${job.resume_pdf_error}`);
  if (job.latex_skip_reason) parts.push(`LaTeX skipped: ${job.latex_skip_reason}`);
  if (job.resume_edit_suggestions) parts.push(`\nResume changes\n${job.resume_edit_suggestions}`);
  if (job.interview_prep) parts.push(`\nInterview prep\n${job.interview_prep}`);
  $("detail").textContent = parts.join("\n");

  loadPreview(job);
}

let previewUrl = "";

async function loadPreview(job) {
  const frame = $("pdf");
  const note = $("pdfnote");
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
    previewUrl = "";
  }
  frame.removeAttribute("src");
  if (!job.resume_tex_file) {
    note.textContent = "This run has no tailored .tex for this job (PDF resume input).";
    return;
  }
  note.textContent = "Loading preview...";
  const url = `/api/runs/${currentStamp}/jobs/${encodeURIComponent(job.job_id)}/resume.pdf`;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      note.textContent = `No PDF preview: ${body.detail || res.statusText}. Open ${job.tex_path} instead.`;
      return;
    }
    previewUrl = URL.createObjectURL(await res.blob());
    frame.src = previewUrl;
    note.textContent = "";
  } catch (err) {
    note.textContent = `No PDF preview: ${err.message}`;
  }
}

async function decide(jobId, decision) {
  try {
    await postJSON(`/api/runs/${currentStamp}/jobs/${encodeURIComponent(jobId)}/decision`, {
      decision,
    });
    await loadJobs(currentStamp);
  } catch (err) {
    alert(`Could not save decision: ${err.message}`);
  }
}

async function loadJobs(stamp) {
  if (!stamp) return;
  if (stamp !== currentStamp) shortlistPage = referralPage = 0;
  currentStamp = stamp;
  try {
    const [run, data] = await Promise.all([
      getJSON(`/api/runs/${stamp}`),
      getJSON(`/api/runs/${stamp}/jobs`),
    ]);
    const bits = [
      run.status,
      `${run.raw_job_count || 0} scraped`,
      `${run.match_count || 0} shortlisted`,
      `${run.resume_pdf_count || 0} pdf`,
    ];
    if (run.status === "failed") bits.push(run.error_message || "");
    $("runinfo").textContent = bits.filter(Boolean).join(" | ");
    renderJobs(data.jobs || []);
  } catch (err) {
    $("runinfo").textContent = `Could not load run: ${err.message}`;
  }
}

async function loadStamps(preferred) {
  const data = await getJSON("/api/runs");
  const select = $("stamp");
  select.replaceChildren();
  (data.stamps || []).forEach((stamp) => {
    const option = document.createElement("option");
    option.value = stamp;
    option.textContent = stamp;
    select.appendChild(option);
  });
  const chosen = preferred && (data.stamps || []).includes(preferred) ? preferred : data.stamps[0];
  if (chosen) {
    select.value = chosen;
    await loadJobs(chosen);
  } else {
    $("runinfo").textContent = "No runs yet. Start one above.";
  }
}

let runSource = null;

function streamRunLogs(stamp) {
  if (runSource) runSource.close();
  runSource = new EventSource(`/api/runs/${stamp}/logs`);
  runSource.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    if (event.type === "done") {
      runSource.close();
      runSource = null;
      finishRun(stamp, event.text);
      return;
    }
    appendLog($("log"), event.text);
  };
  runSource.onerror = () => {
    if (runSource) {
      runSource.close();
      runSource = null;
    }
    $("status").textContent = "log stream disconnected";
    $("start").disabled = false;
  };
}

async function finishRun(stamp, outcome) {
  $("status").textContent = `run ${outcome}`;
  $("start").disabled = false;
  await loadStamps(stamp);
}

async function startRun() {
  $("start").disabled = true;
  $("status").textContent = "starting...";
  setLog($("log"), "");
  const maxJobs = parseInt($("maxjobs").value, 10);
  try {
    const data = await postJSON("/api/runs", {
      resume_path: $("resume").value.trim(),
      max_detail_jobs: Number.isFinite(maxJobs) ? maxJobs : null,
    });
    $("status").textContent = `running ${data.stamp}`;
    streamRunLogs(data.stamp);
  } catch (err) {
    $("status").textContent = `could not start: ${err.message}`;
    $("start").disabled = false;
  }
}

async function resumeActiveRun() {
  const state = await getJSON("/api/runs/status");
  if (state.active) {
    $("start").disabled = true;
    $("status").textContent = `running ${state.stamp}`;
    setLog($("log"), "");
    streamRunLogs(state.stamp);
  }
}

let applySessionId = "";
// The job the LIVE SESSION is applying to - not necessarily the row the user
// last clicked (currentJobId), which can change mid-session.
let applyJobId = "";
let applySource = null;

function setChatEnabled(enabled) {
  $("chat").disabled = !enabled;
  $("send").disabled = !enabled;
  $("abort").disabled = !enabled;
  $("park").disabled = !enabled;
  $("attachresume").disabled = !enabled;
  $("attachletter").disabled = !enabled;
  if (!enabled) showDraftTools("");
}

// A drafted answer used to arrive in a one-line box: unreadable without
// dragging the cursor to the end. The box grows to the text instead.
const CHAT_MAX_HEIGHT = 260;
let lastDraft = "";

function growChat() {
  const box = $("chat");
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight + 2, CHAT_MAX_HEIGHT)}px`;
  const text = box.value;
  $("chatcount").textContent = text.length > 80 ? `${text.length} characters` : "";
}

function showDraftTools(draft) {
  lastDraft = draft || "";
  for (const id of ["redraft", "restoredraft"]) {
    $(id).hidden = !lastDraft;
    $(id).disabled = !lastDraft;
  }
}

function fillChat(text, draft) {
  const box = $("chat");
  box.value = text;
  showDraftTools(draft === undefined ? lastDraft : draft);
  growChat();
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

// While applying, the user is usually in the OTHER window (the Playwright
// Chrome), so a question in the chat pane goes unseen. Surface every prompt
// through an OS notification, a short beep, and a tab-title flag, whenever
// this tab does not have focus.
const baseTitle = document.title;

function alertUser(text) {
  if (document.hasFocus()) return;
  document.title = "(!) waiting for you - " + baseTitle;
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      const n = new Notification("Apply agent needs you", {
        body: (text || "").slice(0, 140),
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    }
  } catch {}
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = 880;
    gain.gain.value = 0.08;
    osc.start();
    osc.stop(ctx.currentTime + 0.2);
    osc.onended = () => ctx.close();
  } catch {}
}

window.addEventListener("focus", () => {
  document.title = baseTitle;
});

// Liveness: the server sends a ping event every few seconds. After a laptop
// sleep or an hour in the background the stream can die silently (no error
// event), leaving the chat armed to a question the page had outlived.
let lastApplyEvent = 0;

async function resyncApply(reason) {
  if (!applySessionId) return;
  try {
    const state = await getJSON("/api/apply/status");
    const dead = ["applied", "failed", "aborted", "closed", "parked", "idle"];
    if (state.session_id !== applySessionId || dead.includes(state.status)) {
      if (applySource) applySource.close();
      applySource = null;
      applySessionId = "";
      applyJobId = "";
      setChatEnabled(false);
      appendApply(`--- the session is over (${reason}); Start apply begins a new one ---`);
      loadJobs(currentStamp);
      return;
    }
    // Alive: replay the transcript from the server so what is on screen is
    // what the agent is actually waiting for.
    resetApply(`(reconnected: ${reason})`);
    setChatEnabled(true);
    streamApply(state.session_id);
  } catch {}
}

setInterval(() => {
  if (applySessionId && lastApplyEvent && Date.now() - lastApplyEvent > 45000) {
    lastApplyEvent = Date.now();
    resyncApply("no word from the server for 45s");
  }
}, 15000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && applySessionId) resyncApply("tab back in front");
});

function streamApply(sessionId) {
  if (applySource) applySource.close();
  applySessionId = sessionId;
  lastApplyEvent = Date.now();
  applySource = new EventSource(`/api/apply/${sessionId}/events`);
  applySource.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    lastApplyEvent = Date.now();
    if (event.type === "ping") return;
    const prefix = {
      question: "AGENT ASKS: ",
      choice: "AGENT ASKS: ",
      history: "AGENT ASKED: ",  // an already-answered prompt, replayed
      answer: "YOU: ",
      error: "ERROR: ",
      done: "SESSION ",
    }[event.type] || "";
    appendApply(prefix + event.text, event.at);
    if (event.type === "question" || event.type === "choice") alertUser(event.text);
    if (event.type === "question") {
      // A model-drafted answer arrives pre-filled for editing; never clobber
      // something the user already started typing.
      if (event.suggestion && !$("chat").value.trim()) {
        fillChat(event.suggestion, event.suggestion);
        appendApply(
          "(the draft is in the box below - read it, edit it, press Enter to send; " +
            "'Ask for changes' redrafts it)"
        );
      } else {
        showDraftTools("");
      }
      $("chat").focus();
    }
    if (event.type === "choice") openAttachModal(event);
    if (event.type === "done") {
      applySource.close();
      applySource = null;
      applySessionId = "";
      applyJobId = "";
      closeAttachModal();
      setChatEnabled(false);
      appendApply("--- session ended; the chat is closed. If you finished the application " +
          "yourself, use Mark applied on the row. Start apply begins a new session. ---"
      );
      loadJobs(currentStamp);
      followQueue();
    }
  };
  applySource.onerror = () => {
    if (applySource) {
      applySource.close();
      applySource = null;
    }
    if (!applySessionId) return;
    // Reconnect if the session is still alive; otherwise unlock the UI so
    // Start apply works again instead of "already running" forever.
    const sid = applySessionId;
    setTimeout(async () => {
      if (applySessionId !== sid || applySource) return;
      try {
        const state = await getJSON("/api/apply/status");
        const dead = ["applied", "failed", "aborted", "closed", "parked", "idle"];
        if (state.session_id === sid && !dead.includes(state.status)) {
          streamApply(sid);
        } else {
          applySessionId = "";
          applyJobId = "";
          setChatEnabled(false);
          appendApply("--- connection lost and the session is over; Start apply begins a new one ---");
          loadJobs(currentStamp);
        }
      } catch {}
    }, 1500);
  };
}

// The worker opens this modal mid-session (a "choice" event) when the form
// asks for a resume or a cover letter. Replies go back through the normal
// chat endpoint using the __use__ / __revise__ sentinels.
let choiceKind = "";

function openAttachModal(event) {
  choiceKind = event.kind || "";
  const meta = event.meta || {};
  const isResume = choiceKind === "resume";
  $("rmtitle").textContent = isResume ? "Which resume should I attach?" : "Review the cover letter";

  const changelog = meta.changelog || "";
  $("rmchanges").hidden = !isResume || !changelog;
  $("rmchanges").textContent = changelog;

  $("rmlinks").hidden = !isResume;
  const tailored = $("rmviewtailored");
  if (isResume && meta.tailored_path) {
    tailored.href = `/api/runs/${currentStamp}/jobs/${encodeURIComponent(applyJobId)}/resume.pdf`;
    tailored.hidden = false;
  } else {
    tailored.hidden = true;
  }

  const text = isResume ? meta.tailored_source || "" : meta.text || "";
  $("rmtext").value = text;
  // The letter is meant to be edited; the resume source hides behind a toggle.
  $("rmtext").hidden = isResume;
  $("rmedittoggle").hidden = !isResume || !text;
  $("rmtoggleedit").textContent = "Edit source";
  $("rmreviserow").hidden = isResume ? true : false;
  $("rminstruction").value = "";

  $("rmusetailored").hidden = !isResume;
  $("rmusedefault").hidden = !isResume;
  $("rmuseedited").hidden = !isResume;
  $("rmuse").hidden = isResume;
  $("rmusetailored").disabled = isResume && !meta.tailored_path;
  $("rmusedefault").disabled = isResume && !meta.default_path;

  const warn = isResume
    ? meta.tailored_error
      ? `Tailored resume: ${meta.tailored_error}`
      : meta.pages > 1
        ? `Warning: the tailored resume is ${meta.pages} pages.`
        : ""
    : meta.error || "";
  $("rmwarn").textContent = warn;
  $("modalback").hidden = false;
}

function closeAttachModal() {
  $("modalback").hidden = true;
  choiceKind = "";
}

async function sendChoice(text) {
  closeAttachModal();
  try {
    await postJSON(`/api/apply/${applySessionId}/chat`, { text });
  } catch (err) {
    appendApply(`Could not send: ${err.message}`);
    // "busy" after a long break usually means the screen is stale: resync.
    if (/busy/i.test(err.message || "")) resyncApply("the agent's state had moved on");
  }
}

async function startApply(jobId) {
  if (applySessionId) {
    alert("An apply session is already running. Finish or abort it first.");
    return;
  }
  // Ask once, on a user gesture, so prompts can reach the user while they
  // are over in the apply browser window.
  if ("Notification" in window && Notification.permission === "default") {
    try {
      Notification.requestPermission();
    } catch {}
  }
  selectJob(jobId);
  resetApply("Starting apply session...");
  showApplyCard();
  try {
    const sess = await postJSON("/api/apply/start", {
      stamp: currentStamp,
      job_id: jobId,
    });
    applyJobId = jobId;
    setChatEnabled(true);
    streamApply(sess.session_id);
    await loadJobs(currentStamp);
  } catch (err) {
    appendApply(`Could not start: ${err.message}`);
  }
}

async function sendChat() {
  const text = $("chat").value.trim();
  if (!text || !applySessionId) return;
  fillChat("", "");
  try {
    await postJSON(`/api/apply/${applySessionId}/chat`, { text });
  } catch (err) {
    appendApply(`Could not send: ${err.message}`);
    fillChat(text);  // keep what was typed
    if (/busy/i.test(err.message || "")) resyncApply("the agent's state had moved on");
  }
}

async function abortApply() {
  if (!applySessionId) return;
  try {
    await postJSON(`/api/apply/${applySessionId}/abort`, {});
  } catch (err) {
    // The session is probably already gone; unlock the UI regardless so the
    // user is never stuck unable to abort or start fresh.
    appendApply(`Could not abort (${err.message}); resetting.`);
    if (applySource) {
      applySource.close();
      applySource = null;
    }
    applySessionId = "";
    applyJobId = "";
    setChatEnabled(false);
    loadJobs(currentStamp);
  }
}

// ---------------------------------------------------------------- the queue
// Apply to the ticked jobs one after another. The agent still never submits:
// every job stops for the candidate to review and submit it themselves. The
// queue only removes the walk back to the table between jobs.

function refreshQueueButton() {
  const n = queueTicks.size;
  const running = queueState.active || !!applySessionId;
  act("startqueue").forEach((button) => {
    button.disabled = n < 1 || running;
    button.textContent = n ? `Start queue (${n})` : "Start queue";
  });
  // No cap: how many forms you can read properly in one sitting is your
  // call. The count is shown so it is a deliberate number, not a slip.
  let hint = "";
  if (running) hint = "a session is running";
  else if (n) hint = `${n} ticked - each one stops for you to submit`;
  role("queuehint").forEach((el) => (el.textContent = hint));
  act("jobinfo").forEach((button) => (button.disabled = !currentJobId));
}

function renderQueueStrip() {
  const strip = $("queuestrip");
  const { current, pending, parked, done, note } = queueState;
  const anything = current || pending.length || parked.length || done.length || note;
  strip.hidden = !anything;
  if (!anything) return;
  strip.replaceChildren();

  const chip = (text, cls) => {
    const span = document.createElement("span");
    span.className = "qjob" + (cls ? " " + cls : "");
    span.textContent = text;
    strip.appendChild(span);
  };
  const label = document.createElement("strong");
  label.textContent = "Queue";
  strip.appendChild(label);

  done.forEach((job) => chip(`${job.label || job.job_id} - ${job.status}`,
                             job.status === "applied" ? "was-applied" : ""));
  parked.forEach((job) => chip(`${job.label || job.job_id} - parked`, "was-parked"));
  if (current) chip(`${current.label || current.job_id} - now`, "now");
  pending.forEach((job) => chip(job.label || job.job_id));
  if (note) {
    const span = document.createElement("span");
    span.className = "qnote";
    span.textContent = note;
    strip.appendChild(span);
  }
  if (pending.length) {
    const clear = document.createElement("button");
    clear.textContent = `Clear ${pending.length} queued`;
    clear.title = "Drop the jobs still waiting. The running one is not touched.";
    clear.addEventListener("click", async () => {
      try {
        queueState = await postJSON("/api/apply/queue/clear", {});
        renderQueueStrip();
        refreshQueueButton();
      } catch (err) {
        appendApply(`Could not clear the queue: ${err.message}`);
      }
    });
    strip.appendChild(clear);
  }
}

function toggleJobInfo() {
  const panel = $("jobdetail");
  panel.open = !panel.open;
  if (panel.open) panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadQueue() {
  try {
    queueState = await getJSON("/api/apply/queue");
  } catch {
    return;
  }
  renderQueueStrip();
  refreshQueueButton();
}

async function startQueue() {
  const items = [];
  queueTicks.forEach((jobId) => {
    const job = jobsById[jobId];
    if (job) items.push({ stamp: currentStamp, job_id: jobId, label: `${job.company} ${job.title}`.trim() });
  });
  if (!items.length) return;
  if ("Notification" in window && Notification.permission === "default") {
    try {
      Notification.requestPermission();
    } catch {}
  }
  resetApply(`Queue: ${items.length} job(s). Each one stops for you to review and submit.`);
  showApplyCard();
  try {
    queueState = await postJSON("/api/apply/queue", { items });
  } catch (err) {
    appendApply(`Could not start the queue: ${err.message}`);
    return;
  }
  queueTicks.clear();
  renderQueueStrip();
  await attachToCurrentSession();
  await loadJobs(currentStamp);
}

async function attachToCurrentSession() {
  const state = await getJSON("/api/apply/status");
  const dead = ["applied", "failed", "aborted", "closed", "parked", "idle"];
  if (state.session_id && !dead.includes(state.status)) {
    applyJobId = state.job_id || "";
    setChatEnabled(true);
    streamApply(state.session_id);
    return true;
  }
  return false;
}

// After a queued job ends, the NEXT one cannot start until its browser has
// closed - Chrome allows one instance per profile, and a submitted
// application keeps the window up for a few seconds so the confirmation page
// is readable. So the page waits for a new session id rather than assuming
// one is already there.
async function followQueue() {
  await loadQueue();
  if (!queueState.active) return;
  appendApply("--- queue: waiting for the browser to close, then opening the next job ---");
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((done) => setTimeout(done, 1000));
    if (applySessionId) return;                 // something else attached first
    if (await attachToCurrentSession()) {
      await loadQueue();
      await loadJobs(currentStamp);
      return;
    }
    await loadQueue();
    if (!queueState.active) {
      appendApply("--- queue: nothing left to open ---");
      return;
    }
  }
  appendApply("--- queue: the next job did not open; use Start apply on a row ---");
}

async function resumeActiveApply() {
  const state = await getJSON("/api/apply/status");
  // Only a LIVE session is re-armed; a finished one used to be replayed and
  // its chat re-enabled on every page load.
  const dead = ["applied", "failed", "aborted", "closed", "parked", "idle"];
  if (state.session_id && !dead.includes(state.status)) {
    applyJobId = state.job_id || "";
    resetApply("");
    setChatEnabled(true);
    streamApply(state.session_id);
  }
}

function showView(name) {
  $("view-jobs").hidden = name !== "jobs";
  $("view-referrals").hidden = name !== "referrals";
  document.querySelectorAll("#nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
}

document.querySelectorAll("#nav button").forEach((btn) => {
  btn.addEventListener("click", () => showView(btn.dataset.view));
});

$("rmusetailored").addEventListener("click", () => sendChoice("tailored"));
$("rmusedefault").addEventListener("click", () => sendChoice("default"));
$("rmuseedited").addEventListener("click", () => sendChoice("__use__\n" + $("rmtext").value));
$("rmuse").addEventListener("click", () => sendChoice("__use__\n" + $("rmtext").value));
$("rmskip").addEventListener("click", () => sendChoice("skip"));
$("rmrevise").addEventListener("click", () => {
  const instruction = $("rminstruction").value.trim();
  if (!instruction) return;
  sendChoice("__revise__ " + instruction);
});
$("rmtoggleedit").addEventListener("click", () => {
  const box = $("rmtext");
  box.hidden = !box.hidden;
  $("rmtoggleedit").textContent = box.hidden ? "Edit source" : "Hide source";
});
// A modal answer is required: closing it without choosing would leave the
// worker blocked, so Esc and backdrop clicks skip the attachment instead.
$("modalback").addEventListener("click", (ev) => {
  if (ev.target === $("modalback")) sendChoice("skip");
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("modalback").hidden) sendChoice("skip");
});

$("attachresume").addEventListener("click", () => sendChoice("attach resume"));
$("attachletter").addEventListener("click", () => sendChoice("cover letter"));

$("start").addEventListener("click", startRun);
$("stamp").addEventListener("change", (ev) => loadJobs(ev.target.value));
$("refresh").addEventListener("click", () => loadStamps(currentStamp));
$("send").addEventListener("click", sendChat);
$("abort").addEventListener("click", abortApply);
act("startqueue").forEach((b) => b.addEventListener("click", startQueue));
act("jobinfo").forEach((b) => b.addEventListener("click", toggleJobInfo));
$("park").addEventListener("click", () => {
  if (!applySessionId) return;
  fillChat("", "");
  postJSON(`/api/apply/${applySessionId}/chat`, { text: "park" }).catch((err) =>
    appendApply(`Could not park: ${err.message}`)
  );
});
$("chat").addEventListener("keydown", (ev) => {
  // Enter sends; Shift+Enter starts a new line, so a paragraph answer can be
  // written without the box swallowing it.
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    sendChat();
  }
});
$("chat").addEventListener("input", growChat);
$("redraft").addEventListener("click", () => {
  const box = $("chat");
  if (!/^\s*(llm|ai)\s*:/i.test(box.value)) {
    fillChat(`llm: ${box.value.trim() === lastDraft.trim() ? "" : box.value}`.trimEnd() + " ");
  }
  box.focus();
});
$("restoredraft").addEventListener("click", () => fillChat(lastDraft));

loadStamps().then(resumeActiveRun).then(resumeActiveApply).then(loadQueue);
