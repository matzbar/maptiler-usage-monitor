import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MARGIN,
  addDays,
  buildChartModel,
  cumulativeRequests,
  niceStep,
} from "../../app/panel/static/js/chart-model.js";

const usage = {
  status: "ok",
  period: { since: "2026-11-03", until: "2026-12-02" },
  requests: { used: 30000, quota: 100000, percent: 30 },
  projection: { requests: 90000, days_in_period: 30 },
};

test("niceStep picks 1, 2 or 5 × 10^n", () => {
  assert.equal(niceStep(108000, 5), 50000);
  assert.equal(niceStep(212220, 5), 50000);
  assert.equal(niceStep(950, 4), 500);
  assert.equal(niceStep(10, 5), 2);
  assert.equal(niceStep(0, 5), 1);
});

test("addDays crosses months without time-zone drift", () => {
  assert.equal(addDays("2026-11-30", 1), "2026-12-01");
  assert.equal(addDays("2026-11-03", 29), "2026-12-02");
});

test("cumulative series keeps only the period, sorted", () => {
  const daily = [
    { date: "2026-11-04", requests: 20 },
    { date: "2026-11-02", requests: 999 }, // previous period
    { date: "2026-11-03", requests: 10 },
  ];
  assert.deepEqual(cumulativeRequests(daily, "2026-11-03", "2026-12-02"), [
    { date: "2026-11-03", dayIndex: 0, total: 10 },
    { date: "2026-11-04", dayIndex: 1, total: 30 },
  ]);
});

test("model maps the period onto the drawing area", () => {
  const daily = [
    { date: "2026-11-03", requests: 10000 },
    { date: "2026-11-05", requests: 20000 },
  ];
  const model = buildChartModel(usage, daily, 700);
  assert.equal(model.width, 700);
  assert.equal(model.height, 260);
  assert.equal(model.total, 30000);
  assert.equal(model.points[0].x, MARGIN.left);
  assert.equal(model.dateLabels.at(-1).x, 700 - MARGIN.right);
  assert.deepEqual(
    model.dateLabels.map((d) => d.date),
    ["2026-11-03", "2026-11-17", "2026-12-02"],
  );
  assert.ok(model.quota.y < model.warningLine.y, "quota line is above the 80% line");
  assert.equal(model.projection.to.x, 700 - MARGIN.right);
  assert.equal(model.ticks[0].value, 0);
  assert.ok(model.ticks.every((t) => t.y >= MARGIN.top && t.y <= model.height - MARGIN.bottom));
});

test("narrow screens get a shorter chart and no projection once the period is over", () => {
  const daily = Array.from({ length: 30 }, (_, i) => ({ date: addDays("2026-11-03", i), requests: 1000 }));
  const model = buildChartModel(usage, daily, 360);
  assert.equal(model.height, 220);
  assert.equal(model.projection, null);
});

test("no data in the period → no model", () => {
  assert.equal(buildChartModel(usage, [{ date: "2026-10-01", requests: 5 }], 700), null);
});
