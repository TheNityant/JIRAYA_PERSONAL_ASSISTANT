"use client";
import { useEffect, useState } from "react";
import { api, CalendarEvent, ServiceStatus } from "@/lib/api";
import ServiceIcon from "./ServiceIcon";
import ServiceMonitor from "./ServiceMonitor";

function localDate(value: string) {return new Date(value.length === 10 ? `${value}T00:00:00` : value);}
function eventDate(event: CalendarEvent) {return localDate(event.start?.dateTime || event.start?.date || "");}
function eventTime(event: CalendarEvent) {
  if (event.start?.date) {
    const last = localDate(event.end?.date || event.start.date); last.setDate(last.getDate() - 1);
    const first = localDate(event.start.date);
    return last > first ? `All day · through ${last.toLocaleDateString([], {month: "short", day: "numeric"})}` : "All day";
  }
  const format: Intl.DateTimeFormatOptions = {hour: "numeric", minute: "2-digit"};
  return `${eventDate(event).toLocaleTimeString([], format)}${event.end?.dateTime ? ` – ${localDate(event.end.dateTime).toLocaleTimeString([], format)}` : ""}`;
}
export default function CalendarWorkspace({onCreateAutomation}: {onCreateAutomation: () => void}) {
  const [service, setService] = useState<ServiceStatus | null>(null);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [days, setDays] = useState(30);
  const [anchor, setAnchor] = useState(() => {const value = new Date(); value.setHours(0, 0, 0, 0); return value;});
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [limited, setLimited] = useState(false);
  const end = new Date(anchor); end.setDate(end.getDate() + days);
  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError("");
    async function load() {
      try {
        const status = await api.calendarStatus(); if (cancelled) return; setService(status);
        if (status.connected) {
          const until = new Date(anchor); until.setDate(until.getDate() + days);
          const result = await api.listCalendarEvents(100, anchor.toISOString(), until.toISOString());
          if (cancelled) return;
          // Deduplicate provider IDs only; distinct recurring instances remain legitimate events.
          setEvents(Array.from(new Map(result.items.map((event) => [event.id, event])).values()));
          setLimited(result.limit_reached);
        } else setEvents([]);
      } catch (e: any) {if (!cancelled) setError(e.message);}
      finally {if (!cancelled) setLoading(false);}
    }
    load(); const timer = window.setInterval(load, 30000);
    return () => {cancelled = true; window.clearInterval(timer);};
  }, [anchor, days, revision]);
  async function connect() {try {const result = await api.startConnection("google_calendar"); window.location.assign(result.authorization_url);} catch (e: any) {setError(e.message);}}
  function move(direction: number) {const next = new Date(anchor); next.setDate(next.getDate() + days*direction); setAnchor(next);}
  const next = events[0];
  return <div className="service-workspace calendar-workspace">
    <header className="service-header"><div className="service-title"><span className="service-app-icon calendar"><ServiceIcon service="google_calendar" size={25}/></span><div><span className="eyebrow">Your connected workspace</span><h1>Calendar</h1><p>{service?.account || "A clear view of what is coming up."}</p></div></div><div className="service-header-actions"><button className="secondary-button icon-label" onClick={() => setRevision((v) => v+1)} disabled={loading}><ServiceIcon service="refresh" size={15}/>Refresh</button><button className="primary-button" onClick={onCreateAutomation} disabled={!service?.connected}>Create watcher</button></div></header>
    {error && <div className="service-alert" role="alert">{error}</div>}
    {!loading && !service?.connected ? <div className="service-empty"><ServiceIcon service="google_calendar" size={38}/><h2>Bring your calendar into Jiraya</h2><p>Connect your Google account to see upcoming events and watch for changes.</p><button className="primary-button" onClick={connect}>Connect Google Calendar</button></div> : <>
      <div className="service-stats"><div><span>In this date window</span><strong>{loading ? "…" : events.length}<small>events</small></strong></div><div><span>First in this window</span><strong className="stat-text">{next?.summary || (loading ? "Loading…" : "No upcoming events")}</strong><small>{next ? eventDate(next).toLocaleDateString([], {weekday: "short", month: "short", day: "numeric"}) : "You have room to focus"}</small></div><ServiceMonitor service={service} label="Calendar"/></div>
      <section className="agenda-panel"><div className="section-header"><div><span className="eyebrow">Agenda</span><h2>{anchor.toLocaleDateString([], {month: "short", day: "numeric"})} — {new Date(end.getTime()-1).toLocaleDateString([], {month: "short", day: "numeric", year: "numeric"})}</h2></div><div className="agenda-controls"><button className="subtle-button" aria-label="Previous date window" onClick={() => move(-1)}>←</button><button className="subtle-button" onClick={() => {const now=new Date();now.setHours(0,0,0,0);setAnchor(now);}}>Today</button><button className="subtle-button" aria-label="Next date window" onClick={() => move(1)}>→</button><select aria-label="Calendar date range" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select></div></div>
        {loading && <div className="loading-state" role="status">Loading your calendar…</div>}
        {!loading && !error && !events.length && <div className="service-empty compact"><ServiceIcon service="google_calendar" size={30}/><h3>Nothing scheduled in this window</h3><p>Try another date range or enjoy a clear calendar.</p></div>}
        {!loading && <div className="agenda-list">{events.map((event, index) => {
          const date = eventDate(event), previous = index ? eventDate(events[index-1]) : null;
          const sameDay = previous?.toDateString() === date.toDateString();
          return <article className="agenda-row" key={event.id}><div className={`agenda-date ${sameDay ? "repeat-date" : ""}`}><span>{date.toLocaleDateString([], {weekday: "short"})}</span><strong>{date.getDate()}</strong><small>{date.toLocaleDateString([], {month: "short"})}</small></div><div className="agenda-content"><div className="agenda-event-title"><h3>{event.summary || "Untitled event"}</h3>{event.recurringEventId && <span className="neutral-tag">Recurring</span>}</div><p><span className="event-time">{eventTime(event)}</span>{event.location && <span> · {event.location}</span>}</p></div>{event.htmlLink && <a className="service-link" href={event.htmlLink} target="_blank" rel="noreferrer" aria-label={`Open ${event.summary || "event"} in Google Calendar`}>Open ↗</a>}</article>;
        })}</div>}
        {limited && <p className="list-note">Showing the first 100 events. Choose a shorter date window to see the rest.</p>}
      </section><p className="workspace-note">Events stay within your selected date range. Recurring events appear only when they occur in that window.</p>
    </>}
  </div>;
}
