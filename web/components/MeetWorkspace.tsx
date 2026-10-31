"use client";
import {useEffect, useState} from "react";
import {api, API_BASE} from "@/lib/api";
import ServiceIcon from "./ServiceIcon";

type DeviceStatus = {configured:boolean; enabled:boolean; active:boolean; last_check:string|null; error:string|null; recent:Array<{key?:string; event_id:string; status:string; reason?:string; start:string}>};
type Meeting = {id:string; summary:string; url:string; start:string; end:string};

export default function MeetWorkspace({accountId, onPair}: {accountId:string; onPair:()=>void}) {
  const [pairing,setPairing]=useState<{token:string;port:string}|null>(null);
  const [device,setDevice]=useState<DeviceStatus|null>(null);
  const [calendarAccount,setCalendarAccount]=useState("");
  const [events,setEvents]=useState<Meeting[]>([]);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [consent,setConsent]=useState(false);
  const [revision,setRevision]=useState(0);
  async function local(path:string,method="GET",body?:unknown) {
    const saved=pairing || JSON.parse(localStorage.getItem(`jiraya.companion:${accountId}`) || "null");
    if(!saved)throw Error("Pair this computer from the GitHub workspace first.");
    const port=Number(saved.port);if(!Number.isInteger(port)||port<1024||port>65535)throw Error("Invalid companion port. Reconnect this computer.");
    const options:RequestInit & {targetAddressSpace:string}={method,targetAddressSpace:"loopback",signal:AbortSignal.timeout(method==="GET"?10000:60000),headers:{Authorization:`Bearer ${saved.token}`,"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})};
    const response=await fetch(`http://127.0.0.1:${port}${path}`,options);
    const data=await response.json();if(response.status===404)throw Error("Update the Jiraya VS Code extension to 0.5.0, restart the old helper, and reconnect this computer.");if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:"Update the VS Code extension to 0.5.0 and reconnect.");return data;
  }
  useEffect(()=>{
    try{setPairing(JSON.parse(localStorage.getItem(`jiraya.companion:${accountId}`)||"null"));}catch{setPairing(null);}
    let cancelled=false;
    const refresh=async()=>{
      const [feed,state]=await Promise.allSettled([api.meetEvents(),local("/meeting/status")]);
      if(cancelled)return;
      setCalendarAccount(feed.status==="fulfilled"?feed.value.calendar_account||"":"");
      setEvents(feed.status==="fulfilled"?feed.value.items:[]);
      setDevice(state.status==="fulfilled"?state.value:null);
      setError([feed,state].filter(result=>result.status==="rejected").map(result=>(result as PromiseRejectedResult).reason.message).join(" "));

    };
    void refresh();const timer=setInterval(refresh,20000);return()=>{cancelled=true;clearInterval(timer);};
  },[accountId,revision]);
  async function enable(){
    if(!consent||busy)return;setBusy(true);setError("");
    try{
      const apiBase=new URL(API_BASE,window.location.origin);
      if(apiBase.origin!==window.location.origin || apiBase.pathname.replace(/\/$/,"")!=="/api/jiraya")throw Error("Use the deployed website with its same-origin API proxy for device attendance.");
      const access=await api.registerMeetDevice();
      setDevice(await local("/meeting/connect","POST",{api_base:apiBase.href.replace(/\/$/,""),device_token:access.token}));setRevision(v=>v+1);
    }catch(e:any){setError(e.message);}finally{setBusy(false);}
  }
  async function pause(){setBusy(true);try{setDevice(await local("/meeting/pause","POST"));setConsent(false);}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  async function retry(key:string){setBusy(true);try{setDevice(await local("/meeting/retry","POST",{key}));}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  return <section className="service-workspace meet-workspace">
    <header className="section-header"><div><span className="eyebrow">Calendar → Google Meet</span><h1>Meet attendance</h1><p>Join scheduled meetings from your primary Calendar using this computer.</p></div><button className="secondary-button" disabled={busy} onClick={()=>setRevision(v=>v+1)}>Refresh</button></header>
    <div className="meet-setup"><div className="meet-setup-icon"><ServiceIcon service="google_calendar" size={34}/><span>→</span><ServiceIcon service="meet" size={34}/></div><h2>Your meeting, at the right time.</h2><p>Gmail automations can create an event with its meeting link. The paired device checks Calendar every 20 seconds and attempts attendance near the event’s start.</p>
      <ol><li>Connect Gmail and Google Calendar.</li><li>Create a Calendar event from a meeting email with a valid Google Meet link.</li><li>Pair this computer and enable attendance below. Approve access with the meeting host when required.</li></ol>
      <p>{calendarAccount ? `Calendar account: ${calendarAccount}. ` : ""}The device uses a separate Google browser profile. Sign in manually in that browser on your first meeting. Gmail/Calendar OAuth does not sign that browser in. Keep this computer and helper running; the Jiraya tab can be closed after enabling.</p>
      {!pairing?<button className="primary-button" onClick={onPair}>Pair this computer in GitHub →</button>:device?.enabled?<><p className="state-badge open">{device.active?"Meeting in progress":"Automatic attendance enabled"}</p><button className="secondary-button" disabled={busy} onClick={pause}>Pause attendance and leave</button></>:<><label className="meet-consent"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>I authorize this computer to join eligible Google Meet events on my primary Calendar with microphone and camera off.</label><button className="primary-button" disabled={!consent||busy||!device} onClick={enable}>{busy?"Connecting…":"Enable automatic attendance"}</button></>}
      <p className="form-note">Requires Jiraya VS Code extension 0.5.0, Chromium helper setup and Calendar access. Device authorization expires after 30 days. This integration provides attendance; recording, transcription and voice replies are not enabled here.</p>
      {device?.last_check&&<p className="form-note">Last device check: {new Date(device.last_check).toLocaleString()}</p>}
      {(error||device?.error)&&<p className="service-alert" role="alert">{error||device?.error}</p>}
    </div>
    <div className="meet-events"><h2>Upcoming linked meetings · next 24 hours</h2>{!events.length?<p>No eligible upcoming Meet events were returned. Check Calendar access, the event time and its meeting URL.</p>:events.map(event=><article key={event.id}><div><strong>{event.summary}</strong><p>{new Date(event.start).toLocaleString()} — {new Date(event.end).toLocaleTimeString()}</p></div><a className="service-link" href={event.url} target="_blank" rel="noreferrer">Meeting link ↗</a></article>)}</div>
    {!!device?.recent.length&&<div className="meet-events"><h2>Device attendance history</h2>{device.recent.map((row,index)=><article key={index}><div><strong>{row.status.replaceAll("_"," ")}</strong><p>{new Date(row.start).toLocaleString()} · {row.reason||"If a device stopped during joining, review the meeting before retrying."}</p></div>{row.key && ["FAILED","WAITING_FOR_AUTH","KNOCKING","CANCELLED","UNVERIFIED","REVIEW_REQUIRED"].includes(row.status) && <button className="secondary-button" disabled={busy||!device.enabled||device.active} onClick={()=>retry(row.key!)}>Retry attendance</button>}</article>)}</div>}
  </section>;
}
