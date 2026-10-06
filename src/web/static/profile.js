// The Profile page and the user menu that opens it.
//
// Every value is built with DOM calls, never innerHTML: the file is edited by
// hand and its text is not markup. Only the keys that changed are sent on
// Save, so a value the server's checks would reject cannot block an
// unrelated edit.

let profileData = null;      // what the server last returned
let profileChanges = {};     // key -> new value, unsaved
let profileJobs = [];        // working copy of the jobs list

const profileEl = el;   // app.js's builder

function profileDirty() {
  return Object.keys(profileChanges).length > 0;
}

function setProfileState(text, kind) {
  const state = $("profilestate");
  state.textContent = text;
  state.dataset.kind = kind || "";
}

function refreshProfileButtons() {
  const n = Object.keys(profileChanges).length;
  $("profilesave").disabled = n === 0;
  $("profilediscard").disabled = n === 0;
  if (n) setProfileState(`${n} unsaved change${n === 1 ? "" : "s"}`, "dirty");
}

function noteChange(key, value) {
  const saved = profileData.values[key];
  const same = JSON.stringify(saved ?? "") === JSON.stringify(value);
  if (same) delete profileChanges[key];
  else profileChanges[key] = value;
  refreshProfileButtons();
  countEmpty();
}

function currentValue(key) {
  return key in profileChanges ? profileChanges[key] : profileData.values[key];
}

function isEmptyValue(value) {
  return Array.isArray(value) ? value.length === 0 : !String(value ?? "").trim();
}

// The agent stops to ask about an empty field when a form wants it, so the
// page says how many there are and marks each one.
function countEmpty() {
  const keys = [];
  profileData.sections.forEach((section) => {
    if (section.title === "Other") return;
    section.fields.forEach((field) => { if (isEmptyValue(currentValue(field.key))) keys.push(field); });
  });
  const box = $("profileempty");
  box.hidden = keys.length === 0;
  // A handful of names, then a count: a new profile has dozens, and each is
  // marked on the page anyway.
  const named = keys.slice(0, 5).map((f) => f.label).join(", ");
  const more = keys.length > 5 ? ` and ${keys.length - 5} more` : "";
  box.textContent = keys.length
    ? `${keys.length} empty (${named}${more}), marked below. ` +
      "When a form asks for one of these, the agent stops and asks you."
    : "";
  // Keyed rows only: the boxes inside a job entry are parts of one value.
  document.querySelectorAll("#profileform .pfield[data-key]").forEach((row) => {
    row.classList.toggle("empty", isEmptyValue(currentValue(row.dataset.key)));
  });
}

function fieldInput(field, value) {
  const id = "pf-" + field.key;
  const kind = field.kind || "text";
  let input;
  if (kind === "long") {
    input = profileEl("textarea", { id, rows: 3 });
    input.value = value ?? "";
  } else {
    input = profileEl("input", {
      id, type: kind === "email" ? "email" : kind === "url" ? "url" : "text",
      inputmode: kind === "number" ? "decimal" : null,
      autocomplete: "off",
    });
    input.value = value ?? "";
    if (kind.startsWith("choice:")) {
      // Suggestions, not a lock: an existing value off the list stays.
      input._choices = choiceList(input, kind.slice(7).split("|"));
    }
  }
  input.addEventListener("input", () => {
    noteChange(field.key, input.value);
    clearFieldError(field.key);
  });
  return input;
}

