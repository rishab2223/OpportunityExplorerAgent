let currentStamp = "";
let currentJobId = "";
let jobsById = {};

const $ = (id) => document.getElementById(id);

function appendLog(box, text) {
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.textContent += (box.textContent ? "\n" : "") + text;
  if (atBottom) box.scrollTop = box.scrollHeight;
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
  const alreadyApplied = job.history_status === "applied";
  const apply = cellButton(cell, "Start apply", "", () => startApply(job.job_id));
  apply.disabled = alreadyApplied;
  if (alreadyApplied) apply.title = "Already applied (see history); Unmark to re-enable";
  cellButton(cell, "Skip", "", () => decide(job.job_id, "no"));
  cellButton(
    cell,
    alreadyApplied ? "Unmark" : "Mark applied",
    alreadyApplied
      ? "Remove from the applied history so future runs process this job again"
      : "Record as applied across runs; future scrapes will drop this job",
    () => setHistory(job.job_id, alreadyApplied ? "" : "applied")
  );
  if (!alreadyApplied) {
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
  }
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
  jobsById = {};
  jobs.forEach((job) => (jobsById[job.job_id] = job));
  renderShortlist(jobs.filter((j) => !REFERRAL_STATES.includes(j.history_status)));
  renderReferrals(jobs.filter((j) => REFERRAL_STATES.includes(j.history_status)));
  if (currentJobId && jobsById[currentJobId]) selectJob(currentJobId);
}

function renderShortlist(jobs) {
  const body = document.querySelector("#jobs tbody");
  body.replaceChildren();
  if (!jobs.length) {
    emptyRow(body, 8, "No shortlisted jobs left in this run.");
    return;
  }
  jobs.forEach((job) => {
    const row = document.createElement("tr");
    row.dataset.jobId = job.job_id;
    const add = rowCellAdder(row);

    add(job.company);
    add(job.title);
    add(job.relevance, scoreClass(job.relevance));
    add(job.location);
    row.appendChild(linksCell(job));
    row.appendChild(pathCell(job));
    const shownStatus =
      job.history_status === "applied" ? "applied ✓" : job.status || "pending";
    const statusCell = add(
      shownStatus,
      "status-" + (job.history_status === "applied" ? "applied" : job.status || "pending")
    );
    if (job.history_how === "similar") {
      statusCell.title = "Matched by company+title from your applied history";
    }
    row.appendChild(actionsCell(job));

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
  if (!jobs.length) {
    emptyRow(body, 7, "No referrals yet - use Referral on a shortlist row.");
    return;
  }
  jobs.forEach((job) => {
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
let applySource = null;

function setChatEnabled(enabled) {
  $("chat").disabled = !enabled;
  $("send").disabled = !enabled;
  $("abort").disabled = !enabled;
}

function streamApply(sessionId) {
  if (applySource) applySource.close();
  applySessionId = sessionId;
  applySource = new EventSource(`/api/apply/${sessionId}/events`);
  applySource.onmessage = (ev) => {
    const event = JSON.parse(ev.data);
    const prefix = { question: "AGENT ASKS: ", answer: "YOU: ", error: "ERROR: ", done: "SESSION " }[
      event.type
    ] || "";
    appendLog($("applylog"), prefix + event.text);
    if (event.type === "question") $("chat").focus();
    if (event.type === "done") {
      applySource.close();
      applySource = null;
      applySessionId = "";
      setChatEnabled(false);
      loadJobs(currentStamp);
    }
  };
  applySource.onerror = () => {
    if (applySource) {
      applySource.close();
      applySource = null;
    }
  };
}

async function startApply(jobId) {
  if (applySessionId) {
    alert("An apply session is already running. Finish or abort it first.");
    return;
  }
  selectJob(jobId);
  setLog($("applylog"), "Starting apply session...");
  try {
    const sess = await postJSON("/api/apply/start", { stamp: currentStamp, job_id: jobId });
    setChatEnabled(true);
    streamApply(sess.session_id);
    await loadJobs(currentStamp);
  } catch (err) {
    appendLog($("applylog"), `Could not start: ${err.message}`);
  }
}

async function sendChat() {
  const text = $("chat").value.trim();
  if (!text || !applySessionId) return;
  $("chat").value = "";
  try {
    await postJSON(`/api/apply/${applySessionId}/chat`, { text });
  } catch (err) {
    appendLog($("applylog"), `Could not send: ${err.message}`);
  }
}

async function abortApply() {
  if (!applySessionId) return;
  try {
    await postJSON(`/api/apply/${applySessionId}/abort`, {});
  } catch (err) {
    appendLog($("applylog"), `Could not abort: ${err.message}`);
  }
}

async function resumeActiveApply() {
  const state = await getJSON("/api/apply/status");
  if (state.session_id && state.status !== "idle") {
    setLog($("applylog"), "");
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

$("start").addEventListener("click", startRun);
$("stamp").addEventListener("change", (ev) => loadJobs(ev.target.value));
$("refresh").addEventListener("click", () => loadStamps(currentStamp));
$("send").addEventListener("click", sendChat);
$("abort").addEventListener("click", abortApply);
$("chat").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") sendChat();
});

loadStamps().then(resumeActiveRun).then(resumeActiveApply);
