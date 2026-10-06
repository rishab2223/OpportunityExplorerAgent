// The Resume fit tab: a conversation on the left, the resume it produces on
// the right. The server (src/web/fit_chat.py) keeps the draft, checks every
// step and builds the preview; this file only shows it and sends what the
// candidate says or changes.
//
// Every request carries a sequence number, and an answer to an older one
// is dropped: a reorder sent twice in a row, or a preview racing a chat
// reply, must not put an older draft back on screen.

let fitDraft = null;
let fitPolicy = null;   // the saved policy, or null while runs use the default
let fitBusy = false;    // a chat turn is in flight
let fitPdfUrl = "";
let fitSeq = 0;

function fitSubject() {
  return $("fitsubject").value || "";
}

// Sends a request; the result is returned only if nothing newer was sent.
async function fitRequest(method, url, payload) {
  const seq = ++fitSeq;
  const data = await sendJSON(method, url, payload);
  return seq === fitSeq ? data : null;
}

// ------------------------------------------------------------------ render

function fitRenderState() {
  const pill = $("fitstate");
  if (fitPolicy) {
    const when = fitPolicy.agreed_at ? new Date(fitPolicy.agreed_at).toLocaleDateString() : "";
    pill.textContent = `Runs use your policy${when ? ` (saved ${when})` : ""}`;
    pill.className = "pill pill-good";
  } else {
    pill.textContent = "Runs use the default rule";
    pill.className = "pill";
  }
  $("fitdefault").hidden = !fitPolicy;
}

function fitRenderMessages() {
  const box = $("fitmessages");
  box.replaceChildren();
  (fitDraft.messages || []).forEach((m) => {
    box.append(el("div", { class: `fitmsg fitmsg-${m.role}` },
      el("span", { class: "fitwho", text: m.role === "you" ? "You" : "Assistant" }),
      el("div", { class: "fittext", text: m.text })));
  });
  if (fitBusy) {
    box.append(el("div", { class: "fitmsg fitmsg-assistant fitthinking" },
      el("span", { class: "fitwho", text: "Assistant" }),
      el("div", { class: "fittext" }, el("span", { class: "dots", "aria-label": "thinking" }))));
  }
  box.scrollTop = box.scrollHeight;
  $("fitready").hidden = !fitDraft.ready_to_save || fitBusy;
}

const FIT_KIND = { layout: "layout", drop_item: "drop a bullet", drop_section: "drop a section",
                   replace_section: "fixed shorter section" };

function fitRenderSteps() {
  const list = $("fitsteps");
  list.replaceChildren();
  const steps = fitDraft.steps || [];
  const applied = new Set(((fitDraft.last_preview || {}).applied) || []);
  if (!steps.length) {
    list.append(el("li", { class: "fitstep-empty muted",
                           text: "No steps: a resume that runs long is left long and flagged." }));
  }
  steps.forEach((step, i) => {
    const tools = el("span", { class: "fitsteptools" },
      el("button", { type: "button", "data-move": "up", "data-i": i, title: "Earlier",
                     "aria-label": `Move "${step.label}" earlier`, disabled: i === 0, text: "↑" }),
      el("button", { type: "button", "data-move": "down", "data-i": i, title: "Later",
                     "aria-label": `Move "${step.label}" later`, disabled: i === steps.length - 1, text: "↓" }),
      el("button", { type: "button", "data-remove": i, title: "Remove this step",
                     "aria-label": `Remove "${step.label}"`, text: "×" }));
    const body = el("div", { class: "fitstepbody" },
      el("span", { class: "fitsteplabel", text: step.label }),
      el("span", { class: `fitkind fitkind-${step.kind}`, text: FIT_KIND[step.kind] || step.kind }),
      applied.has(step.label) ? el("span", { class: "fitused", text: "used in the preview" }) : null,
      step.kind === "replace_section" && step.latex_body
        ? el("details", { class: "fitbody" }, el("summary", { text: "show the fixed text" }),
             el("pre", { text: step.latex_body.trim() }))
        : null);
    list.append(el("li", { class: "fitstep" }, body, tools));
  });
  const guidance = (fitDraft.guidance || "").trim();
  $("fitguidance").hidden = !guidance;
  $("fitguidance").textContent = guidance ? `Rules for the tailoring model: ${guidance}` : "";
}

function fitRenderPreview() {
  const result = fitDraft.last_preview;
  const trail = $("fittrail");
  trail.replaceChildren();
  const pages = $("fitpages");
  const frame = $("fitpdf");
  if (!result) {
    pages.textContent = "";
    pages.className = "pill";
    $("fitpdfnote").textContent = "Pick a resume to try the policy on.";
    return;
  }
  pages.textContent = result.pages ? `${result.pages} page${result.pages === 1 ? "" : "s"}` : "not compiled";
  pages.className = `pill ${result.pages === 1 ? "pill-good" : "pill-bad"}`;
  (result.trail || []).forEach((t, i) => {
    trail.append(el("li", { class: t.pages === 1 ? "ok" : "" },
      el("span", { text: i === 0 ? "As written" : t.step }),
      el("b", { text: `${t.pages} p` })));
  });
  const notes = [];
  if (result.note) notes.push(result.note);
  if ((result.problems || []).length) notes.push(`Not used: ${result.problems.join("; ")}`);
  if (result.trail && result.trail.length === 1 && result.pages === 1) {
    notes.push("This one already fits, so no step was needed. Pick one marked “ran long” to see the policy work.");
  }
  $("fitpdfnote").textContent = notes.join(" ");
  if (!result.pdf) {
    // A failed compile must not leave the previous resume up as if it were this one.
    fitPdfUrl = "";
    frame.removeAttribute("src");
  } else if (result.pdf !== fitPdfUrl) {
    fitPdfUrl = result.pdf;
    frame.src = result.pdf;
  }
}

