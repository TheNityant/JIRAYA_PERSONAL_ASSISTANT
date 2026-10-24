"use client";
import { useEffect, useState } from "react";
import { api, CalendarEvent, ServiceStatus } from "@/lib/api";
import { addDays, calendarAgenda, eventDate, eventTime, sameDay, startOfDay } from "@/lib/calendar-agenda";
import ServiceIcon from "./ServiceIcon";
import ServiceMonitor from "./ServiceMonitor";

function EventRow({event, date, continued}: {event: CalendarEvent; date: Date; continued?: boolean}) {
  return <article className="agenda-row">
    <div className="agenda-date"><span>{date.toLocaleDateString([], {weekday: "short"})}</span><strong>{date.getDate()}</strong><small>{date.toLocaleDateString([], {month: "short"})}</small></div>
    <div className="agenda-content"><div className="agenda-event-title"><h3>{event.summary || "Untitled event"}</h3>{event.recurringEventId && <span className="neutral-tag">Recurring</span>}{continued && <span className="neutral-tag">Continues on this day</span>}</div><p><span className="event-time">{eventTime(event)}</span>{event.location && <span> · {event.location}</span>}</p></div>
    {event.htmlLink && <a className="service-link" href={event.htmlLink} target="_blank" rel="noreferrer" aria-label={`Open ${event.summary || "event"} in Google Calendar`}>Open ↗</a>}
  </article>;
}

