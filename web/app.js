// Single page UI for the remodel estimator. Talks to the small JSON API in
// remodel_estimator/api.py. All rendering uses DOM nodes and textContent, never
// innerHTML, so nothing typed into the form can be injected as markup.
"use strict";

const state = { catalog: null, scope: { project: "", rooms: [] } };
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status})`);
    error.problems = body.problems || [];
    throw error;
  }
  return body;
}

const usd = (n) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const num = (n, digits = 2) => n.toLocaleString("en-US", { maximumFractionDigits: digits });

// ---- scope editor -------------------------------------------------------

function blankRoom() {
  return { name: "New room", length_ft: 10, width_ft: 10, height_ft: 8, doors: 1, windows: 0, work_items: [] };
}

function numberField(label, room, key, step) {
  const input = el("input", { type: "number", min: "0", step: step, value: String(room[key] ?? "") });
  input.addEventListener("input", () => { room[key] = input.value === "" ? null : Number(input.value); });
  return el("label", { text: label }, [input]);
}

// Wall runs with no baseboard (tub, vanity, cabinets). The scope keeps them as a
// labelled list; the form edits the total and keeps the labels until it changes.
function deductionField(room) {
  const total = (room.base_deductions || []).reduce((sum, d) => sum + (Number(d.lf) || 0), 0);
  const input = el("input", { type: "number", min: "0", step: "0.5", value: String(total) });
  input.addEventListener("input", () => {
    const value = Number(input.value);
    room.base_deductions = value > 0 ? [{ label: "runs with no baseboard", lf: value }] : [];
  });
  return el("label", { text: "No baseboard ft" }, [input]);
}

function itemRow(room, item) {
  const selected = room.work_items.find((w) => w.code === item.code);
  const id = `item-${state.scope.rooms.indexOf(room)}-${item.code}`;
  const check = el("input", { type: "checkbox", id: id });
  check.checked = Boolean(selected);
  const qty = el("input", {
    type: "number", min: "0", step: "any", "aria-label": `${item.name} quantity`,
    placeholder: item.needs_qty ? "qty" : (item.basis === "each" ? "1" : "auto"),
  });
  if (selected && selected.qty != null) qty.value = String(selected.qty);
  qty.disabled = !selected;

  const sync = () => {
    room.work_items = room.work_items.filter((w) => w.code !== item.code);
    qty.disabled = !check.checked;
    if (!check.checked) return;
    const entry = { code: item.code };
    if (qty.value !== "") entry.qty = Number(qty.value);
    room.work_items.push(entry);
  };
  check.addEventListener("change", sync);
  qty.addEventListener("input", sync);

  const label = el("label", { for: id }, [check, el("span", { text: item.name })]);
  return el("div", { class: "item" }, [label, qty, el("span", { class: "unit", text: item.unit })]);
}

function roomCard(room, index) {
  const name = el("input", { type: "text", value: room.name, maxlength: "40", "aria-label": "Room name" });
  name.addEventListener("input", () => { room.name = name.value; });
  const remove = el("button", {
    type: "button", class: "link", text: "Remove",
    onclick: () => { state.scope.rooms.splice(index, 1); renderRooms(); },
  });
  const dims = el("div", { class: "dims" }, [
    numberField("Length ft", room, "length_ft", "0.5"),
    numberField("Width ft", room, "width_ft", "0.5"),
    numberField("Height ft", room, "height_ft", "0.5"),
    numberField("Doors", room, "doors", "1"),
    numberField("Windows", room, "windows", "1"),
    deductionField(room),
  ]);
  const items = el("div", { class: "items" });
  for (const phase of state.catalog.phases) {
    const inPhase = state.catalog.items.filter((i) => i.phase === phase.phase);
    if (!inPhase.length) continue;
    items.append(el("div", { class: "phase", text: phase.name }));
    for (const item of inPhase) items.append(itemRow(room, item));
  }
  return el("div", { class: "room" }, [el("div", { class: "room-head" }, [name, remove]), dims, items]);
}

function renderRooms() {
  $("project-name").value = state.scope.project;
  $("rooms").replaceChildren(...state.scope.rooms.map(roomCard));
}

function showErrors(messages) {
  $("errors").replaceChildren(...messages.map((m) => el("li", { text: m })));
}

async function loadExample(name) {
  state.scope = await getJson(`/api/examples/${encodeURIComponent(name)}`);
  renderRooms();
  showErrors([]);
  await runEstimate();
}

// ---- results ------------------------------------------------------------

function table(target, columns, rows) {
  const head = el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label })));
  const body = rows.map((row) => {
    if (row.group) return el("tr", { class: "group" }, [el("td", { colspan: String(columns.length), text: row.group })]);
    const cells = columns.map((c) => el("td", { class: c.num ? "num" : "", text: c.value(row) }));
    return el("tr", { class: row.grand ? "grand" : "" }, cells);
  });
  $(target).replaceChildren(el("thead", {}, [head]), el("tbody", {}, body));
}

function withGroups(rows, key) {
  const out = [];
  let current = null;
  for (const row of rows) {
    if (row[key] !== current) { current = row[key]; out.push({ group: current }); }
    out.push(row);
  }
  return out;
}

function renderEstimate(est) {
  const t = est.totals;
  const card = (label, value, extra = "") =>
    el("div", { class: `card ${extra}` }, [el("div", { class: "label", text: label }), el("div", { class: "value", text: value })]);
  $("totals").replaceChildren(
    card("Estimate total (sample)", usd(t.total), "total"),
    card("Materials", usd(t.materials)),
    card("Labor", usd(t.labor)),
    card("Working days", String(est.schedule.working_days)),
  );

  const totalRows = [
    ["Materials", t.materials], ["Sales tax on materials", t.material_tax], ["Labor", t.labor],
    ["Direct cost", t.direct_cost], ["Overhead", t.overhead], ["Contingency", t.contingency], ["Profit", t.profit],
  ].map(([label, amount]) => ({ label, amount }));
  totalRows.push({ label: "Estimate total", amount: t.total, grand: true });
  table("totals-table", [
    { label: "Line", value: (r) => r.label },
    { label: "Amount (sample)", num: true, value: (r) => usd(r.amount) },
  ], totalRows);

  table("trade-table", [
    { label: "Trade", value: (r) => r.trade },
    { label: "Materials", num: true, value: (r) => usd(r.materials) },
    { label: "Labor", num: true, value: (r) => usd(r.labor) },
    { label: "Total", num: true, value: (r) => usd(r.total) },
  ], est.by_trade);

  table("takeoff-table", [
    { label: "Material", value: (r) => r.description },
    { label: "Net", num: true, value: (r) => num(r.net_qty) },
    { label: "Waste", num: true, value: (r) => `${num(r.waste_pct, 1)}%` },
    { label: "Buy", num: true, value: (r) => String(r.purchase_qty) },
    { label: "Unit", value: (r) => r.unit },
    { label: "Each", num: true, value: (r) => usd(r.unit_price) },
    { label: "Extended", num: true, value: (r) => usd(r.extended) },
  ], withGroups(est.takeoff, "trade"));

  table("labor-table", [
    { label: "Trade", value: (r) => r.trade },
    { label: "Hours", num: true, value: (r) => num(r.hours) },
    { label: "Billable", num: true, value: (r) => num(r.billable_hours) + (r.minimum_applied ? " (min)" : "") },
    { label: "Rate", num: true, value: (r) => usd(r.rate) },
    { label: "Cost", num: true, value: (r) => usd(r.cost) },
  ], est.labor);

  table("schedule-table", [
    { label: "Start day", num: true, value: (r) => num(r.start_day) },
    { label: "End day", num: true, value: (r) => num(r.end_day) },
    { label: "Room", value: (r) => r.room },
    { label: "Work item", value: (r) => r.name },
    { label: "Trade", value: (r) => r.trade },
    { label: "Hours", num: true, value: (r) => num(r.hours) },
    { label: "Then wait", value: (r) => (r.wait_days ? `${num(r.wait_days, 1)} day to ${r.wait_for}` : "") },
  ], est.schedule.steps);

  $("warnings").replaceChildren(...est.warnings.map((w) => el("li", { text: w })));
  $("assumptions").replaceChildren(...est.assumptions.map((a) => el("li", { text: a })));
  $("empty-state").hidden = true;
  $("result").hidden = false;
}

async function runEstimate() {
  state.scope.project = $("project-name").value;
  try {
    const est = await getJson("/api/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.scope),
    });
    showErrors([]);
    renderEstimate(est);
  } catch (error) {
    showErrors(error.problems && error.problems.length ? error.problems : [error.message]);
  }
}

// ---- startup ------------------------------------------------------------

async function start() {
  try {
    const [health, catalog, examples] = await Promise.all([
      getJson("/api/health"), getJson("/api/catalog"), getJson("/api/examples"),
    ]);
    state.catalog = catalog;
    const book = health.price_book;
    $("sample-banner").textContent = book.sample
      ? `${book.name}: ${book.notice}`
      : `Price book: ${book.name}`;
    const select = $("example-select");
    for (const example of examples.examples) {
      select.append(el("option", { value: example.name, text: example.project }));
    }
    select.addEventListener("change", () => loadExample(select.value).catch((e) => showErrors([e.message])));
    $("add-room").addEventListener("click", () => { state.scope.rooms.push(blankRoom()); renderRooms(); });
    $("run-estimate").addEventListener("click", runEstimate);
    const first = examples.examples.find((e) => e.name === "bathroom") || examples.examples[0];
    if (first) {
      select.value = first.name;
      await loadExample(first.name);
    }
  } catch (error) {
    showErrors([`Could not reach the API: ${error.message}. Start it with: python -m remodel_estimator serve`]);
  }
}

start();
