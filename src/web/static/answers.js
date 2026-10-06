// The Saved answers page: the answer bank, readable and fixable.
//
// Built with DOM calls, never innerHTML: answers are text typed into forms.

let savedAnswers = [];
let editingKey = "";

const answerEl = el;   // app.js's builder

function shortDate(iso) {
  if (!iso) return "-";
  const when = new Date(iso);
  return isNaN(when) ? "-" : when.toLocaleDateString();
}

function answerMatches(entry, query) {
  if (!query) return true;
  const text = `${entry.question} ${entry.answer} ${entry.question_key}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).every((word) => text.includes(word));
}

function answerButton(label, title, onClick, className) {
  const btn = answerEl("button", { type: "button", text: label, title, class: className || null });
  btn.addEventListener("click", onClick);
  return btn;
}

function answerRow(entry) {
  const row = answerEl("tr", { "data-key": entry.question_key });
  const question = answerEl("td", { class: "aquestion" },
    answerEl("div", { text: entry.question || entry.question_key }),
    entry.question && entry.question !== entry.question_key
      ? answerEl("div", { class: "muted akey", text: entry.question_key }) : null);
  const answerCell = answerEl("td", { class: "aanswer" });
  const actions = answerEl("td", { class: "aactions" });

  if (editingKey === entry.question_key) {
    const box = answerEl("textarea", { rows: 2, "aria-label": `Answer to ${entry.question}` });
    box.value = entry.answer;
    const error = answerEl("div", { class: "perror", role: "alert" });
    const save = async () => {
      const res = await fetch(`/api/answers/${encodeURIComponent(entry.question_key)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: box.value }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        error.textContent = body.detail || res.statusText;
        return;
      }
      savedAnswers = body.answers || [];
      editingKey = "";
      renderAnswers();
      $("answersinfo").textContent = "Saved";
    };
    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); save(); }
      if (ev.key === "Escape") { editingKey = ""; renderAnswers(); }
    });
    answerCell.append(box, error);
    actions.append(
      answerButton("Save", "Save this answer (Enter)", save, "btn-primary"),
      answerButton("Cancel", "Keep the saved answer (Esc)", () => { editingKey = ""; renderAnswers(); }));
    setTimeout(() => box.focus(), 0);
  } else {
    answerCell.textContent = entry.answer;
    actions.append(
      answerButton("Edit", "Change this answer", () => { editingKey = entry.question_key; renderAnswers(); }),
      answerButton("Delete", "Forget it: the agent asks again next time a form wants it", async () => {
        if (!window.confirm(`Delete the saved answer to "${entry.question || entry.question_key}"?`)) return;
        const res = await fetch(`/api/answers/${encodeURIComponent(entry.question_key)}`, { method: "DELETE" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          $("answersinfo").textContent = `Could not delete: ${body.detail || res.statusText}`;
          return;
        }
        savedAnswers = body.answers || [];
        renderAnswers();
        $("answersinfo").textContent = "Deleted";
      }, "btn-quiet"));
  }

  const sensitive = entry.kind === "sensitive";
  row.append(question, answerCell,
    answerEl("td", {}, answerEl("span", {
      class: sensitive ? "akind sensitive" : "akind",
      text: sensitive ? "sensitive" : "general",
      title: sensitive ? "A legal declaration: every reuse is shown in the apply log" : null,
    })),
    answerEl("td", { class: "num", text: String(entry.times_used || 0) }),
    answerEl("td", { text: shortDate(entry.last_used), title: entry.last_used || null }),
    actions);
  return row;
}

function renderAnswers() {
  const query = $("answersearch").value.trim();
  const shown = savedAnswers.filter((entry) => answerMatches(entry, query));
  const body = document.querySelector("#answers tbody");
  body.replaceChildren(...shown.map(answerRow));
  if (!shown.length) {
    body.append(answerEl("tr", {}, answerEl("td", {
      colspan: 6, class: "muted",
      text: savedAnswers.length ? `No saved answer matches "${query}".`
        : "No saved answers yet. They are added as you answer questions during applications.",
    })));
  }
  $("answersinfo").textContent = query
    ? `${shown.length} of ${savedAnswers.length}` : `${savedAnswers.length} saved`;
}

async function loadAnswers() {
  // An edit in progress survives a trip to another view.
  if (editingKey) return;
  try {
    savedAnswers = (await getJSON("/api/answers")).answers || [];
  } catch (err) {
    $("answersinfo").textContent = "Could not load: " + err.message;
    return;
  }
  renderAnswers();
}

$("answersearch").addEventListener("input", () => {
  editingKey = "";
  renderAnswers();
});