export default function CalendarWorkspace({onCreateAutomation}: {onCreateAutomation: () => void}) {
  const [service, setService] = useState<ServiceStatus | null>(null);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [days, setDays] = useState(30);
  // Initialize calendar dates in the browser so server timezone cannot change the first day.
  const [anchor, setAnchor] = useState<Date | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [followToday, setFollowToday] = useState(true);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [limited, setLimited] = useState(false);

  useEffect(() => {
    function tick() {
      const current = new Date(); setNow(current);
      if (followToday) setAnchor(previous => previous && sameDay(previous, current) ? previous : startOfDay(current));
    }
    tick(); const timer = window.setInterval(tick, 30000);
    return () => window.clearInterval(timer);
  }, [followToday]);

  useEffect(() => {
    if (!anchor) return;
    const from = anchor;
    let cancelled = false;
    setLoading(true); setError("");
    async function load() {
      try {
        const status = await api.calendarStatus(); if (cancelled) return; setService(status);
        if (status.connected) {
          const result = await api.listCalendarEvents(100, from.toISOString(), addDays(from, days).toISOString());
          if (cancelled) return;
          setEvents(result.items); setLimited(result.limit_reached);
        } else {setEvents([]); setLimited(false);}
        setError("");
      } catch (e: any) {if (!cancelled) {setEvents([]);setLimited(false);setError(e.message);}}
      finally {if (!cancelled) setLoading(false);}
    }
    load(); const timer = window.setInterval(load, 30000);
    return () => {cancelled = true; window.clearInterval(timer);};
  }, [anchor, days, revision]);

  async function connect() {try {const result = await api.startConnection("google_calendar"); window.location.assign(result.authorization_url);} catch (e: any) {setError(e.message);}}
  function move(direction: number) {setFollowToday(false);setAnchor(previous => previous ? addDays(previous, days * direction) : startOfDay(new Date()));}
  function today() {const current = new Date();setNow(current);setFollowToday(true);setAnchor(startOfDay(current));}
  if (!anchor || !now) return <div className="loading-state" role="status">Loading your calendar…</div>;

  const end = addDays(anchor, days), agenda = calendarAgenda(events, anchor, days), isToday = sameDay(anchor, now);
  const dayLabel = isToday ? "Today" : "Selected day";
  const longDate = (date: Date) => date.toLocaleDateString([], {weekday: "long", month: "long", day: "numeric", year: "numeric"});
  return <div className="service-workspace calendar-workspace">
    <header className="service-header"><div className="service-title"><span className="service-app-icon calendar"><ServiceIcon service="google_calendar" size={25}/></span><div><span className="eyebrow">Your connected workspace</span><h1>Calendar</h1><p>{service?.account || "A clear view of what is coming up."}</p></div></div><div className="service-header-actions"><button className="secondary-button icon-label" onClick={() => setRevision(v => v+1)} disabled={loading}><ServiceIcon service="refresh" size={15}/>Refresh</button><button className="primary-button" onClick={onCreateAutomation} disabled={!service?.connected}>Create watcher</button></div></header>
    {error && <div className="service-alert" role="alert">{error}</div>}
    {!loading && !service?.connected && !error ? <div className="service-empty"><ServiceIcon service="google_calendar" size={38}/><h2>Bring your calendar into Jiraya</h2><p>Connect your Google account to see upcoming events and watch for changes.</p><button className="primary-button" onClick={connect}>Connect Google Calendar</button></div> : <>
      <div className="service-stats"><div><span>{dayLabel}</span><strong>{loading || error ? "…" : agenda.selected.length}<small>{agenda.selected.length === 1 ? "event" : "events"}</small></strong></div><div><span>{isToday ? "Upcoming" : "Later in this window"}</span><strong>{loading || error ? "…" : agenda.upcoming.length}<small>{agenda.upcoming.length === 1 ? "event" : "events"}</small></strong><small>After {anchor.toLocaleDateString([], {month: "short", day: "numeric"})}</small></div><ServiceMonitor service={service} label="Calendar"/></div>
      <section className="agenda-panel" aria-label={`${dayLabel} events`}>
        <div className="section-header"><div><span className="eyebrow">{dayLabel}</span><h2>{longDate(anchor)}</h2></div><div className="agenda-controls"><button className="subtle-button" aria-label="Previous date window" onClick={() => move(-1)}>←</button><button className="subtle-button" onClick={today}>Today</button><button className="subtle-button" aria-label="Next date window" onClick={() => move(1)}>→</button><select aria-label="Calendar date range" value={days} onChange={e => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select></div></div>
        {loading && <div className="loading-state" role="status">Loading your calendar…</div>}
        {!loading && !error && (!agenda.selected.length ? <div className="service-empty compact calendar-clear-day"><ServiceIcon service="google_calendar" size={30}/><h3>{isToday ? "Nothing scheduled today" : "Nothing scheduled on this day"}</h3><p>{agenda.upcoming.length ? "Your upcoming events are listed below." : "You have room to focus."}</p></div> : <div className="agenda-list">{agenda.selected.map(event => <EventRow key={event.id} event={event} date={anchor} continued={eventDate(event) < anchor}/>)}</div>)}
      </section>
      <section className="agenda-panel calendar-upcoming" aria-label="Upcoming events">
        <div className="section-header"><div><span className="eyebrow">{isToday ? "Upcoming" : "Later in this window"}</span><h2>{addDays(anchor, 1).toLocaleDateString([], {month: "short", day: "numeric"})} — {addDays(end, -1).toLocaleDateString([], {month: "short", day: "numeric", year: "numeric"})}</h2></div><span className="neutral-tag">{loading || error ? "…" : agenda.upcoming.length} {agenda.upcoming.length === 1 ? "event" : "events"}</span></div>
        {!loading && !error && (!agenda.upcoming.length ? <div className="service-empty compact"><h3>{limited ? "No upcoming events in the loaded results" : "No upcoming events in this range"}</h3><p>Choose a longer range to look further ahead.</p></div> : agenda.groups.map(group => <section className="agenda-day-group" key={group.date.toISOString()} aria-label={longDate(group.date)}><h3 className="agenda-day-heading">{sameDay(group.date, addDays(startOfDay(now), 1)) ? "Tomorrow · " : ""}{longDate(group.date)}</h3>{group.events.map(event => <EventRow key={event.id} event={event} date={group.date}/>)}</section>))}
        {!loading && limited && <p className="list-note">Showing the first 100 events in the selected window. Choose a shorter range to see the rest.</p>}
      </section>
      <p className="workspace-note">Times use your device timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}). Recurring events appear only within the selected range.</p>
    </>}
  </div>;
}
