"use client";
import {useEffect,useRef,useState} from "react";
import {api,MeetBrowserInput,MeetBrowserSetup} from "@/lib/api";

const active=(status?:string)=>!!status&&["QUEUED","STARTING","OPEN","VERIFYING"].includes(status);
const reasons:Record<string,string>={SETUP_TIMEOUT:"The connection expired. Open a new connection to continue.",WORKER_LOST:"The meeting server lost contact. Try connecting again after it recovers.",VIEW_CLOSED:"The connection closed after its view was inactive.",JIRAYA_SIGNED_OUT:"Your Jiraya sign-in ended. Sign in again to reconnect.",CALENDAR_CHANGED:"Your Calendar account changed. Connect the current Google account.",BROWSER_SESSION_NOT_REUSABLE:"Google sign-in did not survive verification in the attendance browser. Attendance remains blocked. Try another Google verification method or contact the app operator.",BROWSER_RUNTIME_UNAVAILABLE:"The AWS browser could not start or load Google. Check the meeting server before retrying.",UNSUPPORTED_BROWSER_PAGE:"The browser left the supported Google sign-in pages. Open a new connection.",WORKER_SHUTDOWN:"The meeting server restarted during setup. Open a new connection."};

export default function MeetBrowserConnection({available,paused,calendarConnected,onConnected}:{available:boolean;paused:boolean;calendarConnected:boolean;onConnected:()=>void}){
  const [session,setSession]=useState<MeetBrowserSetup|null>(null),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(""),[text,setText]=useState("");
  const current=useRef<MeetBrowserSetup|null>(null),sequence=useRef(1),chain=useRef(Promise.resolve()),failed=useRef(false),notified=useRef("");
  const connectedCallback=useRef(onConnected);connectedCallback.current=onConnected;
  function update(value:MeetBrowserSetup){current.current=value;sequence.current=Math.max(sequence.current,value.next_sequence);setSession(value);}
  useEffect(()=>{
    const close=()=>{const row=current.current;if(row&&active(row.status))void api.cancelMeetBrowser(row.id).catch(()=>{});};
    window.addEventListener("pagehide",close);return()=>{window.removeEventListener("pagehide",close);close();current.current=null;};
  },[]);
  useEffect(()=>{
    if(!session||!active(session.status))return;
    let cancelled=false,waiting=false;
    async function poll(){if(waiting)return;waiting=true;try{const value=await api.meetBrowser(session!.id);if(!cancelled){update(value);if(value.status==="CONNECTED"&&notified.current!==value.id){notified.current=value.id;connectedCallback.current();}}}catch(e:any){if(!cancelled)setError(e.message);}finally{waiting=false;}}
    void poll();const timer=setInterval(poll,1000);return()=>{cancelled=true;clearInterval(timer);};
  },[session?.id,session?.status]);
  async function start(){setBusy(true);setError("");failed.current=false;try{sequence.current=1;update(await api.startMeetBrowser());setConsent(false);}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  async function close(){if(!session)return;setBusy(true);try{update(await api.cancelMeetBrowser(session.id));setText("");}catch(e:any){setError(e.message);}finally{setBusy(false);}}
  function send(event:MeetBrowserInput){
    const row=current.current;if(row?.status!=="OPEN"||failed.current)return;
    // Serialize writes: typing order survives requests completing at different times.
    chain.current=chain.current.then(async()=>{if(failed.current||current.current?.id!==row.id||current.current?.status!=="OPEN")return;const result=await api.meetBrowserInput(row.id,sequence.current,[event]);if(current.current?.id===row.id)sequence.current=result.next_sequence;}).catch(()=>{failed.current=true;setError("Browser input could not be delivered. Close and reopen the connection before typing again.");});
  }
  function key(e:React.KeyboardEvent<HTMLDivElement>){
    if(e.target!==e.currentTarget)return;
    const supported=["Enter","Tab","Backspace","Delete","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End","Escape"];
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="a"){e.preventDefault();send({kind:"key",key:"Control+A"});}
    else if(!e.ctrlKey&&!e.metaKey&&!e.altKey&&supported.includes(e.key)){e.preventDefault();send({kind:"key",key:e.key==="Tab"&&e.shiftKey?"Shift+Tab":e.key});if(e.key==="Escape")e.currentTarget.blur();}
    else if(!e.ctrlKey&&!e.metaKey&&!e.altKey&&e.key.length===1){e.preventDefault();send({kind:"text",text:e.key});}
  }
  return <div className="meet-browser-connection">
    <h3>Connect your Google meeting browser</h3>
    <p>Initial setup opens a temporary view of your isolated AWS browser here. Complete Google sign-in and any two-factor check yourself. The saved session is reused across meetings; reconnect if Google invalidates it.</p>
    {!available&&<p role="status">The browser connection portal is not available on the meeting server yet.</p>}
    {!paused&&<p role="status">Pause cloud attendance and wait for your active call to leave before connecting the browser.</p>}
    {!active(session?.status)&&<><label className="meet-consent"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>I trust this Jiraya server to relay the Google browser’s screen and my keyboard input. This is a remote AWS browser; the server operator has privileged access. Temporary screen/input data is encrypted and erased by the running service after use or expiry.</label><button className="secondary-button" disabled={!available||!paused||!calendarConnected||!consent||busy} onClick={start}>Open Google meeting browser</button></>}
    {session&&<div role="status"><p>Browser connection: {session.status.replaceAll("_"," ")}. Google account required: {session.calendar_account}.</p>{active(session.status)&&<p>Closes by {new Date(session.expires_at).toLocaleTimeString()}. Closing this panel or signing out also closes the temporary view.</p>}{session.status==="QUEUED"&&<p>Waiting for the meeting server to finish its current work.</p>}{session.status==="VERIFYING"&&<p>Saving your session and verifying that a new attendance browser can reuse it. No meeting is being joined.</p>}{session.status==="CONNECTED"&&<p>Google browser connected and reuse verified. You can now enable cloud attendance.</p>}{session.reason&&<p>{reasons[session.reason]||"The temporary browser connection has closed."}</p>}</div>}
    {session?.status==="OPEN"&&session.frame&&<>
      <p>Remote page origin: <strong>{session.frame.origin}</strong>. This image shows the actual remote page. Click a field, then type; Enter and Tab work. Escape releases keyboard focus. Do not use this view if you do not trust the app operator.</p>
      <div role="application" aria-label="Remote Google browser" tabIndex={0} onKeyDown={key} onPaste={e=>{e.preventDefault();send({kind:"text",text:e.clipboardData.getData("text").slice(0,512)});}} style={{maxWidth:"100%",outline:"1px solid #778",overflow:"hidden"}}>
        <img src={`data:image/jpeg;base64,${session.frame.image}`} alt="Your temporary Google sign-in browser on AWS" draggable={false} width={session.frame.width} height={session.frame.height} style={{display:"block",width:"100%",height:"auto",cursor:"crosshair"}} onClick={e=>{e.currentTarget.parentElement?.focus();const r=e.currentTarget.getBoundingClientRect();send({kind:"click",x:Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),y:Math.min(1,Math.max(0,(e.clientY-r.top)/r.height))});}} onWheel={e=>send({kind:"scroll",delta:Math.round(Math.max(-800,Math.min(800,e.deltaY)))})}/>
      </div>
      <p>For mobile keyboards or pasted text, first select the remote Google field above, then send text below. This input is masked locally and cleared after sending.</p>
      <label>Text for the selected Google field<input type="password" autoComplete="off" value={text} maxLength={512} onChange={e=>setText(e.target.value)}/></label><button className="secondary-button" disabled={!text||busy||failed.current} onClick={()=>{send({kind:"text",text});setText("");}}>Send text to Google field</button>
      <button className="secondary-button" onClick={()=>send({kind:"key",key:"Enter"})}>Press Enter in Google browser</button>
    </>}
    {active(session?.status)&&<button className="secondary-button" disabled={busy} onClick={close}>Close browser connection</button>}
    {error&&<p role="alert" className="service-alert">{error}</p>}
  </div>;
}