// The suggestions under a "choice" box. Not a <datalist>: Chrome draws that
// popup itself, and on this page it opened far from its box.
function choiceList(input, options) {
  const list = profileEl("ul", { class: "pchoices", id: input.id + "-choices", role: "listbox", hidden: true },
    options.map((option) => profileEl("li", { role: "option", text: option, "data-value": option })));
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  let active = -1;
  const items = () => [...list.children];
  const mark = () => items().forEach((li, i) => {
    li.classList.toggle("active", i === active);
    li.setAttribute("aria-selected", String(li.dataset.value === input.value));
  });
  const open = () => { list.hidden = false; input.setAttribute("aria-expanded", "true"); mark(); };
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); active = -1; };
  const pick = (value) => {
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    close();
  };
  // mousedown, not click: the box would lose focus first and close the list.
  list.addEventListener("mousedown", (event) => {
    const li = event.target.closest("li");
    if (!li) return;
    event.preventDefault();
    pick(li.dataset.value);
  });
  input.addEventListener("focus", open);
  input.addEventListener("click", open);
  input.addEventListener("blur", close);
  input.addEventListener("keydown", (event) => {
    const n = items().length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.hidden) open();
      active = event.key === "ArrowDown" ? (active + 1) % n : (active - 1 + n) % n;
      mark();
    } else if (event.key === "Enter" && !list.hidden && active >= 0) {
      event.preventDefault();
      pick(items()[active].dataset.value);
    } else if (event.key === "Escape") {
      close();
    }
  });
  return list;
}

function clearFieldError(key) {
  const slot = document.querySelector(`#profileform [data-error-for="${CSS.escape(key)}"]`);
  if (slot) slot.textContent = "";
}

function fieldRow(field) {
  const value = currentValue(field.key);
  if (field.kind === "jobs") return jobsEditor(field);
  const input = fieldInput(field, value);
  return profileEl("div", { class: "pfield", "data-key": field.key },
    profileEl("label", { for: input.id, text: field.label }),
    input._choices ? profileEl("div", { class: "pcombo" }, input, input._choices) : input,
    field.hint ? profileEl("div", { class: "phint", text: field.hint }) : null,
    profileEl("div", { class: "perror", "data-error-for": field.key, role: "alert" }));
}

// ----------------------------------------------------------- work history

const JOB_INPUTS = [
  ["title", "Title"], ["company", "Company"], ["location", "Location"],
  ["start", "Start (MM/YYYY)"], ["end", "End (MM/YYYY)"],
];

function jobsChanged() {
  noteChange("jobs", profileJobs.map((job) => ({ ...job })));
  clearFieldError("jobs");
}

function jobCard(job, index, rerender) {
  const head = profileEl("div", { class: "pjobhead" },
    profileEl("strong", { text: job.title || job.company ? `${job.title || "Untitled"}` +
      (job.company ? ` at ${job.company}` : "") : `Job ${index + 1}` }),
    profileEl("span", { class: "grow" }));
  const remove = profileEl("button", { type: "button", class: "btn-quiet", text: "Remove" });
  remove.addEventListener("click", () => {
    profileJobs.splice(index, 1);
    jobsChanged();
    rerender();
  });
  head.append(remove);

  const grid = profileEl("div", { class: "pjobgrid" });
  JOB_INPUTS.forEach(([key, label]) => {
    const id = `pj-${index}-${key}`;
    const input = profileEl("input", { id, type: "text", autocomplete: "off" });
    input.value = job[key] || "";
    if (key === "end") input.disabled = !!job.current;
    input.addEventListener("input", () => { job[key] = input.value; jobsChanged(); });
    grid.append(profileEl("div", { class: "pfield" }, profileEl("label", { for: id, text: label }), input));
  });

  const currentId = `pj-${index}-current`;
  const current = profileEl("input", { id: currentId, type: "checkbox" });
  current.checked = !!job.current;
  current.addEventListener("change", () => {
    job.current = current.checked;
    if (job.current) job.end = "";
    jobsChanged();
    rerender();
  });

  const descId = `pj-${index}-description`;
  const desc = profileEl("textarea", { id: descId, rows: 4 });
  desc.value = job.description || "";
  desc.addEventListener("input", () => { job.description = desc.value; jobsChanged(); });

  return profileEl("div", { class: "pjob" }, head, grid,
    profileEl("div", { class: "pcheck" }, current, profileEl("label", { for: currentId, text: "I currently work here" })),
    profileEl("div", { class: "pfield" }, profileEl("label", { for: descId, text: "Description" }), desc));
}

