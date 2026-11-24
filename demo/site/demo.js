// Read-only static demo. Renders the example estimates that demo/build_static_site.py
// wrote into data/ at build time. There is no server and nothing is sent anywhere.
// Rendering uses DOM nodes and textContent, never innerHTML.
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

const usd = (n) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const num = (n, digits = 2) => n.toLocaleString("en-US", { maximumFractionDigits: digits });

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

function showErrors(messages) {
  $("errors").replaceChildren(...messages.map((m) => el("li", { text: m })));
}

function renderEstimate(name, est) {
  const t = est.totals;
  const card = (label, value, extra = "") =>
    el("div", { class: `card ${extra}` }, [el("div", { class: "label", text: label }), el("div", { class: "value", text: value })]);
  $("project-title").textContent = `${est.project} (sample prices)`;
  $("json-link").setAttribute("href", `data/${name}.json`);
  $("totals").replaceChildren(
    card("Sample total (not a quote)", usd(t.total), "total"),
    card("Materials (sample)", usd(t.materials)),
    card("Labor (sample)", usd(t.labor)),
    card("Working days", String(est.schedule.working_days)),
  );

  table("rooms-table", [
    { label: "Room", value: (r) => r.name },
    { label: "L x W x H ft", value: (r) => `${num(r.length_ft)} x ${num(r.width_ft)} x ${num(r.height_ft)}` },
    { label: "Floor sf", num: true, value: (r) => num(r.floor_sf) },
    { label: "Walls sf", num: true, value: (r) => num(r.wall_sf) },
    { label: "Perimeter lf", num: true, value: (r) => num(r.perimeter_lf) },
    { label: "Baseboard lf", num: true, value: (r) => num(r.baseboard_lf) },
  ], est.rooms);

  const totalRows = [
    ["Materials", t.materials], ["Sales tax on materials", t.material_tax], ["Labor", t.labor],
    ["Direct cost", t.direct_cost], ["Overhead", t.overhead], ["Contingency", t.contingency], ["Profit", t.profit],
  ].map(([label, amount]) => ({ label, amount }));
  totalRows.push({ label: "Sample total", amount: t.total, grand: true });
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

  $("schedule-title").textContent = `Sequence (${est.schedule.working_days} working days, one crew per trade)`;
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

const cache = new Map();

async function show(name) {
  for (const link of document.querySelectorAll("a.example")) {
    if (link.dataset.name === name) link.setAttribute("aria-current", "true");
    else link.removeAttribute("aria-current");
  }
  try {
    if (!cache.has(name)) {
      const response = await fetch(`data/${name}.json`);
      if (!response.ok) throw new Error(`could not load data/${name}.json (${response.status})`);
      cache.set(name, await response.json());
    }
    showErrors([]);
    renderEstimate(name, cache.get(name));
  } catch (error) {
    showErrors([`Could not load this example: ${error.message}`]);
  }
}

function current() {
  const names = [...document.querySelectorAll("a.example")].map((a) => a.dataset.name);
  const wanted = window.location.hash.slice(1);
  return names.includes(wanted) ? wanted : (names.includes("bathroom") ? "bathroom" : names[0]);
}

window.addEventListener("hashchange", () => show(current()));
show(current());
