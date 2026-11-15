// Geometry of the "cumulative requests" chart. Pure: turns API data into coordinates, no DOM.

const MS_PER_DAY = 86_400_000;
const HEADROOM = 1.08; // empty space above the highest line, so it doesn't touch the top edge
const MIN_LABEL_GAP = 12; // px between the "cota" and "80%" labels (11px text); closer than this, 80% is unlabeled

export const MARGIN = Object.freeze({ left: 64, right: 16, top: 12, bottom: 28 });

function dayNumber(isoDate) {
  return Date.parse(`${isoDate}T00:00:00Z`) / MS_PER_DAY;
}

export function addDays(isoDate, days) {
  return new Date((dayNumber(isoDate) + days) * MS_PER_DAY).toISOString().slice(0, 10);
}

/** Axis step that is a "round" number (1, 2 or 5 × 10^n) giving about `targetTicks` ticks up to `max`. */
export function niceStep(max, targetTicks) {
  if (!(max > 0)) return 1;
  const rough = max / targetTicks;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  return [1, 2, 5, 10].map((m) => m * magnitude).find((step) => step >= rough);
}

/** Running total of requests per day of the billing period, sorted by date. */
export function cumulativeRequests(daily, since, until) {
  let total = 0;
  return daily
    .filter((d) => d.date >= since && d.date <= until)
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ date: d.date, dayIndex: dayNumber(d.date) - dayNumber(since), total: (total += d.requests) }));
}

/**
 * Everything the chart needs to draw itself, or null when there is nothing to draw.
 * @param {object} usage UsageResponse with status != "unavailable"
 * @param {{date: string, requests: number}[]} daily
 * @param {number} width drawing width in px (the chart uses 1 unit = 1 px, so text keeps its size)
 */
export function buildChartModel(usage, daily, width) {
  const { since, until } = usage.period;
  const daysInPeriod = usage.projection.days_in_period;
  const points = cumulativeRequests(daily, since, until);
  if (!points.length) return null;

  const quota = usage.requests.quota;
  const projected = usage.projection.requests;
  const last = points.at(-1);
  const top = Math.max(quota, projected, last.total) * HEADROOM;
  const height = width < 520 ? 220 : 260;

  const x = (dayIndex) =>
    MARGIN.left + (dayIndex / Math.max(daysInPeriod - 1, 1)) * (width - MARGIN.left - MARGIN.right);
  const y = (value) => MARGIN.top + (1 - value / top) * (height - MARGIN.top - MARGIN.bottom);

  const step = niceStep(top, height < 240 ? 4 : 5);
  const ticks = [];
  for (let value = 0; value <= top; value += step) ticks.push({ value, y: y(value) });

  const middle = Math.floor((daysInPeriod - 1) / 2);
  const dateLabels = [
    { date: addDays(since, 0), x: x(0), anchor: "start" },
    { date: addDays(since, middle), x: x(middle), anchor: "middle" },
    { date: addDays(since, daysInPeriod - 1), x: x(daysInPeriod - 1), anchor: "end" },
  ];

  const lastIndex = daysInPeriod - 1;
  return {
    width,
    height,
    plot: { left: MARGIN.left, right: width - MARGIN.right },
    ticks,
    dateLabels,
    quota: { value: quota, y: y(quota) },
    warningLine: {
      value: quota * 0.8,
      y: y(quota * 0.8),
      // early in a period with heavy use, the projection stretches the axis and the two lines nearly touch
      labeled: y(quota * 0.8) - y(quota) >= MIN_LABEL_GAP,
    },
    points: points.map((p) => ({ ...p, x: x(p.dayIndex), y: y(p.total) })),
    projection:
      last.dayIndex < lastIndex
        ? {
            from: { x: x(last.dayIndex), y: y(last.total) },
            to: { x: x(lastIndex), y: y(projected) },
            value: projected,
          }
        : null,
    total: last.total,
  };
}
