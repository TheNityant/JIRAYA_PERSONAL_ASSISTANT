"use client";
import {useCallback, useEffect, useState} from "react";
import {api, API_BASE} from "@/lib/api";
import ServiceIcon from "./ServiceIcon";
import WorkspaceCheckpoint from "./WorkspaceCheckpoint";

type DeviceStatus = {configured:boolean; account_id?:string|null; calendar_account?:string|null; enabled:boolean; active:boolean; last_check:string|null; error:string|null; recent:Array<{key?:string; event_id:string; status:string; reason?:string; start:string}>};
type Meeting = {id:string; summary:string; url:string; start:string; end:string};
const retryable = ["FAILED","WAITING_FOR_AUTH","KNOCKING","CANCELLED","CLOSED","UNVERIFIED","REVIEW_REQUIRED"];

export default function MeetWorkspace({accountId, calendarConnected, onConnectCalendar}: {accountId:string; calendarConnected:boolean; onConnectCalendar:()=>void}) {
  const [paired,setPaired]=useState(false);
  const [device,setDevice]=useState<DeviceStatus|null>(null);
  const [calendarAccount,setCalendarAccount]=useState("");
  const [feedStatus,setFeedStatus]=useState("");
  const [events,setEvents]=useState<Meeting[]>([]);
  const [feedError,setFeedError]=useState("");
  const [deviceError,setDeviceError]=useState("");
  const [actionError,setActionError]=useState("");
  const [busy,setBusy]=useState(false);
  const [renewing,setRenewing]=useState(false);
  const [consent,setConsent]=useState(false);
  const [revision,setRevision]=useState(0);
  const onPairingChange=useCallback((value:boolean)=>{setPaired(value);setConsent(false);setDevice(null);setRevision(v=>v+1);},[]);
  const local=useCallback(async(path:string,method="GET",body?:unknown)=>{
    let saved;
    try{saved=JSON.parse(localStorage.getItem(`jiraya.companion:${accountId}`)||"null");}catch{throw Error("Saved device pairing is invalid. Forget pairing and reconnect below.");}
    if(!saved?.token)throw Error("Connect this computer using the pairing controls above.");
    const port=Number(saved.port);if(!Number.isInteger(port)||port<1024||port>65535)throw Error("Invalid companion port. Reconnect this computer.");
    const options:RequestInit & {targetAddressSpace:string}={method,cache:"no-store",targetAddressSpace:"loopback",signal:AbortSignal.timeout(method==="GET"?10000:60000),headers:{Authorization:`Bearer ${saved.token}`,"Content-Type":"application/json"},...(body===undefined?{}:{body:JSON.stringify(body)})};
    let response;
    try{response=await fetch(`http://127.0.0.1:${port}${path}`,options);}catch{throw Error("Local helper is unreachable. Start it in VS Code and allow this site's local-network access.");}
    if(response.status===404)throw Error("This helper does not support Meet attendance. Update the Jiraya extension to 0.5.1 or later and restart its helper.");
    if(response.status===401)throw Error("Device pairing was rejected. Forget pairing and reconnect this computer.");
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:"The local helper could not complete this request.");
    if(path==="/meeting/status"&&!("account_id" in data))throw Error("Update the Jiraya extension to 0.5.1 and restart the old helper to use account-aware Meet attendance.");
    return data;
  },[accountId]);
  useEffect(()=>{
    let cancelled=false, checking=false;
    const refresh=async()=>{
      if(checking)return;checking=true;
      try{
        const [feed,state]=await Promise.allSettled([api.meetEvents(),paired?local("/meeting/status"):Promise.resolve(null)]);
        if(cancelled)return;
        if(feed.status==="fulfilled"){
          setCalendarAccount(feed.value.calendar_account||"");setFeedStatus(feed.value.status);
          setEvents(feed.value.status==="READY"?feed.value.items:[]);setFeedError("");
        }else{setEvents([]);setFeedStatus("");setCalendarAccount("");setFeedError(feed.reason.message);}
        if(state.status==="fulfilled"){setDevice(state.value);setDeviceError("");}
        else{setDevice(null);setDeviceError(state.reason.message);}
      }finally{checking=false;}
    };
    void refresh();const timer=setInterval(refresh,20000);return()=>{cancelled=true;clearInterval(timer);};
  },[paired,revision,local]);
  const otherAccount=!!device?.configured&&device.account_id!==accountId;
  const otherCalendar=!!device?.configured&&!otherAccount&&!!calendarAccount&&!!device.calendar_account&&device.calendar_account!==calendarAccount;
  const ownDevice=otherAccount||otherCalendar?null:device;
  const ready=calendarConnected&&feedStatus==="READY";
  async function enable(){
    if(!consent||busy||!ready||!device)return;setBusy(true);setActionError("");
    try{
      const apiBase=new URL(API_BASE,window.location.origin);
      if(apiBase.origin!==window.location.origin || apiBase.pathname.replace(/\/$/,"")!=="/api/jiraya")throw Error("Use the Jiraya website with its same-origin API proxy for device attendance.");
      const access=await api.registerMeetDevice();
      setDevice(await local("/meeting/connect","POST",{api_base:apiBase.href.replace(/\/$/,""),device_token:access.token}));setRenewing(false);setConsent(false);setRevision(v=>v+1);
    }catch(e:any){setActionError(e.message);}finally{setBusy(false);}
  }
  async function pause(){setBusy(true);setActionError("");try{setDevice(await local("/meeting/pause","POST"));setConsent(false);}catch(e:any){setActionError(e.message);}finally{setBusy(false);}}
  async function retry(key:string){setBusy(true);setActionError("");try{setDevice(await local("/meeting/retry","POST",{key}));}catch(e:any){setActionError(e.message);}finally{setBusy(false);}}
  return <section className="service-workspace meet-workspace">
    <header className="section-header"><div><span className="eyebrow">Calendar → Google Meet</span><h1>Meet attendance</h1><p>Join scheduled meetings from your primary Calendar using this computer.</p></div><button className="secondary-button" disabled={busy} onClick={()=>setRevision(v=>v+1)}>Refresh</button></header>
    <WorkspaceCheckpoint accountId={accountId} purpose="meet" onPairingChange={onPairingChange}/>
    <div className="meet-setup"><div className="meet-setup-icon"><ServiceIcon service="google_calendar" size={34}/><span>→</span><ServiceIcon service="meet" size={34}/></div><h2>Your meeting, at the right time.</h2><p>Gmail automations can create an event with its meeting link. The paired device checks Calendar every 20 seconds and attempts attendance near the event’s start.</p>
      <ol><li>Connect Google Calendar. Connect Gmail as well if your events come from email automations.</li><li>Create a Calendar event with one valid Google Meet link.</li><li>Pair this computer above and enable attendance below. The host may need to admit you.</li></ol>
      {!calendarConnected&&<button className="primary-button" onClick={onConnectCalendar}>Connect Google Calendar</button>}
      {feedStatus==="EXTERNAL_ACTIONS_DISABLED"&&<p className="service-alert" role="alert">Automatic attendance is disabled on the server. The app administrator must enable external actions.</p>}
      <p>{calendarAccount ? `Calendar account: ${calendarAccount}. ` : ""}The device uses a separate Google browser profile. Sign in manually in that browser on your first meeting. Gmail/Calendar OAuth does not sign that browser in. Keep this computer awake and the helper running; the Jiraya tab can be closed after enabling.</p>
      {otherCalendar&&<p className="service-alert" role="alert">Google Calendar was connected to a different account. Enable attendance again to use the currently connected Calendar.</p>}
      {otherAccount&&<p className="service-alert" role="alert">This helper was enabled for another Jiraya account, or needs an update to identify its account. Enabling here switches attendance to this account. An active meeting must finish first.</p>}
      {ownDevice?.enabled&&!renewing?<><p className="state-badge open">{ownDevice.active?"Meeting in progress":"Automatic attendance enabled"}</p><button className="secondary-button" disabled={busy} onClick={pause}>Pause attendance and leave</button></>:<><label className="meet-consent"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>I authorize this computer to join eligible Google Meet events on my primary Calendar with microphone and camera off.</label><button className="primary-button" disabled={!paired||!consent||busy||!device||!ready||device.active} onClick={enable}>{busy?"Connecting…":"Enable automatic attendance"}</button></>}
      <p className="form-note">Requires Jiraya VS Code extension 0.5.1 or later, Chromium helper setup and Calendar access. Device authorization expires after 30 days; enable attendance again to renew it. This integration provides attendance; recording, transcription and voice replies are not enabled here.</p>
      {ownDevice?.last_check&&<p className="form-note">Last device check: {new Date(ownDevice.last_check).toLocaleString()}</p>}
      {(actionError||feedError||deviceError||ownDevice?.error)&&<p className="service-alert" role="alert">{[actionError,feedError,deviceError,ownDevice?.error].filter(Boolean).join(" ")}</p>}
      {ownDevice?.enabled&&<button className="secondary-button" disabled={busy||!ready||ownDevice.active} onClick={()=>{setRenewing(true);setConsent(false);}}>Renew device authorization</button>}
    </div>
    <div className="meet-events"><h2>Upcoming linked meetings · next 24 hours</h2>{!events.length?<p>{!calendarConnected?"Connect Google Calendar to load meetings.":feedError?"Meetings could not be loaded. Resolve the Calendar error above and refresh.":feedStatus!=="READY"?"Meetings are unavailable until Calendar and server actions are ready.":"No eligible upcoming Meet events were returned. Check Calendar access, the event time and its meeting URL."}</p>:events.map(event=><article key={event.id}><div><strong>{event.summary}</strong><p>{new Date(event.start).toLocaleString()} — {new Date(event.end).toLocaleTimeString()}</p></div><a className="service-link" href={event.url} target="_blank" rel="noreferrer">Meeting link ↗</a></article>)}</div>
    {!!ownDevice?.recent.length&&<div className="meet-events"><h2>Device attendance history</h2>{ownDevice.recent.map((row,index)=><article key={row.key||index}><div><strong>{row.status.replaceAll("_"," ")}</strong><p>{new Date(row.start).toLocaleString()} · {row.reason||"If a device stopped during joining, review the meeting before retrying."}</p></div>{row.key&&retryable.includes(row.status)&&<button className="secondary-button" disabled={busy||!ownDevice.enabled||ownDevice.active||!ready} onClick={()=>retry(row.key!)}>Retry attendance</button>}</article>)}</div>}
  </section>;
}
