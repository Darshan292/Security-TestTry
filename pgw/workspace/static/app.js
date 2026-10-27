/* HR Workspace UI. Vanilla JS; the DOM is built with createElement/textContent only (no HTML parsing of data).
 * Everything shown about a run comes from the workspace backend's response, which carries the gateway's own
 * report (stages with measured times, spans, placeholders, outbound text, checks, outcome). Animations replay those
 * real stages with a short minimum display time per stage; the real times are what is printed. */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const motion = () => !reduced.matches;
  const STAGE_MS = 360;          // minimum display time of a stage that ran
  const SKIP_MS = 170;           // ... of a stage that was skipped
  const SEG_MS = 240;            // connector draw before the next node starts
  const HISTORY_MAX = 25;
  const CLIENT_TIMEOUT_MS = 150000;

  const TASK_ICON = { ticket_summary: "i-doc", hr_case_explanation: "i-case", hr_email_draft: "i-mail",
                      salary_comparison: "i-scale", esi_letter: "i-heart", band_position: "i-band" };
  const NODE_OF = { received: 0, verify_source: 1, compute: 1, protect: 1, egress_check: 2, provider: 3, restore: 4 };
  const CATEGORY = { PERSON: "person", EMPLOYEE_ID: "id", PAN: "id", AADHAAR: "id", AADHAAR_LIKE: "id",
                     BANK_ACCOUNT: "id", IFSC: "id", CARD: "id", UPI_ID: "id", IDENTIFIER: "id", SECRET: "id",
                     COMPENSATION: "money", DERIVED: "money", DATE_OF_BIRTH: "date", EMAIL: "contact", PHONE: "contact" };
  const TYPE_LABEL = { PERSON: "Name", EMPLOYEE_ID: "Employee ID", PAN: "PAN", AADHAAR: "Aadhaar",
                       AADHAAR_LIKE: "Aadhaar-like number", BANK_ACCOUNT: "Bank account", IFSC: "IFSC code",
                       CARD: "Card number", UPI_ID: "UPI ID", IDENTIFIER: "Reference", SECRET: "Secret",
                       COMPENSATION: "Pay", DERIVED: "Computed value", DATE_OF_BIRTH: "Date of birth", EMAIL: "Email",
                       PHONE: "Phone" };
  const ACTION_TEXT = { alias: "replaced with a placeholder", remove: "removed before sending",
                        withhold: "withheld, never sent", generalize: "generalised before sending",
                        allow: "found, sent unchanged (allowed by the contract)" };
  const PLACEHOLDER = /\[([A-Z][A-Z0-9_]*?)(?:_([0-9A-Z]{6,8}))?\]/g;
  const WITHHELD_WORD = "(withheld)";

  const state = { session: null, task: null, running: false, history: [], current: null, seq: 0, token: 0,
                  people: {} };

  // --- DOM helpers ------------------------------------------------------------------------------------------
  function el(tag, opts, ...kids) {
    const n = document.createElement(tag);
    if (opts) {
      if (opts.cls) n.className = opts.cls;
      if (opts.text != null) n.textContent = String(opts.text);
      if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) if (v != null) n.setAttribute(k, String(v));
    }
    for (const k of kids) if (k != null) n.append(k);
    return n;
  }
  function icon(name, cls) {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS(SVGNS, "use");
    u.setAttribute("href", "#" + name);
    s.append(u);
    return s;
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, motion() ? ms : 0));
  const fmtInt = (n) => Math.round(n).toLocaleString("en-IN");
  function fmtMs(v) {
    if (v == null || !isFinite(v)) return "–";
    return v < 10 ? v.toFixed(1) + " ms" : fmtInt(v) + " ms";
  }
  function inr(n) {
    const s = String(Math.abs(Math.trunc(n)));
    const head = s.slice(0, -3), tail = s.slice(-3);
    const groups = head ? head.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + "," : "";
    return (n < 0 ? "-" : "") + "₹" + groups + tail;
  }
  function countUp(node, value, fmt) {
    if (value == null || !isFinite(value)) { node.textContent = fmt(value); return; }
    if (!motion()) { node.textContent = fmt(value); return; }
    const start = performance.now(), dur = 520;
    const step = (now) => {
      const p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 3);
      node.textContent = fmt(value * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function enter(node) {
    node.classList.remove("enter");
    void node.offsetWidth;
    node.classList.add("enter");
  }

  // --- chips --------------------------------------------------------------------------------------------------
  function chip(text, category, opts) {
    const c = el("span", { cls: "chip chip-" + (category || "other") + (opts && opts.ph ? " ph" : "") },
                 el("span", { cls: "chip-text", text }));
    if (opts && opts.title) { c.title = opts.title; c.setAttribute("aria-label", text + " (" + opts.title + ")"); }
    return c;
  }
  function morph(c, text, toPh, delay) {
    const apply = () => {
      const t = c.firstChild;
      if (!motion()) { t.textContent = text; c.classList.toggle("ph", toPh); return; }
      const w0 = c.getBoundingClientRect().width;
      c.classList.add("swap");
      setTimeout(() => {
        t.textContent = text;
        c.classList.toggle("ph", toPh);
        c.style.width = "auto";
        const w1 = c.getBoundingClientRect().width;
        c.style.width = w0 + "px";
        void c.offsetWidth;
        c.style.width = w1 + "px";
        c.classList.remove("swap");
        setTimeout(() => { c.style.width = ""; }, 480);
      }, 210);
    };
    if (delay && motion()) setTimeout(apply, delay); else apply();
  }
  function placeholderType(name, code) {
    if (code) return name;                       // [PERSON_7K2Q9D] -> PERSON
    return CATEGORY[name] ? name : null;         // [PAN] -> PAN (withheld marker); other brackets are plain text
  }
  /** Text with chips for every placeholder-shaped token; `original(ph)` gives the value to show first, if known. */
  function markPlaceholders(text, original) {
    const frag = document.createDocumentFragment(), chips = [];
    let last = 0;
    PLACEHOLDER.lastIndex = 0;
    for (let m; (m = PLACEHOLDER.exec(text));) {
      const type = placeholderType(m[1], m[2]);
      if (!type) continue;
      frag.append(text.slice(last, m.index));
      const withheld = !m[2];
      const orig = original ? original(m[0]) : null;
      const cat = withheld ? "withheld" : (CATEGORY[type] || "other");
      const title = (TYPE_LABEL[type] || type) + (withheld ? ": withheld, never sent" : ": placeholder");
      const c = chip(orig != null ? orig : m[0], orig != null && withheld ? (CATEGORY[type] || "other") : cat,
                     { ph: orig == null, title });
      chips.push({ chip: c, ph: m[0], orig, cat });
      frag.append(c);
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return { frag, chips };
  }

  // --- session & task picker --------------------------------------------------------------------------------
  async function loadSession() {
    let r;
    try {
      r = await fetch("/api/session", { credentials: "same-origin", cache: "no-store" });
    } catch (e) {
      showInputError("The workspace could not be reached. Check your connection and reload.");
      return;
    }
    if (r.status === 401) { expired(); return; }
    if (!r.ok) { showInputError("The workspace could not load its configuration. Reload to try again."); return; }
    const s = await r.json();
    state.session = s;
    $("user-name").textContent = s.user;
    if (s.development) {
      $("dev-banner").hidden = false;
      $("dev-banner-text").textContent = "Signed in as " + s.user + " (local development user). Use synthetic data only.";
    }
    buildTasks(s.tasks);
  }

  function buildTasks(tasks) {
    const groups = { text: $("tasks-text"), form: $("tasks-form") };
    for (const t of tasks) {
      const input = el("input", { attrs: { type: "radio", name: "task", value: t.id, "aria-describedby": "route-note" } });
      input.addEventListener("change", () => selectTask(t));
      const tile = el("label", { cls: "tile" }, input, icon(TASK_ICON[t.id] || "i-doc"),
                      el("span", { cls: "tile-title", text: t.title }));
      tile.title = t.summary;
      groups[t.kind].append(tile);
    }
    const first = groups.text.querySelector("input");
    if (first) { first.checked = true; selectTask(tasks.find((t) => t.id === first.value)); }
  }

  function selectTask(t) {
    state.task = t;
    hideInputError();
    const note = $("route-note");
    note.replaceChildren(icon(t.kind === "text" ? "i-home" : "i-lock"), el("span", { text: t.route_note }));
    $("text-input").hidden = t.kind !== "text";
    $("form-input").hidden = t.kind !== "form";
    if (t.kind === "text") {
      $("text-label").textContent = t.input_label;
      const ta = $("text");
      ta.placeholder = t.placeholder;
      ta.maxLength = t.max_chars;
      updateCounter();
      const rows = $("people-rows");
      rows.replaceChildren();
      for (const r of t.roles.filter((r) => r.required)) addPerson(r.id);
      updateRequires();
      $("run-hint").textContent = "";
    } else {
      buildForm(t);
      const off = !state.session.forms_enabled;
      $("forms-disabled").hidden = !off;
      $("run-hint").textContent = off ? "" : "Signed by the workspace as the form's source.";
    }
    $("run").disabled = t.kind === "form" && !state.session.forms_enabled;
  }

  function updateCounter() {
    const t = state.task, n = $("text").value.length, c = $("counter");
    if (!t || t.kind !== "text") return;
    c.textContent = fmtInt(n) + " / " + fmtInt(t.max_chars);
    c.classList.toggle("near", n > t.max_chars * 0.9 && n <= t.max_chars);
    c.classList.toggle("over", n > t.max_chars);
  }

  // --- people ---------------------------------------------------------------------------------------------------
  const PERSON_FIELDS = [["name", "Full name", "text", "p-name"], ["employee_id", "Employee ID", "text", "p-id"],
                         ["email", "Email", "email", "p-email"], ["phone", "Phone", "tel", "p-phone"]];
  function addPerson(roleId) {
    const t = state.task, rows = $("people-rows");
    const idx = rows.children.length;
    const sel = el("select", { cls: "p-role", attrs: { "aria-label": "Role", "data-path": "people[" + idx + "].role" } });
    for (const r of t.roles) {
      const o = el("option", { text: r.label, attrs: { value: r.id } });
      if (r.id === roleId) o.selected = true;
      sel.append(o);
    }
    sel.addEventListener("change", updateRequires);
    const row = el("div", { cls: "person" }, sel);
    for (const [f, label, type, cls] of PERSON_FIELDS) {
      const i = el("input", { cls, attrs: { type, placeholder: label, "aria-label": label, autocomplete: "off",
                                            spellcheck: "false", "data-field": f } });
      i.addEventListener("input", updateRequires);
      row.append(i);
    }
    const rm = el("button", { cls: "btn btn-ghost btn-icon remove", attrs: { type: "button", "aria-label": "Remove person" } },
                  icon("i-x"));
    rm.addEventListener("click", () => { row.remove(); renumberPeople(); updateRequires(); });
    row.append(rm);
    rows.append(row);
    renumberPeople();
    return row;
  }
  function renumberPeople() {
    [...$("people-rows").children].forEach((row, i) => {
      row.querySelector("select").setAttribute("data-path", "people[" + i + "].role");
      for (const inp of row.querySelectorAll("input")) inp.setAttribute("data-path", "people[" + i + "]." + inp.dataset.field);
    });
  }
  function collectPeople() {
    const out = [];
    for (const row of $("people-rows").children) {
      const p = { role: row.querySelector("select").value };
      let any = false;
      for (const inp of row.querySelectorAll("input")) {
        if (inp.value.trim()) { p[inp.dataset.field] = inp.value; any = true; }
      }
      if (any) out.push(p);
    }
    return out;
  }
  function updateRequires() {
    const t = state.task, box = $("people-requires");
    box.replaceChildren();
    if (!t || t.kind !== "text") return;
    const present = new Set(collectPeople().map((p) => p.role));
    const req = t.roles.filter((r) => r.required);
    if (!req.length) return;
    box.append(el("span", { text: "Contract requires" }));
    for (const r of req) {
      const met = present.has(r.id);
      box.append(el("span", { cls: "req" + (met ? " met" : ""), attrs: { title: met ? "Provided" : "Not provided yet" } },
                    icon(met ? "i-ok" : "i-dash"), el("span", { text: r.label })));
    }
  }

  // --- forms ------------------------------------------------------------------------------------------------------
  function buildForm(t) {
    const box = $("form-fields");
    box.replaceChildren();
    const instr = el("select", { attrs: { id: "f-instruction", "data-path": "instruction" } });
    for (const c of t.choices) instr.append(el("option", { text: c.text, attrs: { value: c.id } }));
    box.append(el("div", { cls: "form-field full" }, el("label", { cls: "label", text: "What should the AI write?",
                                                                  attrs: { for: "f-instruction" } }), instr,
                  el("span", { cls: "hint", text: "A fixed list: forms never carry free text." })));
    box.lastChild.classList.add("form-group-lead");
    const groups = new Map();
    for (const f of t.fields) {
      if (!groups.has(f.group)) {
        const fs = el("fieldset", { cls: "form-group" }, el("legend", { text: f.group }));
        groups.set(f.group, fs);
        box.append(fs);
      }
      const id = "f-" + f.path.replace(/[^a-z0-9]/gi, "-");
      let control;
      if (f.kind === "band") {
        control = el("select", { attrs: { id, "data-path": f.path } });
        control.append(el("option", { text: "Choose a band", attrs: { value: "" } }));
        for (const b of t.bands) control.append(el("option", { text: b.id + " · " + inr(b.min) + " to " + inr(b.max),
                                                             attrs: { value: b.id } }));
      } else if (f.kind === "amount") {
        const inp = el("input", { attrs: { id, type: "text", inputmode: "numeric", autocomplete: "off",
                                           "data-path": f.path, "aria-describedby": id + "-hint" } });
        const hint = el("span", { cls: "hint", text: f.hint, attrs: { id: id + "-hint" } });
        inp.addEventListener("input", () => {
          const digits = inp.value.replace(/[^0-9]/g, "");
          hint.textContent = digits ? inr(Number(digits)) + " · " + f.hint.toLowerCase() : f.hint;
        });
        control = el("div", { cls: "money" }, inp);
        groups.get(f.group).append(el("div", { cls: "form-field" + (f.group === "Band" ? " full" : "") },
                                      el("label", { cls: "label", text: f.label, attrs: { for: id } }), control, hint));
        continue;
      } else {
        control = el("input", { attrs: { id, type: "text", autocomplete: "off", spellcheck: "false",
                                         "data-path": f.path, maxlength: f.kind === "name" ? 100 : 20 } });
      }
      groups.get(f.group).append(el("div", { cls: "form-field" + (f.kind === "name" || f.kind === "band" ? " full" : "") },
                                    el("label", { cls: "label", text: f.label, attrs: { for: id } }), control));
    }
  }
  function collectForm() {
    const t = state.task, out = { instruction: $("f-instruction").value };
    for (const f of t.fields) {
      const node = document.querySelector('[data-path="' + CSS.escape(f.path) + '"]');
      let v = node ? node.value : "";
      if (f.kind === "amount") {
        const digits = v.replace(/[\s,₹]/g, "");
        v = /^[0-9]+$/.test(digits) ? Number(digits) : v;
      }
      out[f.path] = v;
    }
    return out;
  }

  // --- input errors -------------------------------------------------------------------------------------------
  function showInputError(msg, path) {
    const e = $("input-error");
    e.textContent = msg;
    e.hidden = false;
    if (path) {
      const node = document.querySelector('[data-path="' + CSS.escape(path) + '"]') || (path === "text" ? $("text") : null);
      if (node) { node.setAttribute("aria-invalid", "true"); node.focus(); }
    }
  }
  function hideInputError() {
    $("input-error").hidden = true;
    for (const n of document.querySelectorAll('[aria-invalid="true"]')) n.removeAttribute("aria-invalid");
  }
  function fieldName(path) {
    const t = state.task;
    if (path === "text") return t.input_label;
    if (path === "instruction") return "Instruction";
    const f = (t.fields || []).find((x) => x.path === path);
    if (f) return f.group + " · " + f.label;
    const m = /^people\[(\d+)\]\.?(\w*)/.exec(path || "");
    if (m) return "Person " + (Number(m[1]) + 1) + (m[2] ? " · " + m[2].replace("_", " ") : "");
    return "Input";
  }

  // --- run ------------------------------------------------------------------------------------------------------
  async function run() {
    if (state.running || !state.task) return;
    hideInputError();
    const t = state.task;
    const body = { task: t.id };
    if (t.kind === "text") {
      body.text = $("text").value;
      if (!body.text.trim()) { showInputError("Paste some text first.", "text"); return; }
      body.people = collectPeople();
    } else {
      body.form = collectForm();
    }
    setRunning(true);
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), CLIENT_TIMEOUT_MS);
    let resp, data;
    try {
      resp = await fetch("/api/run", { method: "POST", credentials: "same-origin", cache: "no-store", signal: ctl.signal,
                                      headers: { "Content-Type": "application/json", "X-PGW-Workspace": "1" },
                                      body: JSON.stringify(body) });
      data = await resp.json().catch(() => null);
    } catch (e) {
      data = null;
      resp = null;
    } finally {
      clearTimeout(timer);
    }
    if (resp && resp.status === 401) { setRunning(false); expired(); return; }
    if (resp && resp.status === 400 && data && data.error && data.error.type === "invalid_input") {
      setRunning(false);
      resetResults();
      showInputError(fieldName(data.error.field) + ": " + data.error.message + ".", data.error.field);
      return;
    }
    if (!data || !data.view) data = localFailure(resp, data);
    const entry = { id: ++state.seq, at: new Date(), task: t, data };
    state.history.unshift(entry);
    if (state.history.length > HISTORY_MAX) state.history.pop();
    renderHistory(entry.id);
    try {
      await present(entry, true);
    } finally {
      setRunning(false);
    }
  }

  /** A failure before the gateway answered (workspace unreachable, refused, timed out): shown, never hidden. */
  function localFailure(resp, data) {
    const err = (data && data.error) || {};
    const plain = !resp ? { title: "No response from the workspace",
                            plain: "The workspace did not answer. Check your connection and try again." }
      : resp.status === 413 ? { title: "Too long", plain: "This is longer than the workspace accepts. Shorten it and try again." }
      : resp.status === 503 && err.type === "forms_disabled" ? { title: "Forms not enabled",
                            plain: "Structured tasks are not enabled on this workspace. Contact your administrator." }
      : { title: "Request not processed", plain: "The workspace refused this request (" + resp.status + "). Reload the page and try again." };
    return { ok: false, http_status: resp ? resp.status : 0, round_trip_ms: null, answer: null,
             error: { type: err.type || "workspace_error", title: plain.title, plain: plain.plain, sent: false, detail: null },
             view: { available: false, outcome: "error", stages: [], spans: [], fields: [], checks: [], outbound: null,
                     counts: { by_type: {} }, total_ms: null }, input: null };
  }

  function setRunning(on) {
    state.running = on;
    const b = $("run");
    b.classList.toggle("is-running", on);
    b.disabled = on || (state.task && state.task.kind === "form" && !state.session.forms_enabled);
    $("run-label").textContent = on ? "Running" : "Run";
    $("results").setAttribute("aria-busy", on ? "true" : "false");
    if (on) {
      state.token++;
      resetResults();
      setNode(0, "active");
      $("journey-note").textContent = "Waiting for the privacy gateway…";
      $("journey-note").hidden = false;
      if (window.matchMedia && window.matchMedia("(max-width: 1040px)").matches) {
        $("results").scrollIntoView({ behavior: motion() ? "smooth" : "auto", block: "start" });
      }
      for (const id of ["body-typed", "body-saw", "body-back"]) $(id).replaceChildren(skeleton());
    }
  }
  function skeleton() {
    return el("div", { cls: "skeleton", attrs: { "aria-hidden": "true" } },
              el("span"), el("span"), el("span"), el("span"), el("span"));
  }

  // --- journey ------------------------------------------------------------------------------------------------
  function setNode(i, s) { $("node-" + i).setAttribute("data-state", s); }
  function drawSeg(i, on) {
    for (const id of i < 2 ? ["seg-" + i] : ["seg-" + i + "o", "seg-" + i + "i"]) {
      const p = $(id);
      if (p) p.classList.toggle("drawn", on);
    }
  }
  function resetResults() {
    const j = $("journey");
    j.setAttribute("data-route", "none");
    j.removeAttribute("data-outcome");
    for (let i = 0; i < 5; i++) setNode(i, "idle");
    for (let i = 0; i < 4; i++) drawSeg(i, false);
    setModel("none");
    $("steps").replaceChildren();
    $("timing").hidden = true;
    $("outcome").hidden = true;
    $("checks-card").hidden = true;
    $("copy").disabled = true;
    $("tech-body").replaceChildren(el("p", { cls: "empty", text: "Run a task to see the gateway's report." }));
    for (const id of ["meta-typed", "meta-saw"]) $(id).textContent = "";
    $("title-saw").textContent = "What the AI saw";
    for (const id of ["body-typed", "body-saw", "body-back"]) $(id).replaceChildren(el("p", { cls: "empty", text: "Nothing yet." }));
  }
  function setModel(where) {
    const use = $("model-icon");
    use.setAttribute("href", where === "in_house" ? "#i-building" : "#i-cloud");
    $("model-label").textContent = where === "in_house" ? "In-house model" : where === "external" ? "External AI" : "AI";
  }
  function routeOf(view) {
    const o = view.outbound;
    if ((o && o.destination === "in_house") || view.route === "PRIVATE_LOCAL" || view.outcome === "kept_inside") return "in_house";
    if (view.route || o || view.outcome === "sent") return "external";
    return "none";
  }

  /** On PRIVATE_LOCAL the gateway reports verify_source "failed" and continues in-house: shown as diverted. */
  function shown(st, view) {
    return st.id === "verify_source" && st.status === "failed" && view.outcome === "kept_inside" ? "diverted" : st.status;
  }
  function stageText(st, run) {
    const v = run.data.view, c = v.counts || {}, src = v.input_source || {}, o = v.outbound || {};
    const inHouse = routeOf(v) === "in_house";
    const err = run.data.error;
    const failed = st.status === "failed";
    const stopped = failed && err ? "Stopped here: " + err.title + "." : "Stopped here.";
    switch (st.id) {
      case "received":
        return ["Request received", failed ? stopped : "The gateway identified the workspace and the task's data contract" +
                (v.contract ? " (" + v.contract + ")." : ".")];
      case "verify_source":
        if (failed && v.outcome === "kept_inside") return ["Source check", "Not signed by an approved source system, so the gateway kept it inside the company."];
        if (failed) return ["Source check", stopped];
        if (st.status === "skipped") return ["Source check", "Not needed for this request."];
        return ["Source check", src.verified ? "Signed by an approved source: " + (src.id || "verified") + "."
          : "Not signed by an approved source system, so it stays inside the company."];
      case "compute":
        if (st.status === "skipped") return ["Calculation inside the gateway", "Not needed for this task."];
        return ["Calculation inside the gateway", failed ? stopped : "Worked out from the exact values; they stay inside."];
      case "protect":
        if (st.status === "skipped") return ["Privacy shield", inHouse ? "Not needed: nothing leaves the company." : "Not needed."];
        return ["Privacy shield", failed ? stopped : (c.replaced != null ? fmtInt(c.replaced) + " replaced with placeholders" : "Personal details replaced") +
                (c.removed ? ", " + fmtInt(c.removed) + " removed or withheld." : ".")];
      case "egress_check":
        if (st.status === "skipped") return ["Final check", inHouse ? "Not needed: nothing leaves the company." : "Not needed."];
        return ["Final check", failed ? stopped : "The exact outgoing text was checked for every value found or declared."];
      case "provider":
        if (st.status === "skipped") return [inHouse ? "In-house model" : "External AI", "Not reached."];
        return [inHouse ? "In-house model" : "External AI", failed ? stopped : inHouse ? "Answered inside the company."
          : "Sent protected" + (o.provider ? " to " + o.provider + (o.model ? " · " + o.model : "") : "") + "."];
      case "restore": {
        const r = v.restore || {};
        if (st.status === "skipped") return ["Back to you", "Not needed."];
        return ["Back to you", failed ? stopped : inHouse ? "Answer checked before it was shown." :
                (r.restored != null ? fmtInt(r.restored) + " real details put back" : "Real details put back") +
                (r.kept_withheld ? "; " + fmtInt(r.kept_withheld) + " stay withheld." : ".")];
      }
      default:
        return [st.id.replace(/_/g, " "), st.status];
    }
  }
  function stepRow(st, run) {
    const [title, text] = stageText(st, run);
    const ms = el("span", { cls: "step-ms", text: st.status === "skipped" ? "skipped" : "" });
    const li = el("li", { cls: "step", attrs: { "data-state": "idle", "data-stage": st.id } },
                  el("span", { cls: "step-icon" }, icon("i-dash")),
                  el("div", null, el("div", { cls: "step-title", text: title }), el("div", { cls: "step-text", text })),
                  ms);
    li._ms = ms;
    return li;
  }
  function finishStep(li, st, animate, view) {
    const s = shown(st, view);
    li.setAttribute("data-state", s);
    li.querySelector(".step-icon").replaceChildren(icon(s === "done" ? "i-ok" : s === "failed" ? "i-x" : s === "diverted" ? "i-home" : "i-dash"));
    if (st.status !== "skipped") {
      if (animate) countUp(li._ms, st.ms, fmtMs); else li._ms.textContent = fmtMs(st.ms);
    }
  }

  // --- presenting a run -------------------------------------------------------------------------------------
  async function present(run, animate) {
    const token = ++state.token;
    const live = () => token === state.token;
    animate = animate && motion();
    resetResults();
    state.current = run;
    const d = run.data, v = d.view, route = routeOf(v);
    const j = $("journey");
    j.setAttribute("data-route", route);
    j.setAttribute("data-outcome", v.outcome);
    setModel(route);
    $("title-saw").textContent = route === "in_house" ? "What the in-house model saw" : "What the AI saw";
    $("timing").hidden = false;
    $("t-gateway").textContent = "–";
    $("t-round").textContent = "–";
    renderTyped(run);
    $("body-saw").replaceChildren(skeleton());
    $("body-back").replaceChildren(skeleton());

    const stages = v.stages || [];
    const note = $("journey-note");
    if (!v.available) {
      note.hidden = false;
      note.textContent = d.input ? "The gateway did not include its transparency report for this request, so individual steps are not shown. The outcome below is what its response proves."
        : "The request did not reach the privacy gateway.";
    } else {
      note.hidden = true;
    }
    const steps = $("steps");
    const rows = stages.map((st) => stepRow(st, run));
    steps.replaceChildren(...rows);
    const last = new Map();
    stages.forEach((st, i) => { if (NODE_OF[st.id] != null) last.set(NODE_OF[st.id], i); });
    const nodeStates = new Map();
    let sawShown = false, backShown = false;
    let saw = null, back = null;

    const showSaw = (phase) => { if (!sawShown) { saw = renderSaw(run, phase); sawShown = true; } };
    const showBack = () => { if (!backShown) { back = renderBack(run, animate); backShown = true; } };

    for (let i = 0; i < stages.length; i++) {
      if (!live()) return;
      const st = stages[i], n = NODE_OF[st.id], li = rows[i];
      if (n != null && !nodeStates.has(n)) { nodeStates.set(n, []); setNode(n, "active"); }
      li.setAttribute("data-state", "active");
      if (st.id === "protect" && st.status === "done") showSaw(animate ? "values" : "final");
      if (st.id === "provider" && st.status !== "skipped") showSaw("final");
      if (animate) await wait(st.status === "skipped" ? SKIP_MS : STAGE_MS);
      if (!live()) return;
      finishStep(li, st, animate, v);
      if (n != null) nodeStates.get(n).push(shown(st, v) === "diverted" ? "done" : st.status);
      if (st.id === "protect" && st.status === "done" && saw) saw.toPlaceholders();
      if (st.id === "provider" && st.status === "done") showBack();
      if (st.id === "restore" && st.status === "done") { showBack(); if (back) back.toValues(); }
      if (n != null && last.get(n) === i) {
        const ss = nodeStates.get(n);
        const ns = ss.includes("failed") ? "failed" : ss.every((x) => x === "skipped") ? "skipped" : "done";
        setNode(n, ns);
        const more = stages.slice(i + 1).some((x) => NODE_OF[x.id] != null && NODE_OF[x.id] > n);
        if (ns !== "failed" && more && n < 4) {   // draw only the connector to the next node
          drawSeg(n, true);
          if (animate) await wait(SEG_MS);
        }
      }
    }
    if (!live()) return;
    showSaw("final");
    showBack();
    if (back) back.toValues(true);
    $("results").setAttribute("aria-busy", "false");
    if (v.total_ms != null) countUp($("t-gateway"), v.total_ms, fmtMs); else $("t-gateway").textContent = "not reported";
    if (d.round_trip_ms != null) countUp($("t-round"), d.round_trip_ms, fmtMs);
    renderOutcome(run, animate);
    renderChecks(v);
    renderTech(run);
    $("copy").disabled = typeof d.answer !== "string";
  }

  // --- panes ---------------------------------------------------------------------------------------------------
  function originals(run) {
    /** placeholder -> the user's own value, from the report's spans (offsets into the text) or form fields. */
    const map = new Map(), d = run.data, v = d.view;
    if (d.input && d.input.kind === "text") {
      for (const s of v.spans || []) if (s.placeholder) map.set(s.placeholder, d.input.text.slice(s.start, s.end));
    } else if (d.input && d.input.kind === "form") {
      const rows = new Map(d.input.rows.map((r) => [r.path, r]));
      for (const f of v.fields || []) {
        const r = rows.get(f.field);
        if (f.placeholder && r) map.set(f.placeholder, r.kind === "amount" ? inr(r.value) : String(r.value));
      }
    }
    return map;
  }

  function renderTyped(run) {
    const d = run.data, v = d.view, body = $("body-typed"), meta = $("meta-typed");
    if (!d.input) { body.replaceChildren(el("p", { cls: "empty", text: "Not available." })); return; }
    if (d.input.kind === "text") {
      const text = d.input.text, spans = (v.spans || []).filter((s) => s.message === d.user_message_index);
      const frag = document.createDocumentFragment();
      let last = 0;
      for (const s of spans) {
        frag.append(text.slice(last, s.start));
        const what = ACTION_TEXT[s.action] || "found";
        const cat = s.action === "remove" || s.action === "withhold" ? "withheld" : s.category;
        frag.append(chip(text.slice(s.start, s.end), cat, { title: s.label + ": " + what +
                         (s.source === "declared" ? " (declared person)" : "") }));
        last = s.end;
      }
      frag.append(text.slice(last));
      body.replaceChildren(frag);
      meta.textContent = fmtInt(text.length) + " characters" + (spans.length ? " · " + spans.length + " found" : "");
    } else {
      const fields = new Map((v.fields || []).map((f) => [f.field, f]));
      const box = el("div", { cls: "rows" });
      let group = null;
      for (const r of d.input.rows) {
        if (r.group && r.group !== group) { group = r.group; box.append(el("div", { cls: "row-group", text: group })); }
        const val = r.kind === "amount" ? inr(r.value) : String(r.value);
        const f = fields.get(r.path);
        let shown;
        if (f) {
          const withheld = f.action === "withhold" || f.action === "remove";
          shown = chip(val, withheld ? "withheld" : f.category, { title: f.label + ": " + (ACTION_TEXT[f.action] || "protected") });
        } else {
          shown = el("span", { text: val });
        }
        box.append(el("div", { cls: "kv" }, el("span", { cls: "k", text: r.label }), el("span", null, shown)));
      }
      const computed = (v.fields || []).filter((f) => /^outputs\./.test(f.field));
      if (computed.length) {
        box.append(el("div", { cls: "row-group", text: "Computed inside the gateway" }));
        const what = { alias: ["sent as a placeholder", null], allow: ["sent as is", "other"], withhold: ["stays inside", "withheld"],
                       remove: ["stays inside", "withheld"], generalize: ["sent generalised", null] };
        for (const f of computed) {
          const [label, cat] = what[f.action] || [f.action || "reported", "other"];
          box.append(el("div", { cls: "kv" }, el("span", { cls: "k", text: f.field.slice(8).replace(/_/g, " ") }),
                        el("span", null, chip(label, cat || f.category, { title: "Computed output: " + (ACTION_TEXT[f.action] || label) }))));
        }
      }
      const instr = (v.fields || []).filter((f) => f.field === "instruction").length;
      if (instr) box.append(el("p", { cls: "pane-note", text: instr + " value(s) in the instruction were protected." }));
      body.replaceChildren(box);
      meta.textContent = "Form · signed by the workspace";
    }
    enter(body);
  }

  function renderSaw(run, phase) {
    const d = run.data, v = d.view, body = $("body-saw"), meta = $("meta-saw"), o = v.outbound;
    const route = routeOf(v);
    const result = { toPlaceholders() {} };
    if (route === "in_house" && o) {
      // The gateway does not echo what went in-house (it is the unprotected input): show where it went instead.
      const kids = [el("p", { cls: "pane-note in", text: "Kept inside. Your text went only to the company's in-house model and was not sent to an external AI." }),
                    dl([["Model", o.model || "not reported", true], ["Body size", o.bytes != null ? fmtInt(o.bytes) + " bytes" : null, true],
                        ["SHA-256", o.sha256, true]])];
      if (d.private_model === "standin") {
        kids.push(el("p", { cls: "pane-note", text: "This in-house endpoint is the development stand-in: it extracts sentences and is not a language model." }));
      }
      body.replaceChildren(...kids);
      meta.textContent = "In-house";
      enter(body);
      return result;
    }
    if (!o || !o.messages || !o.messages.length) {
      const nothing = v.outcome === "refused" || (d.error && d.error.sent === false);
      body.replaceChildren(el("p", { cls: "pane-note" + (nothing ? " stop" : ""),
        text: nothing ? "Nothing was sent to an AI." : o && o.sha256 ? "The gateway does not return the outgoing text on errors. It sent " + fmtInt(o.bytes || 0) + " bytes (SHA-256 in the technical details)."
          : v.available ? "Nothing left the gateway for this request."
          : "The gateway did not report the outgoing text for this request." }));
      enter(body);
      return result;
    }
    const users = o.messages.filter((m) => m.role === "user");
    const main = (users.length ? users[users.length - 1] : o.messages[o.messages.length - 1]).content;
    meta.textContent = (o.bytes != null ? fmtInt(o.bytes) + " bytes" : "") + (o.provider ? " · " + o.provider : "");
    const orig = originals(run);
    const valuesFirst = phase === "values";
    const { frag, chips } = markPlaceholders(main, valuesFirst ? (ph) => orig.get(ph) ?? null : null);
    const lead = v.route === "LOCAL_COMPUTE"
      ? "Computed inside the gateway: the AI received only placeholders and the computed facts, never the exact pay."
      : "Exactly what left the company. The gateway's own instructions to the AI are in the technical details.";
    body.replaceChildren(el("p", { cls: "pane-note", text: lead }), frag);
    enter(body);
    result.toPlaceholders = () => {
      chips.forEach((c, i) => { if (c.orig != null) morph(c.chip, c.ph, true, Math.min(i, 12) * 45); });
      chips.forEach((c) => { if (c.orig != null && c.cat === "withheld") c.chip.className = "chip chip-withheld ph"; });
    };
    return result;
  }

  function renderBack(run, animate) {
    const d = run.data, v = d.view, body = $("body-back");
    const result = { toValues() {} };
    if (typeof d.answer !== "string") {
      body.replaceChildren(el("p", { cls: "pane-note stop", text: v.outcome === "refused" ? "No answer: the request was stopped before anything was sent."
        : "No answer was returned." }));
      enter(body);
      return result;
    }
    // value -> placeholder for every value this request protected (the user's own data, already in this page)
    const pairs = [...originals(run).entries()].map(([ph, val]) => ({ ph, val })).filter((p) => p.val && p.val.length > 1);
    const typeOf = (ph) => { const m = /^\[([A-Z][A-Z0-9_]*?)_[0-9A-Z]{6,8}\]$/.exec(ph); return m ? m[1] : null; };
    const text = d.answer, found = [];
    for (const p of pairs) {
      for (let i = text.indexOf(p.val); i >= 0; i = text.indexOf(p.val, i + 1)) found.push({ s: i, e: i + p.val.length, p });
    }
    for (let i = text.indexOf(WITHHELD_WORD); i >= 0; i = text.indexOf(WITHHELD_WORD, i + 1)) {
      found.push({ s: i, e: i + WITHHELD_WORD.length, p: null });
    }
    found.sort((a, b) => a.s - b.s || (b.e - b.s) - (a.e - a.s));
    const frag = document.createDocumentFragment(), chips = [];
    let last = 0;
    for (const f of found) {
      if (f.s < last) continue;
      frag.append(text.slice(last, f.s));
      if (!f.p) {
        frag.append(chip(WITHHELD_WORD, "withheld", { title: "Withheld: never sent and not put back" }));
      } else {
        const type = typeOf(f.p.ph);
        const cat = CATEGORY[type] || "other";
        const c = chip(animate ? f.p.ph : f.p.val, cat, { ph: animate, title: (TYPE_LABEL[type] || "Value") + ": put back by the gateway" });
        chips.push({ chip: c, val: f.p.val });
        frag.append(c);
      }
      last = f.e;
    }
    frag.append(text.slice(last));
    const kids = [];
    if (routeOf(v) === "in_house") kids.push(el("p", { cls: "pane-note in", text: "Answer from the in-house model." }));
    body.replaceChildren(...kids, frag);
    enter(body);
    let done = false;
    result.toValues = () => {
      if (done) return;
      done = true;
      chips.forEach((c, i) => morph(c.chip, c.val, false, Math.min(i, 12) * 45));
    };
    return result;
  }

  // --- outcome, checks, technical -------------------------------------------------------------------------------
  function renderOutcome(run, animate) {
    const d = run.data, v = d.view, e = d.error, box = $("outcome"), c = v.counts || {}, r = v.restore || {};
    const failedChecks = (v.checks || []).filter((x) => x.status === "fail");
    let cls = v.outcome, kicker, title, text, ic;
    const facts = [];
    if (v.outcome === "sent" && failedChecks.length) {
      cls = "warn"; ic = "i-alert"; kicker = "Sent, but a check failed";
      title = "Review this answer before using it";
      text = "The gateway sent this request but reported " + failedChecks.length + " failed check" +
             (failedChecks.length > 1 ? "s" : "") + " (listed below). Tell your administrator.";
    } else if (v.outcome === "sent") {
      ic = "i-shield"; kicker = "Protected";
      title = "Personal details protected";
      text = v.available
        ? "Personal details were replaced before the request left the company" +
          (v.input_source && v.input_source.verified ? ", and its source was verified" : "") +
          ". The answer has the real details back."
        : "The gateway accepted this request and sent it through its protections. It did not include a detailed report, so individual steps are not shown.";
      if (c.replaced != null) facts.push(["Replaced", c.replaced]);
      if (c.removed) facts.push(["Removed or withheld", c.removed]);
      if (r.restored != null) facts.push(["Put back", r.restored]);
    } else if (v.outcome === "kept_inside") {
      ic = "i-building"; kicker = "Kept inside";
      title = "Handled by the in-house model";
      text = "This text is not signed by an approved source system, so the gateway kept it inside the company. It was not sent to an external AI.";
    } else if (v.outcome === "refused") {
      ic = "i-stop"; kicker = "Stopped";
      title = e ? e.title : "Stopped by the privacy gateway";
      text = e ? e.plain : "The privacy gateway did not accept this request.";
    } else {
      ic = "i-alert"; kicker = "Not completed";
      title = e ? e.title : "Something went wrong";
      text = e ? e.plain : "The request could not be completed.";
    }
    const factsBox = el("div", { cls: "outcome-facts" });
    if (e && e.sent === false) factsBox.append(el("span", { cls: "fact", text: "Nothing was sent to an AI" }));
    for (const [k, n] of facts) {
      const num = el("strong", { text: "0" });
      factsBox.append(el("span", { cls: "fact" }, num, document.createTextNode(" " + k.toLowerCase())));
      if (animate) countUp(num, n, fmtInt); else num.textContent = fmtInt(n);
    }
    const kids = [el("div", { cls: "outcome-kicker", text: kicker }), el("div", { cls: "outcome-title", text: title }),
                  el("p", { cls: "outcome-text", text })];
    if (factsBox.childNodes.length) kids.push(factsBox);
    if (e && e.detail) kids.push(el("p", { cls: "outcome-detail", text: "Gateway: " + e.detail }));
    box.className = "outcome o-" + cls;
    box.replaceChildren(el("div", { cls: "outcome-icon" }, icon(ic)), el("div", null, ...kids));
    box.hidden = false;
  }

  function checkItem(c, view) {
    const word = c.status === "pass" ? "Passed" : c.status === "fail" ? "Failed" : c.status === "n/a" ? "Not applicable" : "Unknown";
    const cls = c.status === "pass" ? "pass" : c.status === "fail" ? "fail" : c.status === "n/a" ? "na" : "unknown";
    // Pasted text is unsigned by design: on the in-house route this failure is the reason it stayed inside.
    const expected = c.status === "fail" && c.id === "source_verified" && view && view.outcome === "kept_inside";
    const li = el("li", { cls: "check " + cls + (expected ? " expected" : "") },
              el("span", { cls: "check-icon" }, icon(cls === "pass" ? "i-ok" : cls === "fail" ? "i-x" : "i-dash")),
              el("div", null, el("div", { cls: "check-label", text: c.label }),
                 el("div", { cls: "check-id" }, el("span", { cls: "check-status", text: word }),
                    document.createTextNode(" · " + c.id))));
    if (expected) {
      li.lastChild.append(el("div", { cls: "check-note", text: "Expected for pasted text: this is why the gateway kept it inside the company." }));
    }
    return li;
  }
  function renderChecks(v) {
    const checks = v.checks || [];
    if (!checks.length) { $("checks-card").hidden = true; return; }
    const main = checks.filter((c) => c.status !== "n/a"), na = checks.filter((c) => c.status === "n/a");
    $("check-list").replaceChildren(...main.map((c) => checkItem(c, v)));
    $("na-list").replaceChildren(...na.map((c) => checkItem(c, v)));
    $("na-count").textContent = String(na.length);
    $("na-wrap").hidden = !na.length;
    $("checks-card").hidden = false;
  }

  function dl(pairs) {
    const d = el("dl", { cls: "dl" });
    for (const [k, val, mono] of pairs) {
      if (val == null || val === "") continue;
      d.append(el("dt", { text: k }), el("dd", { cls: mono ? "mono" : null, text: val }));
    }
    return d;
  }
  function renderTech(run) {
    const d = run.data, v = d.view, o = v.outbound, kids = [];
    kids.push(el("section", null, el("h3", { text: "Request" }), dl([
      ["Gateway request id", d.gateway_request_id || v.request_id, true], ["Purpose", d.purpose || v.purpose, true],
      ["Contract", v.contract, true], ["Route", v.route, true], ["Outcome", v.outcome, true],
      ["Input source", v.input_source ? (v.input_source.id || "none") + (v.input_source.verified ? " (verified)" : " (not verified)") : null, true],
      ["Signed by the workspace as", d.signed_as, true], ["HTTP status", d.http_status ? String(d.http_status) : null, true],
      ["Gateway time (sum of stages)", v.total_ms != null ? fmtMs(v.total_ms) : null, true],
      ["Round trip (workspace to gateway)", d.round_trip_ms != null ? fmtMs(d.round_trip_ms) : null, true],
      ["Report", v.available ? "pgw_report v" + (v.version || "?") + (v.truncated ? " (truncated by the gateway)" : "") +
        (v.dropped ? " · " + v.dropped + " malformed entries not shown" : "") : "not included in the response", false],
      ["Error type", d.error ? d.error.type : null, true]])));
    if ((v.stages || []).length) {
      const tb = el("tbody");
      for (const st of v.stages) tb.append(el("tr", null, el("td", { cls: "mono", text: st.id }), el("td", { text: st.status }),
                                              el("td", { cls: "r", text: st.status === "skipped" ? "–" : fmtMs(st.ms) })));
      kids.push(el("section", null, el("h3", { text: "Stages (measured by the gateway)" }),
                   el("table", { cls: "tbl" }, el("thead", null, el("tr", null, el("th", { text: "Stage" }), el("th", { text: "Status" }),
                                                                   el("th", { cls: "r", text: "Time" }))), tb)));
    }
    const by = (v.counts && v.counts.by_type) || {};
    if (Object.keys(by).length) {
      const tb = el("tbody");
      for (const [t, n] of Object.entries(by)) tb.append(el("tr", null, el("td", { text: TYPE_LABEL[t] || t }),
                                                         el("td", { cls: "mono", text: t }), el("td", { cls: "r", text: fmtInt(n) })));
      kids.push(el("section", null, el("h3", { text: "Found, by type" }), el("table", { cls: "tbl" }, tb)));
    }
    if (o) {
      kids.push(el("section", null, el("h3", { text: "Outbound request" }), dl([
        ["Destination", o.destination, true], ["Provider", o.provider, true], ["Model", o.model, true],
        ["Body size", o.bytes != null ? fmtInt(o.bytes) + " bytes" : null, true], ["SHA-256 of the exact body", o.sha256, true],
        ["Gateway placeholder note included", o.gateway_note_included ? "yes" : "no", true]])));
      if (o.messages) {
        const pre = el("pre", { cls: "raw" });
        o.messages.forEach((m, i) => {
          if (i) pre.append(document.createTextNode("\n\n"));
          pre.append(el("span", { cls: "raw-role", text: m.role + ":" }), document.createTextNode("\n" + m.content));
        });
        kids.push(el("section", null, el("h3", { text: "Raw outbound messages" }), pre));
      } else {
        kids.push(el("p", { cls: "hint", text: o.destination === "in_house"
          ? "The gateway does not return the text it sent to the in-house model (it is your unprotected input)."
          : "The gateway does not return the outgoing text on errors; the size and hash above are of the exact bytes." }));
      }
    }
    $("tech-body").replaceChildren(...kids);
  }

  // --- history, copy, dialogs -------------------------------------------------------------------------------------
  const OUTCOME_WORD = { sent: "Protected", kept_inside: "Kept inside", refused: "Stopped", error: "Not completed" };
  function renderHistory(currentId) {
    const list = $("history-list");
    list.replaceChildren(...state.history.map((h) => {
      const b = el("button", { attrs: { type: "button", "aria-current": h.id === currentId ? "true" : "false" } },
                   el("span", { cls: "h-time", text: h.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }),
                   el("span", { cls: "h-title", text: h.task.title }),
                   el("span", { cls: "h-outcome" }, el("span", { cls: "dot dot-" + h.data.view.outcome }),
                      el("span", { text: OUTCOME_WORD[h.data.view.outcome] || h.data.view.outcome })));
      b.addEventListener("click", () => {
        if (state.running) return;
        for (const x of list.querySelectorAll("button")) x.setAttribute("aria-current", "false");
        b.setAttribute("aria-current", "true");
        present(h, false);
      });
      return el("li", null, b);
    }));
    $("history-empty").hidden = state.history.length > 0;
    $("history-clear").hidden = state.history.length === 0;
  }

  async function copyAnswer() {
    const a = state.current && state.current.data.answer;
    if (typeof a !== "string") return;
    let ok = false;
    try { await navigator.clipboard.writeText(a); ok = true; } catch (e) {
      const ta = el("textarea", { attrs: { readonly: "", "aria-hidden": "true" } });
      ta.value = a;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.append(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
    }
    const label = $("copy-label");
    label.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => { label.textContent = "Copy answer"; }, 1600);
  }

  function expired() {
    $("expired").hidden = false;
    $("reload").focus();
  }

  // --- wiring -------------------------------------------------------------------------------------------------------
  function init() {
    $("text").addEventListener("input", updateCounter);
    $("run").addEventListener("click", run);
    $("add-person").addEventListener("click", () => {
      const t = state.task;
      const present = new Set(collectPeople().map((p) => p.role));
      const next = t.roles.find((r) => r.required && !present.has(r.id)) || t.roles.find((r) => !r.required) || t.roles[0];
      const row = addPerson(next.id);
      row.querySelector("input").focus();
      updateRequires();
    });
    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter" && $("main").contains(document.activeElement)) {
        ev.preventDefault();
        run();
      }
    });
    $("compose-title").closest("section").addEventListener("input", () => { if (!$("input-error").hidden) hideInputError(); });
    $("copy").addEventListener("click", copyAnswer);
    $("history-clear").addEventListener("click", () => {
      state.history = [];
      renderHistory(null);
    });
    const how = $("how");
    $("how-open").addEventListener("click", () => how.showModal());
    $("how-close").addEventListener("click", () => how.close());
    how.addEventListener("click", (ev) => { if (ev.target === how) how.close(); });
    $("reload").addEventListener("click", () => window.location.reload());
    loadSession();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
