// Draws the cumulative requests chart (inline SVG) from the model built in chart-model.js.

import { buildChartModel } from "./chart-model.js";
import { emptyState, fill, h, s } from "./dom.js";
import { formatDate, formatInteger, formatShortDate } from "./format.js";

const MIN_WIDTH = 280;
const FALLBACK_WIDTH = 720;

function horizontalLine(model, y, className) {
  return s("line", { class: className, x1: model.plot.left, x2: model.plot.right, y1: y, y2: y });
}

function lineLabel(model, y, text) {
  return s("text", { class: "chart__line-label", x: model.plot.right, y: y - 5, "text-anchor": "end" }, text);
}

function buildSvg(model, usage) {
  const { until } = usage.period;
  const label =
    `Requests acumulados: ${formatInteger(model.total)} de ${formatInteger(model.quota.value)}; ` +
    `projeção de ${formatInteger(usage.projection.requests)} até ${formatDate(until)}`;
  const svg = s("svg", {
    class: "chart__svg",
    viewBox: `0 0 ${model.width} ${model.height}`,
    width: model.width,
    height: model.height,
    role: "img",
    "aria-label": label,
  });

  for (const tick of model.ticks) {
    svg.append(
      horizontalLine(model, tick.y, "chart__grid"),
      s(
        "text",
        { class: "chart__axis-label", x: model.plot.left - 8, y: tick.y + 4, "text-anchor": "end" },
        formatInteger(tick.value),
      ),
    );
  }
  // Native append (not dom.js): it would turn a null into the text "null", so the optional label is added apart.
  svg.append(horizontalLine(model, model.warningLine.y, "chart__warning"));
  if (model.warningLine.labeled) svg.append(lineLabel(model, model.warningLine.y, "80%"));
  svg.append(horizontalLine(model, model.quota.y, "chart__quota"), lineLabel(model, model.quota.y, "cota"));
  for (const tick of model.dateLabels) {
    svg.append(
      s(
        "text",
        { class: "chart__axis-label", x: tick.x, y: model.height - 8, "text-anchor": tick.anchor },
        formatShortDate(tick.date),
      ),
    );
  }
  if (model.projection) {
    const { from, to } = model.projection;
    svg.append(s("line", { class: "chart__projection", x1: from.x, y1: from.y, x2: to.x, y2: to.y }));
  }
  const path = model.points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  svg.append(s("path", { class: "chart__usage", d: path }));
  for (const point of model.points) {
    svg.append(
      s(
        "circle",
        { class: "chart__point", cx: point.x, cy: point.y, r: 3 },
        s("title", {}, `${formatDate(point.date)}: ${formatInteger(point.total)} acumulados`),
      ),
    );
  }
  return svg;
}

/** Renders the chart into `container`, sized to the container's current width. */
export function renderUsageChart(container, usage, daily) {
  if (usage.status === "unavailable") {
    fill(container, emptyState("Sem dados de consumo."));
    return;
  }
  const width = Math.max(Math.round(container.clientWidth) || FALLBACK_WIDTH, MIN_WIDTH);
  const model = buildChartModel(usage, daily, width);
  if (!model) {
    fill(container, emptyState("Ainda não há consumo dia a dia neste período."));
    return;
  }
  fill(
    container,
    buildSvg(model, usage),
    h(
      "ul",
      { class: "chart__legend" },
      h("li", { class: "chart__legend-item chart__legend-item--usage", text: "Consumo acumulado" }),
      h("li", { class: "chart__legend-item chart__legend-item--projection", text: "Projeção pela média diária" }),
      h("li", { class: "chart__legend-item chart__legend-item--quota", text: "Cota" }),
    ),
    h("p", {
      class: "hint",
      text: "O histórico dia a dia pode ficar um pouco atrás do total do período, que vem direto da última verificação.",
    }),
  );
}
