// Small DOM helpers. Text always goes in through text nodes, never as HTML, so API data can't inject markup.

import { TONES } from "./labels.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function append(node, children) {
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/**
 * Creates an HTML element.
 * Props: `class`, `text` and any attribute; null/false values are skipped. Children: nodes or strings.
 */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  return append(node, children);
}

/** Creates an SVG element (attributes as-is, children: nodes or strings). */
export function s(tag, attributes = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value != null) node.setAttribute(name, String(value));
  }
  return append(node, children);
}

/**
 * Replaces a container's content in a single DOM write. Unlike Element.replaceChildren, null/false
 * children are skipped (replaceChildren would turn them into the text "null").
 */
export function fill(container, ...children) {
  container.replaceChildren(append(document.createDocumentFragment(), children));
  return container;
}

/** Pill with a tone (see TONES); an unknown tone falls back to neutral instead of an unstyled pill. */
export function badge(text, tone = "neutral") {
  const safeTone = TONES.includes(tone) ? tone : "neutral";
  return h("span", { class: `badge badge--${safeTone}`, text });
}

export function emptyState(text) {
  return h("p", { class: "empty", text });
}

/**
 * Table with a header row. Each column: {title, value: (row) => string|Node, numeric?: boolean}.
 * `labelledBy`: id of the heading that names the table, for screen readers.
 */
export function dataTable(columns, rows, { emptyText, labelledBy = null }) {
  if (!rows.length) return emptyState(emptyText);
  const cellClass = (column) => (column.numeric ? "numeric" : null);
  const head = h(
    "tr",
    {},
    columns.map((c) => h("th", { scope: "col", class: cellClass(c), text: c.title })),
  );
  const body = rows.map((row) =>
    h(
      "tr",
      {},
      columns.map((c) => h("td", { class: cellClass(c) }, c.value(row) ?? "—")),
    ),
  );
  return h(
    "div",
    { class: "table-scroll" },
    h("table", { "aria-labelledby": labelledBy }, h("thead", {}, head), h("tbody", {}, body)),
  );
}