function jobsEditor(field) {
  const wrap = profileEl("div", { class: "pfield pjobs", "data-key": field.key });
  const list = profileEl("div", { class: "pjoblist" });
  const render = () => {
    list.replaceChildren(...profileJobs.map((job, i) => jobCard(job, i, render)));
    if (!profileJobs.length) list.append(profileEl("p", { class: "muted", text: "No jobs yet." }));
    countEmpty();
  };
  const add = profileEl("button", { type: "button", text: "Add job" });
  add.addEventListener("click", () => {
    // Most recent first, so a new job goes on top.
    profileJobs.unshift({ title: "", company: "", location: "", start: "", end: "", current: false, description: "" });
    jobsChanged();
    render();
  });
  wrap.append(profileEl("div", { class: "row" }, add), list,
    profileEl("div", { class: "perror", "data-error-for": field.key, role: "alert" }));
  render();
  return wrap;
}

// ------------------------------------------------------------- the page

function renderProfile() {
  profileChanges = {};
  profileJobs = (profileData.values.jobs || []).map((job) => ({ ...job }));
  const form = $("profileform");
  form.replaceChildren(...profileData.sections.map((section) =>
    profileEl("div", { class: "card psection" },
      profileEl("div", { class: "card-head" }, profileEl("h2", { text: section.title })),
      section.note ? profileEl("p", { class: "muted pnote", text: section.note }) : null,
      profileEl("div", { class: section.fields.length === 1 && section.fields[0].kind === "jobs" ? "" : "pgrid" },
        section.fields.map(fieldRow)))));
  $("profilenever").replaceChildren(
    ...profileData.never_stored.map((line) => profileEl("li", { text: line })));
  refreshProfileButtons();
  countEmpty();
}

async function loadProfile(force) {
  // Coming back to the page must not throw away edits made before leaving it.
  if (profileData && profileDirty() && !force) return;
  setProfileState("Loading...");
  try {
    profileData = await getJSON("/api/profile");
  } catch (err) {
    setProfileState("Could not load the profile: " + err.message, "bad");
    return;
  }
  renderProfile();
  setProfileState(profileData.exists ? "" : "No profile file yet: Save creates it.");
}

async function saveProfile() {
  if (!profileDirty()) return;
  $("profilesave").disabled = true;
  setProfileState("Saving...");
  const res = await fetch("/api/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: profileChanges }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const errors = (body.detail && body.detail.errors) || { "": body.detail || res.statusText };
    Object.entries(errors).forEach(([key, message]) => {
      const slot = document.querySelector(`#profileform [data-error-for="${CSS.escape(key)}"]`);
      if (slot) slot.textContent = message;
    });
    const n = Object.keys(errors).length;
    setProfileState(`Not saved: ${n} field${n === 1 ? " needs" : "s need"} fixing`, "bad");
    $("profilesave").disabled = false;
    const first = document.querySelector("#profileform .perror:not(:empty)");
    if (first) first.closest(".pfield").scrollIntoView({ block: "center", behavior: "smooth" });
    return;
  }
  profileData = body;
  renderProfile();
  setProfileState("Saved", "good");
}

$("profilesave").addEventListener("click", saveProfile);
$("profilediscard").addEventListener("click", () => {
  renderProfile();
  setProfileState("Changes discarded");
});
window.addEventListener("beforeunload", (ev) => {
  if (profileDirty()) { ev.preventDefault(); ev.returnValue = ""; }
});

// ------------------------------------------------------------ user menu

function setUserMenu(open) {
  $("usermenu").hidden = !open;
  $("userbtn").setAttribute("aria-expanded", String(open));
  if (open) $("usermenu").querySelector("[role=menuitem]").focus();
}

$("userbtn").addEventListener("click", (ev) => {
  ev.stopPropagation();
  setUserMenu($("usermenu").hidden);
});
document.querySelectorAll("#usermenu [data-open-view]").forEach((item) => {
  item.addEventListener("click", () => {
    setUserMenu(false);
    showView(item.dataset.openView);
  });
});
document.addEventListener("click", (ev) => {
  if (!$("usermenu").hidden && !ev.target.closest(".usermenu")) setUserMenu(false);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("usermenu").hidden) {
    setUserMenu(false);
    $("userbtn").focus();
  }
});
