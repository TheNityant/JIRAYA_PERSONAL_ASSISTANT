import type { CalendarEvent } from "./api";

export function localDate(value: string) {
  // Google date-only values are calendar dates, not UTC midnight timestamps.
  return new Date(value.length === 10 ? `${value}T00:00:00` : value);
}

export function startOfDay(value: Date) {
  const date = new Date(value); date.setHours(0, 0, 0, 0); return date;
}

export function addDays(value: Date, days: number) {
  const date = new Date(value); date.setDate(date.getDate() + days); return date;
}

export function sameDay(first: Date, second: Date) {
  return first.toDateString() === second.toDateString();
}

export function eventDate(event: CalendarEvent) {
  return localDate(event.start?.dateTime || event.start?.date || "");
}

function eventEnd(event: CalendarEvent) {
  if (event.end?.dateTime || event.end?.date) return localDate(event.end.dateTime || event.end.date!);
  return event.start?.date ? addDays(eventDate(event), 1) : new Date(eventDate(event).getTime() + 1);
}

export function calendarAgenda(events: CalendarEvent[], anchor: Date, days: number) {
  const start = startOfDay(anchor), tomorrow = addDays(start, 1), end = addDays(start, days);
  const visible = Array.from(new Map(events.map(event => [event.id, event])).values())
    .filter(event => event.status !== "cancelled" && Number.isFinite(eventDate(event).getTime())
      && Number.isFinite(eventEnd(event).getTime()) && eventEnd(event) > eventDate(event)
      && eventDate(event) < end && eventEnd(event) > start)
    .sort((first, second) => eventDate(first).getTime() - eventDate(second).getTime());
  const selected = visible.filter(event => eventDate(event) < tomorrow && eventEnd(event) > start);
  const upcoming = visible.filter(event => eventDate(event) >= tomorrow);
  const groups: Array<{date: Date; events: CalendarEvent[]}> = [];
  for (const event of upcoming) {
    const date = eventDate(event), last = groups[groups.length - 1];
    if (last && sameDay(last.date, date)) last.events.push(event);
    else groups.push({date: startOfDay(date), events: [event]});
  }
  return {selected, upcoming, groups};
}

export function eventTime(event: CalendarEvent) {
  if (event.start?.date) {
    const last = addDays(eventEnd(event), -1), first = eventDate(event);
    return last > first ? `All day · through ${last.toLocaleDateString([], {month: "short", day: "numeric"})}` : "All day";
  }
  const first = eventDate(event), end = eventEnd(event);
  const format: Intl.DateTimeFormatOptions = {hour: "numeric", minute: "2-digit"};
  const label = (date: Date) => date.toLocaleString([], {...format, ...(!sameDay(first, end) ? {month: "short", day: "numeric"} as const : {})});
  return `${label(first)}${event.end?.dateTime ? ` – ${label(end)}` : ""}`;
}
