"use client";

export default function SchedulingSettings({value, connected, onChange}: {value: unknown; connected: boolean; onChange: (value: Record<string, unknown>) => void}) {
  const config = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const enabled = config.enabled === true;
  const set = (key: string, value: unknown) => onChange({...config, [key]: value});
  const weekdays = Array.isArray(config.weekdays) ? config.weekdays as number[] : [0,1,2,3,4];
  return <fieldset className="scheduling-settings"><legend>Calendar-backed scheduling reply</legend>
    <label className="toggle-row"><input type="checkbox" checked={enabled} disabled={!connected && !enabled} onChange={e => onChange({time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone, duration_minutes:60, work_start:"09:00", work_end:"18:00", weekdays:[0,1,2,3,4], horizon_days:7, notice_minutes:120, calendar_ids:["primary"], assume_timezone:false, ...config, enabled:e.target.checked})}/>Check availability and suggest meeting times</label>
    {!connected && <p className="form-note">Connect Google Calendar before enabling scheduling replies.</p>}
    {enabled && <>
      <p className="form-note">Generates a fixed availability reply from Calendar free/busy. Only selected calendars are checked. Booking is optional and uses your primary calendar.</p>
      <label>Scheduling timezone<input aria-label="Scheduling timezone" value={String(config.time_zone || "")} onChange={e => set("time_zone",e.target.value)} placeholder="Asia/Kolkata"/></label>
      <div className="two-column"><label>Working day starts<input aria-label="Working day starts" type="time" value={String(config.work_start || "09:00")} onChange={e => set("work_start",e.target.value)}/></label><label>Working day ends<input aria-label="Working day ends" type="time" value={String(config.work_end || "18:00")} onChange={e => set("work_end",e.target.value)}/></label></div>
      <div className="scheduling-weekdays">{["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map((day,index) => <label key={day}><input type="checkbox" checked={weekdays.includes(index)} onChange={e => set("weekdays",e.target.checked ? [...weekdays,index].sort() : weekdays.filter(value => value !== index))}/>{day}</label>)}</div>
      <div className="two-column"><label>Default meeting minutes<input aria-label="Default meeting minutes" type="number" min={15} max={120} value={Number(config.duration_minutes ?? 60)} onChange={e => set("duration_minutes",Number(e.target.value))}/></label><label>Search ahead (days)<input aria-label="Search ahead days" type="number" min={1} max={14} value={Number(config.horizon_days ?? 7)} onChange={e => set("horizon_days",Number(e.target.value))}/></label></div>
      <label>Minimum notice (minutes)<input aria-label="Minimum notice minutes" type="number" min={0} max={10080} value={Number(config.notice_minutes ?? 120)} onChange={e => set("notice_minutes",Number(e.target.value))}/></label>
      <label>Calendar IDs (comma separated)<input aria-label="Scheduling calendar IDs" value={Array.isArray(config.calendar_ids) ? config.calendar_ids.join(", ") : "primary"} onChange={e => set("calendar_ids",e.target.value.split(",").map(value => value.trim()))}/><span className="form-note">Use primary for your main calendar. Add other Google Calendar IDs if they should block availability.</span></label>
      <label>Gap around existing meetings (minutes)<input aria-label="Meeting buffer minutes" type="number" min={0} max={120} value={Number(config.buffer_minutes ?? 0)} onChange={e => set("buffer_minutes",Number(e.target.value))}/><span className="form-note">Use 60 to leave an hour before and after busy events.</span></label>
      <label className="toggle-row"><input type="checkbox" checked={config.book_proposed === true} onChange={e => set("book_proposed", e.target.checked)}/>Book one explicitly proposed free time, then reply</label>
      {config.book_proposed === true && <p className="form-note">Add primary to checked calendars. With approval or Automatic, Jiraya creates and verifies the event before sending a confirmation. Busy, date-only or missing-time requests receive suggestions without booking. Calendar invitations are not sent. Draft only creates neither an event nor a sent reply.</p>}
      <label className="toggle-row"><input type="checkbox" checked={config.assume_timezone === true} onChange={e => set("assume_timezone",e.target.checked)}/>Use my scheduling timezone when the email omits one</label>
      <p className="form-note">Start with Draft only or Ask before action. Automatic also requires the existing action-safety check and external actions enabled. Availability is checked again before sending; unclear requests or Calendar errors require review.</p>
    </>}
  </fieldset>;
}
