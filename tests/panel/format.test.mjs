import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatDate,
  formatDateTime,
  formatInteger,
  formatPercent,
  formatShortDate,
  formatTimeAgo,
  toneForPercent,
} from "../../app/panel/static/js/format.js";
import { CHANNELS, LEVELS, statusFor, textFor } from "../../app/panel/static/js/labels.js";

test("numbers use the Brazilian format", () => {
  assert.equal(formatInteger(196500), "196.500");
  assert.equal(formatPercent(78.6), "78,6%");
  assert.equal(formatPercent(0), "0,0%");
  assert.equal(formatInteger(null), "—");
  assert.equal(formatPercent(undefined), "—");
});

test("timestamps are shown in Brasília time", () => {
  assert.equal(formatDateTime("2026-11-14T20:12:54Z"), "14/11/2026, 17:12");
  assert.equal(formatDateTime(null), "—");
});

test("calendar dates never shift a day", () => {
  assert.equal(formatDate("2026-12-01"), "01/12/2026");
  assert.equal(formatShortDate("2026-12-01"), "01/12");
});

test("time ago", () => {
  const now = Date.parse("2026-11-14T12:00:00Z");
  assert.equal(formatTimeAgo("2026-11-14T11:59:40Z", now), "agora há pouco");
  assert.equal(formatTimeAgo("2026-11-14T11:45:00Z", now), "há 15 min");
  assert.equal(formatTimeAgo("2026-11-14T06:00:00Z", now), "há 6 h");
  assert.equal(formatTimeAgo("2026-11-11T12:00:00Z", now), "há 3 dias");
});

test("tone follows the alert thresholds", () => {
  assert.equal(toneForPercent(49.9), "ok");
  assert.equal(toneForPercent(50), "notice");
  assert.equal(toneForPercent(79.9), "notice");
  assert.equal(toneForPercent(80), "warning");
  assert.equal(toneForPercent(100), "critical");
});

test("statusFor always returns {label, tone}", () => {
  assert.deepEqual(statusFor(LEVELS, "high"), { label: "Alto (80%)", tone: "warning" });
  assert.deepEqual(statusFor(LEVELS, "new_level"), { label: "new_level", tone: "neutral" });
  assert.deepEqual(statusFor(LEVELS, "toString"), { label: "toString", tone: "neutral" }); // inherited keys are not codes
});

test("textFor falls back to the raw code", () => {
  assert.equal(textFor(CHANNELS, "email"), "e-mail");
  assert.equal(textFor(CHANNELS, "sms"), "sms");
  assert.equal(textFor(CHANNELS, "constructor"), "constructor");
});
