const test = require('node:test');
const assert = require('node:assert/strict');
const calendar = require('../app/web/calendar-view.js');

const event = (id, actor, date, time = '') => ({
  event_id: id, actor_id: actor, actor_name: 'Artist ' + actor,
  name: 'Event ' + id, date, open_time: time, url: 'https://www.eventernote.com/events/' + id,
});

test('month grid starts on Monday and includes every day in leap February', () => {
  const cells = calendar.monthCells('2024-02');
  assert.equal(cells[0].key, '2024-01-29');
  assert.equal(cells.at(-1).key, '2024-03-10');
  assert.equal(cells.filter(day => day.inMonth).length, 29);
  assert.equal(cells.length, 42);
});

test('calendar includes all six weeks for a long month', () => {
  const cells = calendar.monthCells('2026-03');
  assert.equal(cells.length, 42);
  assert.equal(cells[0].key, '2026-02-23');
  assert.equal(cells.at(-1).key, '2026-04-05');
});

test('month navigation crosses year boundaries', () => {
  assert.equal(calendar.shiftMonth('2026-12', 1), '2027-01');
  assert.equal(calendar.shiftMonth('2026-01', -1), '2025-12');
});

test('weeks span Monday through Sunday across year boundaries', () => {
  const days = calendar.weekCells('2027-01-03');
  assert.equal(days.length, 7);
  assert.equal(days[0].key, '2026-12-28');
  assert.equal(days[6].key, '2027-01-03');
  assert.equal(calendar.weekCells('2027-01-04')[0].key, '2027-01-04');
});

test('weekly navigation preserves date-only values around leap days', () => {
  assert.equal(calendar.shiftDate('2024-02-26', 7), '2024-03-04');
  assert.equal(calendar.shiftDate('2024-03-04', -7), '2024-02-26');
  assert.deepEqual(calendar.weekCells('2026-02-30'), []);
});

test('date-only keys do not shift across UTC boundaries', () => {
  assert.equal(calendar.dateKey(new Date(2026, 9, 27, 0, 15)), '2026-10-27');
  assert.equal(calendar.validDate('2024-02-29'), true);
  assert.equal(calendar.validDate('2026-02-29'), false);
  assert.equal(calendar.validDate('2026-13-01'), false);
});

test('shared events have one entry with all artists without mutating cache rows', () => {
  const rows = [event(1, 11, '2026-10-27'), event(1, 12, '2026-10-27'), event(1, 11, '2026-10-27')];
  const merged = calendar.mergeEvents(rows);
  assert.equal(merged.length, 1);
  assert.deepEqual(merged[0].actors.map(a => a.id), [11, 12]);
  assert.equal(rows[0].actors, undefined);
});

test('daily events are sorted by performance time, with unknown times last', () => {
  const rows = [event(1, 1, '2026-10-27'), event(2, 1, '2026-10-27', '開場 18:00 開演 19:00'),
    event(3, 1, '2026-10-27', '開場 11:00 開演 12:00'), event(4, 1, '2026-10-26', '開演 20:00')];
  assert.deepEqual(calendar.mergeEvents(rows).map(e => e.event_id), [4, 3, 2, 1]);
});

test('artist and future filters preserve co-performers and include today', () => {
  const merged = calendar.mergeEvents([event(1, 11, '2026-10-27'), event(1, 12, '2026-10-27'),
    event(2, 11, '2026-10-26'), event(3, 13, '2026-10-28')]);
  const filtered = calendar.filterEvents(merged, '12', true, '2026-10-27');
  assert.deepEqual(filtered.map(e => e.event_id), [1]);
  assert.equal(filtered[0].actors.length, 2);
  assert.equal(calendar.filterEvents(merged, null, false, '2026-10-27').length, 3);
});

test('dates missing or malformed remain accessible separately from calendar days', () => {
  const events = calendar.mergeEvents([event(1, 1, ''), event(2, 1, '2026-02-30'), event(3, 1, '2026-10-27')]);
  const grouped = calendar.eventsByDay(events);
  assert.equal(grouped.get('undated').length, 2);
  assert.equal(grouped.get('2026-10-27').length, 1);
  assert.equal(calendar.eventsByDay([]).size, 0);
});
