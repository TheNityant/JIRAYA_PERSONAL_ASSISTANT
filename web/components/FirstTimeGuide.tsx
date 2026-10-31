"use client";

import {useEffect, useRef, useState} from "react";
import ServiceIcon from "./ServiceIcon";
import type {ConnectionCatalogItem} from "@/lib/api";

const steps = [
  {label:"Welcome", title:"Your services, working together.", text:"Connect your accounts, choose what Jiraya should notice, and decide which actions it may take."},
  {label:"Connect", title:"Start with the accounts you use.", text:"Choose Connect beside a service and approve access on its own website. You can add more services later."},
  {label:"Automate", title:"Turn an incoming email into an action.", text:"In Automations, choose New automation or +. Select a trigger, set your matching conditions, then choose an action."},
  {label:"Review", title:"Choose how much control to keep.", text:"Start with Ask before action. Once you have checked a workflow, choose Automatic when you want it to act without approval."},
  {label:"Workspace", title:"Keep your projects and commitments in view.", text:"Use Calendar to review events, Meet to enable attendance on your paired computer, GitHub for repository tools, and Notifications and Obligations to follow what needs attention."},
];

export default function FirstTimeGuide({accountId, connections, onWorkspace}: {accountId:string; connections:ConnectionCatalogItem[]; onWorkspace:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [step, setStep] = useState(0);
  const storageKey = `jiraya.guide.v1:${accountId}`;
  useEffect(() => {
    let seen = false;
    try {seen = Boolean(localStorage.getItem(storageKey));} catch {}
    if (!seen && !dialog.current?.open) dialog.current?.showModal();
    return () => dialog.current?.close();
  }, [storageKey]);
  function close() {
    try {localStorage.setItem(storageKey,"seen");} catch {}
    dialog.current?.close();
  }
  function open() {setStep(0);dialog.current?.showModal();}
  const current = steps[step];
  return <>
    <button className="guide-launch" onClick={open}>Quick start guide</button>
    <dialog ref={dialog} className="first-time-guide" aria-labelledby="guide-title" onCancel={event=>{event.preventDefault();close();}}>
      <header className="guide-top"><span className="eyebrow">Welcome to Jiraya · {step + 1} of {steps.length}</span><button className="ghost-button" onClick={close} aria-label="Close quick start guide">Close ×</button></header>
      <nav className="guide-progress" aria-label="Guide steps">{steps.map((item,index)=><button key={item.label} onClick={()=>setStep(index)} aria-current={step===index ? "step" : undefined}><span>{index<step ? "✓" : index+1}</span>{item.label}</button>)}</nav>
      <div className="guide-body">
        <h2 id="guide-title">{current.title}</h2><p className="guide-intro">{current.text}</p>
        {step <= 1 && <div className="guide-services">{[{id:"gmail",name:"Gmail",copy:"Match email, prepare replies and track commitments."},{id:"google_calendar",name:"Google Calendar",copy:"Create events and check your availability."},{id:"github",name:"GitHub",copy:"Watch repository activity, create issues and branches."}].map(service=>{
          const connected=connections.some(item=>item.id===service.id && item.status==="CONNECTED");
          return <article key={service.id}><div className={`guide-icon ${service.id}`}><ServiceIcon service={service.id} size={27}/></div><h3>{service.name}</h3><p>{service.copy}</p><span className={connected ? "guide-connected" : "guide-neutral"}>{connected ? "✓ Connected" : "Connect when ready"}</span></article>;
        })}</div>}
        {step===0 && <div className="guide-note">One example: a meeting request arrives in Gmail → Jiraya understands the date and time → your Calendar gets an event.</div>}
        {step===1 && <div className="guide-note"><strong>Your setup stays with you.</strong> Connections and automations are saved to your Jiraya account. If a provider’s authorization expires or is revoked, reconnect that service. Start with one service; all three are optional.</div>}
        {step===2 && <><div className="guide-flow">{[{icon:"gmail",title:"Incoming email",detail:"A meeting request"},{icon:"logic",title:"Match + context",detail:"Date and start time supplied"},{icon:"google_calendar",title:"Calendar event",detail:"Your configured action"}].map((item,index)=><article key={item.title}><span className="guide-flow-number">0{index+1}</span><ServiceIcon service={item.icon} size={28}/><h3>{item.title}</h3><p>{item.detail}</p></article>)}</div><div className="guide-note"><strong>For your first Calendar automation:</strong> choose Gmail → Create calendar event, set your timezone and default duration, then save and enable the rule. Send a fresh email with a future meeting date and time from a second account.</div></>}
        {step===3 && <><div className="guide-policies">{[{title:"Draft only",copy:"Prepare a proposal. No external action is performed."},{title:"Ask before action",copy:"Review the proposal and approve it before execution."},{title:"Automatic",copy:"Matching requests may execute immediately, subject to checks."}].map(item=><article key={item.title}><ServiceIcon service="check"/><div><h3>{item.title}</h3><p>{item.copy}</p></div></article>)}</div><div className="guide-note">Watch Notifications for results or requests for attention. Check the actual email, event or repository to confirm your first test. Background processing takes time; it is not instant.</div></>}
        {step===4 && <><div className="guide-policies"><article><ServiceIcon service="github" size={25}/><div><h3>Push several local projects</h3><p>In GitHub, install the VS Code extension and choose Connect with VS Code. Approve your computer once, add your projects, and enable each repository’s checkpoint policy.</p></div></article><article><ServiceIcon service="notify" size={25}/><div><h3>Follow what happens next</h3><p>Notifications show results and approval requests. Obligations track commitments and later evidence about their completion.</p></div></article></div><div className="guide-note">Cloud automations need the backend running. Pushing local files also needs your computer and its local helper running. Device pairing is remembered in the same browser; new devices or cleared storage need pairing again.</div></>}
      </div>
      <footer className="guide-footer"><button className="ghost-button" onClick={close}>Skip for now</button><div>{step>0 && <button className="secondary-button" onClick={()=>setStep(step-1)}>Back</button>}{step<steps.length-1 ? <button className="primary-button" onClick={()=>setStep(step+1)}>Next →</button> : <button className="primary-button" onClick={()=>{close();onWorkspace();}}>Open my workspace →</button>}</div></footer>
      <p className="guide-reopen">You can reopen this walkthrough from Quick start guide.</p>
    </dialog>
  </>;
}
