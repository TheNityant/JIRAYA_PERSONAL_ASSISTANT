"use client";
import { useEffect, useRef, useState } from "react";
import ServiceIcon from "./ServiceIcon";

type MeetingAction = {kind?: string; trigger_reason?: string; reply_text?: string; detail?: string; at?: string};
type MeetingStatus = {
  active?: boolean;
  owner_name?: string;
  name_variants?: string[];
  general_participation?: boolean;
  transcript_turns?: number;
  spoken_replies?: number;
  voice?: {ready?: boolean; sample_path?: string};
  actions?: MeetingAction[];
};
type TranscriptRow = {speaker: string; text: string};

const EMPTY_STATUS: MeetingStatus = {};

export default function MeetWorkspace() {
  const [token, setToken] = useState("");
  const [port, setPort] = useState("8765");
  const [paired, setPaired] = useState(false);
  const [online, setOnline] = useState(false);
  const [status, setStatus] = useState<MeetingStatus>(EMPTY_STATUS);
  const [transcript, setTranscript] = useState<TranscriptRow[]>([]);
  const [connectionError, setConnectionError] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [ingestText, setIngestText] = useState("");
  const [ingestSpeaker, setIngestSpeaker] = useState("");
  const [instruction, setInstruction] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [nameVariants, setNameVariants] = useState("");
  const [generalParticipation, setGeneralParticipation] = useState(false);
  const [voicePath, setVoicePath] = useState("");
  const [revision, setRevision] = useState(0);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const active = Boolean(status.active);

  async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
    const parsed = Number(port);
    if (!Number.isInteger(parsed) || parsed < 1024 || parsed > 65535) throw Error("Enter a local port from 1024 to 65535.");
    const response = await fetch(`http://127.0.0.1:${parsed}${path}`, {method, cache: "no-store", signal: AbortSignal.timeout(10000), headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === "string" ? result.detail : "The companion rejected this request.");
    return result;
  }

  useEffect(() => {
    const saved = localStorage.getItem("jiraya.companion");
    if (saved) {try {const value = JSON.parse(saved);setToken(value.token);setPort(value.port);setPaired(true);} catch {localStorage.removeItem("jiraya.companion");}}
  }, []);

  useEffect(() => {
    if (!paired) return;
    let cancelled = false;
    async function refresh() {
      try {
        const [current, rows] = await Promise.all([call<MeetingStatus>("/meeting/status"), call<{items: TranscriptRow[]}>("/meeting/transcript")]);
        if (!cancelled) {setOnline(true);setStatus(current || EMPTY_STATUS);setTranscript(rows.items || []);setConnectionError("");}
      } catch (e: any) {if (!cancelled) {setOnline(false);setConnectionError(e.message || "Start the local companion to connect.");}}
    }
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {cancelled = true;window.clearInterval(timer);};
  }, [paired, token, port, revision]);

  useEffect(() => {
    // Prefill the settings form once real values arrive, without fighting the user's edits.
    if (status.owner_name !== undefined && !ownerName) setOwnerName(status.owner_name || "");
    if (status.name_variants !== undefined && !nameVariants) setNameVariants((status.name_variants || []).join(", "));
    if (status.general_participation !== undefined) setGeneralParticipation(Boolean(status.general_participation));
  }, [status.owner_name, status.name_variants, status.general_participation]);

  useEffect(() => {transcriptRef.current?.scrollTo({top: transcriptRef.current.scrollHeight});}, [transcript.length]);

  async function pair() {
    setBusy(true);setError("");
    try {
      const health = await call<{service: string}>("/health");
      if (health.service !== "jiraya-local-companion") throw Error("This is not a Jiraya companion.");
      localStorage.setItem("jiraya.companion", JSON.stringify({token, port}));
      setPaired(true);setOnline(true);
    } catch (e: any) {setError(e.message || "Could not connect to the local companion.");}
    finally {setBusy(false);}
  }

  async function act(path: string, method: string, body?: unknown, done?: string) {
    setBusy(true);setError("");setFeedback("");
    try {
      const result = await call<MeetingStatus | Record<string, unknown>>(path, method, body);
      const action = result as MeetingAction;
      setFeedback(done || action.reply_text || action.detail || "Done.");
    } catch (e: any) {setError(e.message);}
    finally {setBusy(false);}
  }

  return <section className="service-workspace meet-workspace">
    <header className="service-header"><div className="service-title"><span className="service-app-icon github"><ServiceIcon service="meet" size={25}/></span><div><span className="eyebrow">Live meeting assistant · this computer</span><h1>Google Meet</h1><p>{online ? (active ? `Listening for ${status.owner_name || "your name"} in the meeting.` : "Assistant configured but idle. Start it from the companion or a meeting.") : "Your in-meeting assistant: transcript, replies and voice."}</p></div></div><div className="service-header-actions"><span className={`state-badge ${online ? "open" : "warning"}`}>{online ? (active ? "Assistant active" : "Assistant idle") : "Companion offline"}</span></div></header>
    {connectionError && paired && <div className="service-alert" role="alert">{connectionError}. Check that the companion is running and browser local-network access is allowed.</div>}
    {error && <div className="service-alert" role="alert">{error}</div>}
    {feedback && <div className="service-alert success" role="status">{feedback}</div>}
    {!paired ? <div className="checkpoint-panel"><div className="checkpoint-setup"><div><strong>Connect your local companion once</strong><p>Run <code>python -m app.local_companion serve</code> and paste the pairing token shown there. The meeting endpoints live on the same companion as workspace checkpoints, so one pairing covers both.</p></div><form onSubmit={(e) => {e.preventDefault();pair();}}><label>Pairing token<input type="password" required autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}/></label><label>Local port<input inputMode="numeric" required value={port} onChange={(e) => setPort(e.target.value)}/></label><button className="secondary-button" disabled={busy}>Connect companion</button></form></div></div> : <>
      <div className="checkpoint-toolbar"><span>{online ? (active ? "Assistant active" : "Assistant configured, idle") : "Companion offline"}</span><div><button className="ghost-button" disabled={busy} onClick={() => setRevision((v) => v + 1)}>Retry now</button><button className="ghost-button" disabled={busy} onClick={() => {localStorage.removeItem("jiraya.companion");setPaired(false);setOnline(false);setStatus(EMPTY_STATUS);setTranscript([]);setError("");setFeedback("");setToken("");setPort("8765");}}>Forget pairing</button></div></div>
      <div className="service-stats">
        <div><span>Assistant</span><strong className="stat-text">{active ? "Active" : "Idle"}</strong><small>{status.transcript_turns ?? 0} transcript turns heard</small></div>
        <div><span>Replies spoken</span><strong>{status.spoken_replies ?? 0}<small>this meeting</small></strong></div>
        <div><span>Voice</span><strong className="stat-text">{status.voice?.ready ? "Ready" : "Not configured"}</strong><small>{status.voice?.ready ? "Owner voice sample loaded" : "Upload a sample to speak in your voice"}</small></div>
        <div><span>Owner</span><strong className="stat-text">{status.owner_name || "Not set"}</strong><small>Reacts when your name is called</small></div>
      </div>

      <div className="github-columns">
        <section className="activity-panel">
          <div className="section-header"><div><span className="eyebrow">Live transcript</span><h2>What is being said</h2></div><span className="small-label">{transcript.length} turns</span></div>
          <div className="activity-list meet-transcript" ref={transcriptRef}>
            {transcript.map((row, index) => <article className="activity-row" key={index}><span className={`activity-state-icon ${row.speaker.toLowerCase() === (status.owner_name || "").toLowerCase() ? "done" : "open"}`}><ServiceIcon service="meet" size={15}/></span><div className="activity-content"><h3>{row.speaker}</h3><p>{row.text}</p></div></article>)}
            {!transcript.length && <div className="service-empty compact"><ServiceIcon service="meet" size={28}/><h3>No transcript yet</h3><p>Add a turn below, or let the Meet extension stream captions while you are in a call.</p></div>}
          </div>
          <form className="meet-ingest" onSubmit={(e) => {e.preventDefault();if (!ingestText.trim()) return;act("/meeting/ingest", "POST", {text: ingestText, speaker: ingestSpeaker || "unknown"}, "Turn added to the meeting context.");setIngestText("");}}>
            <div className="two-column">
              <label>Speaker name<input maxLength={80} value={ingestSpeaker} onChange={(e) => setIngestSpeaker(e.target.value)} placeholder="unknown"/></label>
              <label>What they said<input required maxLength={500} value={ingestText} onChange={(e) => setIngestText(e.target.value)} placeholder="Type a transcript turn…"/></label>
            </div>
            <button className="secondary-button" disabled={busy || !online}>Add turn</button>
          </form>
        </section>

        <aside className="repo-action-panel">
          <span className="eyebrow">Assistant controls</span>
          <h2>Drive the assistant</h2>
          <p className="action-panel-intro">Ask it to say something, stop it, or tune when it should speak.</p>
          <form onSubmit={(e) => {e.preventDefault();if (!instruction.trim()) return;act("/meeting/say", "POST", {instruction}, instruction);setInstruction("");}}>
            <label>Instruction<textarea rows={3} required maxLength={500} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Example: Summarise the deadline Priya just mentioned."/></label>
            <button className="primary-button" disabled={busy || !online || !instruction.trim()}>Send instruction<span aria-hidden="true">↗</span></button>
          </form>
          <button className="danger-link" disabled={busy || !online} onClick={() => act("/meeting/stop", "POST", undefined, "Assistant stopped.")}>Stop assistant</button>
          <div className="section-divider"/>
          <span className="section-title">Assistant settings</span>
          <form onSubmit={(e) => {e.preventDefault();act("/meeting/config", "PUT", {owner_name: ownerName, name_variants: nameVariants.split(",").map((item) => item.trim()).filter(Boolean), general_participation: generalParticipation}, "Assistant settings saved.");}}>
            <label>Your name<input required maxLength={120} value={ownerName} onChange={(e) => setOwnerName(e.target.value)} placeholder="Nityant"/></label>
            <label>Name variants (comma separated)<input maxLength={300} value={nameVariants} onChange={(e) => setNameVariants(e.target.value)} placeholder="Nit, Nitu"/></label>
            <label className="toggle-row"><span><strong>General participation</strong><small>Allow the assistant to speak up without being called by name.</small></span><input type="checkbox" checked={generalParticipation} onChange={(e) => setGeneralParticipation(e.target.checked)}/></label>
            <button className="secondary-button" disabled={busy || !online}>Save settings</button>
          </form>
          <div className="section-divider"/>
          <span className="section-title">Voice sample</span>
          <form onSubmit={(e) => {e.preventDefault();act("/meeting/voice-sample", "POST", {path: voicePath}, "Voice sample registered.");setVoicePath("");}}>
            <label>Sample file path (on this computer)<input required maxLength={500} value={voicePath} onChange={(e) => setVoicePath(e.target.value)} placeholder="C:\\Users\\me\\voice\\sample.wav"/></label>
            <button className="secondary-button" disabled={busy || !online}>Register sample</button>
          </form>
          <div className="action-panel-footer"><ServiceIcon service="meet" size={15}/><span>Audio is generated locally and played by the Meet browser extension. Only transcript text travels through the API.</span></div>
        </aside>
      </div>
    </>}
  </section>;
}