function fitRenderSubjects(subjects, chosen) {
  const select = $("fitsubject");
  select.replaceChildren();
  if (!subjects.length) {
    select.append(el("option", { value: "", text: "No resume to try it on yet - start a run first" }));
    return;
  }
  const groups = { true: el("optgroup", { label: "Ran long - the policy has work to do" }),
                   false: el("optgroup", { label: "Fit already" }) };
  subjects.forEach((s) => {
    groups[Boolean(s.overflowed)].append(
      el("option", { value: s.id, text: s.overflowed ? `${s.label} — ran long` : s.label }));
  });
  [groups.true, groups.false].forEach((g) => { if (g.children.length) select.append(g); });
  if (chosen && subjects.some((s) => s.id === chosen)) select.value = chosen;
}

function fitRender() {
  if (!fitDraft) return;
  fitRenderState();
  fitRenderMessages();
  fitRenderSteps();
  fitRenderPreview();
  ["fitsend", "fitinput", "fitsave", "fitsubject", "fitrestart", "fitdefault"].forEach((id) => {
    $(id).disabled = fitBusy;
  });
  $("fitsteps").querySelectorAll("button").forEach((b) => { if (fitBusy) b.disabled = true; });
}

// ------------------------------------------------------------------ actions

async function loadFit() {
  try {
    const data = await fitRequest("GET", "/api/fit");
    if (!data) return;
    fitDraft = data.draft;
    fitPolicy = data.policy;
    fitRenderSubjects(data.subjects, fitDraft.subject);
    fitRender();
    if (!fitDraft.last_preview && fitSubject()) fitPreview();
  } catch (err) {
    $("fitmessages").textContent = `Could not open Resume fit: ${err.message}`;
  }
}

async function fitPreview() {
  if (!fitDraft || !fitSubject()) return;
  $("fitpdfnote").textContent = "Compiling…";
  try {
    const data = await fitRequest("POST", "/api/fit/preview",
      { steps: fitDraft.steps, guidance: fitDraft.guidance, subject: fitSubject() });
    if (!data) return;
    fitDraft = data;
    fitRender();
  } catch (err) {
    $("fitpdfnote").textContent = `Could not build the preview: ${err.message}`;
  }
}

async function fitChat(ev) {
  ev.preventDefault();
  const text = $("fitinput").value.trim();
  if (!text || fitBusy || !fitDraft) return;
  fitBusy = true;
  fitDraft.messages = [...(fitDraft.messages || []), { role: "you", text }];
  $("fitinput").value = "";
  fitRender();
  try {
    const data = await fitRequest("POST", "/api/fit/chat", { text, subject: fitSubject() });
    if (data) fitDraft = data;
  } catch (err) {
    fitDraft.messages.push({ role: "assistant", text: `(Could not answer: ${err.message})` });
    $("fitinput").value = text;
  } finally {
    fitBusy = false;
    fitRender();
    $("fitinput").focus();
  }
}

async function fitSave() {
  if (!fitDraft) return;
  try {
    const data = await fitRequest("POST", "/api/fit/policy", { steps: fitDraft.steps, guidance: fitDraft.guidance });
    if (!data) return;
    fitPolicy = data.policy;
    fitDraft = data.draft;
    fitRender();
  } catch (err) {
    $("fitpdfnote").textContent = `Could not save: ${err.message}`;
  }
}

async function fitRestart() {
  if (fitBusy || !confirm("Clear this conversation and start again? Your saved policy stays as it is.")) return;
  try {
    const data = await fitRequest("DELETE", "/api/fit/draft");
    if (!data) return;
    fitDraft = data.draft;
    fitPolicy = data.policy;
    fitRender();
    fitPreview();
  } catch (err) {
    $("fitpdfnote").textContent = `Could not start over: ${err.message}`;
  }
}

async function fitBackToDefault() {
  if (fitBusy || !confirm("Forget your saved policy? Runs go back to dropping the CCNA bullet, then the Certifications section.")) return;
  try {
    const data = await fitRequest("DELETE", "/api/fit/policy");
    if (!data) return;
    fitPolicy = data.policy;
    fitRender();
  } catch (err) {
    $("fitpdfnote").textContent = `Could not forget the policy: ${err.message}`;
  }
}

$("fitform").addEventListener("submit", fitChat);
$("fitinput").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    $("fitform").requestSubmit();
  }
});
$("fitsave").addEventListener("click", fitSave);
$("fitrestart").addEventListener("click", fitRestart);
$("fitdefault").addEventListener("click", fitBackToDefault);
$("fitsubject").addEventListener("change", fitPreview);
$("fitsteps").addEventListener("click", (ev) => {
  const button = ev.target.closest("button");
  if (!button || fitBusy || !fitDraft) return;
  const steps = [...fitDraft.steps];
  if (button.dataset.remove !== undefined) {
    steps.splice(Number(button.dataset.remove), 1);
  } else if (button.dataset.move) {
    const i = Number(button.dataset.i);
    const j = button.dataset.move === "up" ? i - 1 : i + 1;
    [steps[i], steps[j]] = [steps[j], steps[i]];
  } else {
    return;
  }
  fitDraft.steps = steps;
  fitDraft.ready_to_save = false;
  fitRenderSteps();
  fitPreview();
});
