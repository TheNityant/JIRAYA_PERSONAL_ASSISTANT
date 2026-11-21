"use client";

import {useEffect, useMemo, useState} from "react";
import {api, EmailActivity, EmailConversation, EmailOverview, EmailWatch} from "@/lib/api";

type Filter = "ALL" | "ASSISTANT_TO_REPLY" | "CLIENT_TO_REPLY" | "NO_REPLY_NEEDED" | "REVIEW";
const labels: Record<string,string> = {
  ASSISTANT_TO_REPLY: "Assistant to reply",
  CLIENT_TO_REPLY: "Client to reply",
  NO_REPLY_NEEDED: "No reply needed",
};

export default function EmailWorkspace({role, onLegacy}: {role: "CLIENT" | "ASSISTANT"; onLegacy?: () => void}) {
  const [overview,setOverview] = useState<EmailOverview | null>(null);
  const [selected,setSelected] = useState<EmailConversation | null>(null);
  const [filter,setFilter] = useState<Filter>("ALL");
  const [search,setSearch] = useState("");
  const [invite,setInvite] = useState("");
  const [code,setCode] = useState("");
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [draft,setDraft] = useState("");
  const [watches,setWatches] = useState<EmailWatch[]>([]);
  const [activity,setActivity] = useState<EmailActivity[]>([]);
  const [watchName,setWatchName] = useState("");
  const [watchCondition,setWatchCondition] = useState("");
  const [watchInstructions,setWatchInstructions] = useState("");
  const [watchAction,setWatchAction] = useState<"NOTIFY"|"DRAFT_REPLY">("NOTIFY");
  const [watchPolicy,setWatchPolicy] = useState<"REQUIRED"|"AUTO">("REQUIRED");
  const [previewWatch,setPreviewWatch] = useState("");
  const [previewSubject,setPreviewSubject] = useState("Could we schedule a meeting?");
  const [previewBody,setPreviewBody] = useState("Hi, could we meet Friday at 2 PM? Please let me know.");
  const [previewResult,setPreviewResult] = useState("");

  async function load(preserveId?: string) {
    const data = await api.emailOverview();
    setOverview(data);
    if (data.linked) {
      const [savedWatches, history] = await Promise.all([api.emailWatches(), api.emailActivity()]);
      setWatches(savedWatches);
      setActivity(history);
    }
    const current = preserveId || selected?.id;
    if (current && data.items.some(item => item.id === current)) {
      const detail = await api.emailDetail(current);
      setSelected(detail);
      setDraft(detail.draft_body || "");
    } else {
      setSelected(null);
      setDraft("");
    }
  }

  useEffect(() => {void load().catch(e=>setError(e.message));}, []);
  const items = overview?.items || [];
  const visible = useMemo(() => items.filter(item =>
    (filter === "ALL" || (filter === "REVIEW" ? item.review_required : item.responsibility === filter)) &&
    `${item.subject} ${item.sender} ${item.summary}`.toLowerCase().includes(search.trim().toLowerCase())
  ), [items,filter,search]);

  async function action(operation: () => Promise<unknown>, success: string, currentId?: string) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {await operation(); await load(currentId); setNotice(success);}
    catch (e: any) {setError(e.message || "Something went wrong.");}
    finally {setBusy(false);}
  }
  async function open(id:string) {
    try {setError(""); const row = await api.emailDetail(id); setSelected(row); setDraft(row.draft_body || "");}
    catch (e:any) {setError(e.message);}
  }
  const active = items.filter(i=>i.status === "OPEN");
  const metrics = role === "ASSISTANT" ? [
    ["All conversations",items.length],["Assistant to reply",active.filter(i=>i.responsibility==="ASSISTANT_TO_REPLY").length],
    ["Client attention",active.filter(i=>i.responsibility==="CLIENT_TO_REPLY").length],
    ["Review AI choice",active.filter(i=>i.review_required).length],
  ] : [["Your attention",active.length],["Unread",active.filter(i=>i.unread).length]];

  return <main className="email-shell">
    <header className="email-header">
      <div><span className="email-eyebrow">JIRAYA / SHARED INBOX</span><h1>{role==="ASSISTANT"?"Assistant workspace":"Your attention"}</h1>
        <p>{role==="ASSISTANT" ? `Manage ${overview?.client?.display_name || "your client's"} conversations and review Jiraya's decisions.` : "Decisions and conversations your assistant has passed to you."}</p></div>
      <div className="email-header-actions">
        {role==="CLIENT" && <button className="email-outline" disabled={busy} onClick={()=>action(async()=>{const response=await api.startConnection("gmail");window.location.assign(response.authorization_url);},"Opening Gmail authorization…")}>Connect Gmail ↗</button>}
        {role==="ASSISTANT" && onLegacy && <button className="email-outline" onClick={onLegacy}>Jiraya services ↗</button>}
        {overview?.linked && <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailSync(),"Inbox checked.")}>Sync Gmail</button>}
        {overview?.linked && <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailSeedDemo(),"Sample conversations ready to try again.")}>Reset sample inbox</button>}
      </div>
    </header>
    {error && <div className="email-alert" role="alert">{error}</div>}
    {notice && <div className="email-success" role="status">{notice}</div>}
    {!overview ? <div className="email-empty">Loading your workspace…</div> :
    !overview.linked ? <section className="email-connect">
      <span className="email-eyebrow">ASSISTANT ACCESS</span><h2>Connect to your client's inbox</h2>
      <p>Ask your client to create an invitation code from their workspace. Your account receives access only after you accept it.</p>
      <form onSubmit={e=>{e.preventDefault();void action(()=>api.emailJoin(code.trim()),"Client linked.");}}>
        <input aria-label="Client invitation code" placeholder="Paste client invitation code" value={code} onChange={e=>setCode(e.target.value)} required minLength={16}/>
        <button className="email-primary" disabled={busy}>Connect</button>
      </form>
    </section> : <>
      <div className="email-metrics">{metrics.map(([label,value])=><div className="email-metric" key={String(label)}><strong>{value}</strong><span>{label}</span></div>)}</div>
      {role==="CLIENT" && <details className="email-invite"><summary>Invite your human assistant</summary>
        <p>Create a single-use code valid for 24 hours. Share it only with the assistant you trust to manage your connected inbox.</p>
        <button className="email-outline" disabled={busy} onClick={()=>action(async()=>{const value=await api.emailInvitation();setInvite(value.code);},"Invitation created.")}>Create invitation code</button>
        {invite && <code>{invite}</code>}
        <button className="email-outline" disabled={busy} onClick={()=>{if(window.confirm("Revoke all assistant access to your shared email workspace?"))void action(()=>api.emailRevoke(),"Assistant access revoked.");}}>Revoke assistant access</button>
      </details>}
      {role==="ASSISTANT" && <details className="email-watches">
        <summary>Gmail watches <span>{watches.length} configured</span></summary>
        <p>Watches execute in the client's connected Gmail account. Notify-only never sends. An automatic reply may send under the conditions and instructions you set.</p>
        <div className="email-watch-grid">
          {watches.map(watch=><div className="email-watch-row" key={watch.id}><div><strong>{watch.name}</strong><small>{watch.action==="NOTIFY"?"Notify only":watch.approval_policy==="AUTO"?"Automatic reply":"Review draft"} · {watch.condition}</small></div>
            <div className="email-watch-actions"><button className="email-outline" onClick={()=>{setPreviewWatch(watch.id);setPreviewResult("");}}>Test rule</button><button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailSetWatch(watch.id,!watch.enabled),watch.enabled?"Watch paused.":"Watch enabled.")}>{watch.enabled?"Pause":"Enable"}</button></div></div>)}
        </div>
        {previewWatch&&<form className="email-watch-form email-preview" onSubmit={e=>{e.preventDefault();void action(async()=>{const result=await api.emailPreviewWatch(previewWatch,{sender:"test-sender@example.com",subject:previewSubject,body:previewBody});setPreviewResult(`${result.matches?"Matched":"Did not match"} · ${result.decision} · ${result.reason}. No email was sent.`);},"Watch evaluated without sending email.");}}>
          <h3>Test this watch safely</h3><p>Use sample email text to preview matching and the action Jiraya would choose. This does not send a self-email or run the watch.</p>
          <input aria-label="Sample email subject" required value={previewSubject} onChange={e=>setPreviewSubject(e.target.value)}/>
          <textarea aria-label="Sample email body" required value={previewBody} onChange={e=>setPreviewBody(e.target.value)} rows={3}/>
          <button className="email-outline" disabled={busy}>Preview decision</button>
          {previewResult&&<output>{previewResult}</output>}
        </form>}
        <form className="email-watch-form" onSubmit={e=>{e.preventDefault();void action(async()=>{await api.emailCreateWatch({name:watchName,condition:watchCondition,action:watchAction,instructions:watchInstructions,approval_policy:watchPolicy});setWatchName("");setWatchCondition("");setWatchInstructions("");},"Watch created in the client's Gmail automation.");}}>
          <h3>New Gmail watch</h3>
          <input aria-label="Watch name" required maxLength={255} placeholder="Watch name" value={watchName} onChange={e=>setWatchName(e.target.value)}/>
          <textarea aria-label="When should this watch match" required minLength={8} placeholder="Describe the emails this watch should match…" value={watchCondition} onChange={e=>setWatchCondition(e.target.value)} rows={2}/>
          <div><label>Action <select value={watchAction} onChange={e=>setWatchAction(e.target.value as "NOTIFY"|"DRAFT_REPLY")}><option value="NOTIFY">Notify only</option><option value="DRAFT_REPLY">Prepare a reply</option></select></label>
          {watchAction==="DRAFT_REPLY"&&<label>Reply policy <select value={watchPolicy} onChange={e=>setWatchPolicy(e.target.value as "REQUIRED"|"AUTO")}><option value="REQUIRED">Review before sending</option><option value="AUTO">Automatic under this watch</option></select></label>}</div>
          {watchAction==="DRAFT_REPLY"&&<textarea aria-label="Reply instructions" placeholder="What may Jiraya say? Be specific for automatic replies." value={watchInstructions} onChange={e=>setWatchInstructions(e.target.value)} rows={2}/>}
          <button className="email-primary" disabled={busy}>Save watch</button>
        </form>
      </details>}
      {activity.length>0&&<details className="email-watches email-activity"><summary>Recent Gmail activity <span>{activity.length} updates</span></summary>
        <div className="email-watch-grid">{activity.map(item=><div className="email-watch-row" key={item.id}><div><strong>{item.title}</strong><small>{item.message}</small></div><span className="email-label">{item.category==="ACTION_REQUIRED"?"Needs attention":item.category==="AUTOMATION_EXECUTED"?"Done by Jiraya":"Update"}</span></div>)}</div>
      </details>}
      <section className="email-toolbar">
        <div className="email-tabs" aria-label="Conversation filters">
          {(role==="CLIENT" ? [["ALL","Needs your attention"]] : [["ALL","All"],["ASSISTANT_TO_REPLY","Assistant"],["CLIENT_TO_REPLY","Client"],["NO_REPLY_NEEDED","No reply"],["REVIEW","Review"]]).map(([key,label])=>
            <button className={filter===key?"active":""} key={key} onClick={()=>setFilter(key as Filter)}>{label}</button>)}
        </div>
        <input aria-label="Search conversations" placeholder="Search conversations…" value={search} onChange={e=>setSearch(e.target.value)}/>
      </section>
      <div className="email-layout">
        <section className="email-list" aria-label="Conversations">
          {!visible.length && <div className="email-empty"><h2>No conversations here yet</h2><p>{role==="ASSISTANT"?"Try a different filter or ask your client to load the demo.":"Connect Gmail through Jiraya services, then sync your inbox, or load sample conversations to explore the workflow."}</p>
            <button className="email-primary" disabled={busy} onClick={()=>action(()=>api.emailSeedDemo(),"Sample conversations ready to try again.")}>Try sample inbox</button>
          </div>}
          {visible.map(item=><button className={`email-row ${selected?.id===item.id?"selected":""}`} key={item.id} onClick={()=>void open(item.id)}>
            <span className="email-row-top"><strong>{item.sender}</strong><small>{new Date(item.updated_at).toLocaleDateString()}</small></span>
            <span className="email-row-subject">{item.unread&&<i className="email-unread"/>}{item.subject}</span>
            <span className="email-row-summary">{item.summary || item.reason}</span>
            <span className="email-row-foot"><span className={`email-label ${item.responsibility.toLowerCase()}`}>{labels[item.responsibility]}</span>
              {item.review_required&&<span className="email-label review">Review AI choice</span>}
              {item.status!=="OPEN"&&<span className="email-label handled">{item.status==="HANDLED"?"Handled":item.status==="ON_HOLD"?"On hold":"Check send"}</span>}
              {item.source==="DEMO"&&<span className="email-label demo">Sample</span>}</span>
          </button>)}
        </section>
        <section className="email-detail" aria-label="Conversation detail">
          {!selected ? <div className="email-empty"><h2>Select a conversation</h2><p>Read the thread, understand the assignment, and decide what happens next.</p>
            {items.length>0 && <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailSeedDemo(),"Sample conversations ready to try again.")}>Reset sample inbox</button>}
          </div> : <>
            <div className="email-detail-head"><div><span className="email-eyebrow">{selected.source==="DEMO"?"SAMPLE CONVERSATION · NO REAL EMAIL SENT":"CONNECTED GMAIL"}</span>
              <h2>{selected.subject}</h2><p>From {selected.sender}</p></div>
              {role==="ASSISTANT"&&<button className="email-star" aria-label={selected.important?"Remove important":"Mark important"} disabled={busy} onClick={()=>action(()=>api.emailUpdate(selected.id,{important:!selected.important}),"Importance updated.",selected.id)}>{selected.important?"★":"☆"}</button>}
            </div>
            <div className="email-ai"><span className="email-eyebrow">JIRAYA'S READ</span><p>{selected.summary || "Open the conversation to understand its context."}</p>
              <div><span className={`email-label ${selected.responsibility.toLowerCase()}`}>{labels[selected.responsibility]}</span>
                <span>{selected.reason}</span></div>
              {selected.review_required && <small>AI was uncertain or a new message differed from a human correction. Please review.</small>}
              {role==="ASSISTANT"&&<button className="email-outline email-reclassify" disabled={busy} onClick={()=>action(()=>api.emailClassify(selected.id),"AI rechecked the conversation.",selected.id)}>Recheck with AI</button>}
            </div>
            <div className="email-thread"><h3>Conversation</h3>{selected.messages?.map(message=><article key={message.id}>
              <div><strong>{message.from}</strong><time>{new Date(message.date).toLocaleString()}</time></div>
              <p className="email-message-body">{message.body}</p>
            </article>)}</div>
            {role==="ASSISTANT" && <div className="email-decide"><label>Who should reply next?
                <select value={selected.responsibility} disabled={busy} onChange={e=>action(()=>api.emailUpdate(selected.id,{responsibility:e.target.value as EmailConversation["responsibility"]}),"Assignment updated for both views.",selected.id)}>
                  {Object.keys(labels).map(key=><option value={key} key={key}>{labels[key]}</option>)}
                </select></label></div>}
              <div className="email-compose"><h3>Reply to this email</h3><p>{role==="ASSISTANT"?"Ask Jiraya for a suggestion or write your own. Review the final text before sending.":"Write your reply here and send it from your connected Gmail account."}</p>
                {role==="ASSISTANT" && <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailDraft(selected.id),"AI draft ready for review.",selected.id)}>Suggest a draft</button>}
                <textarea aria-label="Reply draft" value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Write a reply to this conversation…" rows={6}/>
                <div className="email-compose-actions"><button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailUpdate(selected.id,{draft_body:draft}),"Draft saved.",selected.id)}>Save draft</button>
                  <button className="email-primary" disabled={busy||!draft.trim()||selected.status!=="OPEN"} onClick={()=>{if(window.confirm(selected.source==="DEMO"?"Simulate this reply? No email will be sent.":"Send this reply from the client's connected Gmail account?")) void action(()=>api.emailSend(selected.id,draft),selected.source==="DEMO"?"Sample reply simulated; no email sent.":"Reply sent through Gmail.",selected.id);}}>{selected.source==="DEMO"?"Simulate reply":"Send reply"}</button>
                </div>
              </div>
              {role==="ASSISTANT" ? <div className="email-decide">
                <button className="email-outline" disabled={busy||selected.status==="SENDING"||selected.status==="SEND_UNCERTAIN"} onClick={()=>action(()=>api.emailUpdate(selected.id,{responsibility:"CLIENT_TO_REPLY",status:"OPEN"}),"Sent to the client's attention.",selected.id)}>Send to client</button>
                <button className="email-outline" disabled={busy||selected.status==="HANDLED"||selected.status==="SENDING"||selected.status==="SEND_UNCERTAIN"} onClick={()=>{if(window.confirm("Mark done without sending an email? Use this only if the task was completed outside Jiraya."))void action(()=>api.emailUpdate(selected.id,{status:"HANDLED"}),"Marked done.",selected.id);}}>Mark done</button>
                <button className="email-outline" disabled={busy||selected.status==="HANDLED"||selected.status==="SENDING"||selected.status==="SEND_UNCERTAIN"} onClick={()=>action(()=>api.emailUpdate(selected.id,{status:selected.status==="ON_HOLD"?"OPEN":"ON_HOLD"}),selected.status==="ON_HOLD"?"Resumed.":"On hold.",selected.id)}>{selected.status==="ON_HOLD"?"Resume":"Keep on hold"}</button>
                {selected.status==="HANDLED" && <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailUpdate(selected.id,{status:"OPEN"}),"Reopened.",selected.id)}>Reopen</button>}
              </div> : <div className="email-decide"><button className="email-outline" disabled={busy||selected.status==="HANDLED"} onClick={()=>{if(window.confirm("Confirm that you already handled this outside Jiraya? No email will be sent by this action."))void action(()=>api.emailUpdate(selected.id,{status:"HANDLED"}),"Marked handled outside Jiraya.",selected.id);}}>Action taken outside Jiraya</button>
              <button className="email-outline" disabled={busy} onClick={()=>action(()=>api.emailUpdate(selected.id,{responsibility:"ASSISTANT_TO_REPLY"}),"Delegated to your assistant.",selected.id)}>Ask my personal assistant to take action</button></div>}
          </>}
        </section>
      </div>
    </>}
  </main>;
}
