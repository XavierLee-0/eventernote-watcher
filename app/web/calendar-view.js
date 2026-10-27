/* Calendar dates remain date-only strings; never convert them through UTC. */
const EventCalendar = (() => {
  const pad = value => String(value).padStart(2, '0');
  const dateKey = date => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const validDate = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return false;
    const [year, month, day] = value.split('-').map(Number);
    return year >= 1000 && dateKey(new Date(year, month - 1, day)) === value;
  };
  const shiftMonth = (month, offset) => {
    const [year, number] = month.split('-').map(Number);
    return dateKey(new Date(year, number - 1 + offset, 1)).slice(0, 7);
  };
  const monthCells = month => {
    const [year, number] = month.split('-').map(Number);
    const first = new Date(year, number - 1, 1);
    const offset = (first.getDay() + 6) % 7;
    return Array.from({ length: 42 }, (_, index) => {
      const date = new Date(year, number - 1, 1 - offset + index);
      return { key: dateKey(date), day: date.getDate(), inMonth: date.getMonth() === number - 1 };
    });
  };
  const shiftDate = (key, offset) => {
    const [year, month, day] = key.split('-').map(Number);
    return dateKey(new Date(year, month - 1, day + offset));
  };
  const weekCells = anchor => {
    if (!validDate(anchor)) return [];
    const [year, month, day] = anchor.split('-').map(Number);
    const offset = (new Date(year, month - 1, day).getDay() + 6) % 7;
    return Array.from({length:7}, (_, index) => ({key:shiftDate(anchor, index-offset)}));
  };
  const startTime = event => {
    const text = event.open_time || '';
    const match = text.match(/開演\s*(\d{1,2}):(\d{2})/) || text.match(/開場\s*(\d{1,2}):(\d{2})/);
    return match ? Number(match[1]) * 60 + Number(match[2]) : Infinity;
  };
  const compareEvents = (a, b) => {
    const day = (validDate(a.date) ? a.date : '9999-99-99').localeCompare(validDate(b.date) ? b.date : '9999-99-99');
    if (day) return day;
    const aTime = startTime(a), bTime = startTime(b);
    if (aTime !== bTime) return aTime < bTime ? -1 : 1;
    return String(a.name).localeCompare(String(b.name), 'ja') || a.event_id - b.event_id;
  };
  const mergeEvents = rows => {
    const merged = new Map();
    for (const row of rows) {
      if (!merged.has(row.event_id)) merged.set(row.event_id, { ...row, actors: [] });
      const event = merged.get(row.event_id);
      if (!event.actors.some(actor => actor.id === row.actor_id)) {
        event.actors.push({ id: row.actor_id, name: row.actor_name || '未知出演者' });
      }
    }
    return [...merged.values()].sort(compareEvents);
  };
  const filterEvents = (events, actorId, futureOnly, today) => events.filter(event =>
    (actorId == null || event.actors.some(actor => String(actor.id) === String(actorId))) &&
    (!futureOnly || !validDate(event.date) || event.date >= today));
  const eventsByDay = events => {
    const days = new Map();
    for (const event of events) {
      const key = validDate(event.date) ? event.date : 'undated';
      if (!days.has(key)) days.set(key, []);
      days.get(key).push(event);
    }
    return days;
  };
  const colorFromHue = hue => ({
    color: `hsl(${hue} 70% 29%)`,
    backgroundColor: `hsl(${hue} 75% 95%)`,
    borderColor: `hsl(${hue} 65% 65%)`,
  });
  const fallbackArtistColor = id => colorFromHue((Number(id) * 137.507764) % 360);
  const artistColors = artists => {
    const palette = [220, 145, 20, 280, 345, 175, 45, 245, 195, 310, 85, 210, 5, 160, 260, 330];
    const colors = new Map(), used = new Set();
    // Use the complete watched list so filtering or changing months keeps colors stable.
    const ids = [...new Set(artists.map(a => String(a.actor_id)))].sort((a,b) => Number(a)-Number(b));
    for (const id of ids) {
      let slot = Number(id) % palette.length;
      if (used.size < palette.length) {
        while (used.has(slot)) slot = (slot + 1) % palette.length;
        used.add(slot);
        colors.set(id, colorFromHue(palette[slot]));
      } else {
        colors.set(id, fallbackArtistColor(id));
      }
    }
    return colors;
  };
  return { dateKey, validDate, shiftMonth, monthCells, shiftDate, weekCells, mergeEvents, filterEvents, eventsByDay, artistColors, fallbackArtistColor };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = EventCalendar;
