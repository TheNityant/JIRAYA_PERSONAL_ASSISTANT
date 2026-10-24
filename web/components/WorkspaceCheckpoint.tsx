"use client";
import { type FormEvent, useEffect, useState } from "react";

type Registration = {session_count: number; items: Array<{age_seconds: number; project_count: number; folders: Array<{path: string; root?: string; error?: string}>}>};

type Project = {id: string; path: string; repository?: string; branch?: string; target_branch?: string; files?: string[]; enabled: boolean; unsaved?: boolean; error?: string};
type Result = {id: string; repository: string; branch: string; status: string; commit?: string; files?: number; detail?: string; remaining_files?: number; remaining_commits?: number};
type Job = {id: string; status: string; message?: string; results: Result[]};

export default function WorkspaceCheckpoint() {
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [port, setPort] = useState("8765");
  const [paired, setPaired] = useState(false);
  const [online, setOnline] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [registration, setRegistration] = useState<Registration | null>(null);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [job, setJob] = useState<Job | null>(null);
  const [connectionError, setConnectionError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [message, setMessage] = useState("Jiraya workspace checkpoint");
  const running = job?.status === "RUNNING";

  async function call<T>(path: string, method = "GET", body?: unknown, credentials = {token, port}): Promise<T> {
    const pairingToken = credentials.token.trim();
    if (!pairingToken) throw Error("Enter the pairing token printed by the local companion.");
    const parsed = Number(credentials.port);
    if (!Number.isInteger(parsed) || parsed < 1024 || parsed > 65535) throw Error("Enter a local port from 1024 to 65535.");
    const response = await fetch(`http://127.0.0.1:${parsed}${path}`, {method, cache: "no-store", signal: AbortSignal.timeout(10000), headers: {Authorization: `Bearer ${pairingToken}`, "Content-Type": "application/json"}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const result = await response.json();
    if (response.status === 401) throw Error("The companion is reachable but rejected the submitted token. Check the visible token against the running companion terminal.");
    if (!response.ok) throw Error(typeof result.detail === "string" ? result.detail : "The companion rejected this request. Check the project settings.");
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
        const [rows, latest] = await Promise.all([call<{items: Project[]; registration?: Registration}>("/projects"), call<Job | null>("/checkpoints/latest")]);
        if (!cancelled) {setOnline(true);setProjects(rows.items);setRegistration(rows.registration || null);setJob(latest);setConnectionError("");}
      } catch (e: any) {if (!cancelled) {setOnline(false);setConnectionError(e.message || "Start the local companion to connect.");}}
    }
    refresh(); const timer = window.setInterval(refresh, 5000);
    return () => {cancelled = true;window.clearInterval(timer);};
  }, [paired, token, port, revision]);

  async function pair(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    // Read the submitted controls: password-manager autofill can bypass onChange.
    // Pass these credentials explicitly; setState does not update this closure.
    const form = new FormData(event.currentTarget);
    const credentials = {token: String(form.get("companionToken") || "").trim(), port: String(form.get("companionPort") || "").trim()};
    setToken(credentials.token);setPort(credentials.port);
    setBusy(true);setError("");
    try {
      const health = await call<{service: string}>("/health", "GET", undefined, credentials);
      if (health.service !== "jiraya-local-companion") throw Error("This is not a Jiraya companion.");
      localStorage.setItem("jiraya.companion", JSON.stringify(credentials));setConnectionError("");setShowToken(false);setPaired(true);setOnline(true);setRevision((v) => v+1);
    } catch (e: any) {setError(e.message || "Could not connect to the local companion.");}
    finally {setBusy(false);}
  }
  async function configure(project: Project, enabled: boolean) {
    setBusy(true);setError("");
    try {
      await call(`/projects/${project.id}`, "PUT", {enabled, branch: targets[project.id] || project.target_branch || project.branch});
      setProjects((current) => current.map((row) => row.id === project.id ? {...row, enabled, target_branch: targets[project.id] || project.target_branch || project.branch} : row));
    } catch (e: any) {setError(e.message);}
    finally {setBusy(false);}
  }
  async function checkpoint() {
    setBusy(true);setError("");
    try {
      const started = await call<{id: string; status: string}>("/checkpoints", "POST", {message});
      setJob({...started, results: []});setRevision((v) => v+1);
    } catch (e: any) {setError(e.message);}
    finally {setBusy(false);}
  }
  return <section className="checkpoint-panel" aria-label="Local workspace checkpoint">
    <div className="checkpoint-header"><div><span className="eyebrow">Developer workspace · this computer</span><h2>Finish the day. Push your open projects.</h2><p>Commit saved changes and push enabled repositories to their configured branches using local Git.</p></div><span className={`state-badge ${online ? "open" : "warning"}`}>{online ? "Companion connected" : "Companion offline"}</span></div>
    {connectionError && paired && <div className="service-alert" role="alert">{connectionError}. Check that the companion is running and browser local-network access is allowed.</div>}
    {error && <div className="service-alert" role="alert">{error}</div>}
    {!paired ? <div className="checkpoint-setup"><div><strong>Connect your local companion once</strong><p>Run <code>python -m app.local_companion serve</code> from your Jiraya installation, pair the VS Code extension, then paste the printed token here. Your local Git credentials stay on this computer.</p><a className="service-link" href="https://github.com/TheNityant/JIRAYA_PERSONAL_ASSISTANT/blob/phase2e-generic-execution/docs/WORKSPACE_CHECKPOINT.md" target="_blank" rel="noreferrer">Setup instructions ↗</a></div><form onSubmit={pair}><label>Pairing token<input name="companionToken" type={showToken ? "text" : "password"} required autoComplete="off" autoCapitalize="none" spellCheck={false} value={token} onChange={(e) => setToken(e.target.value)}/></label><label>Local port<input name="companionPort" inputMode="numeric" required value={port} onChange={(e) => setPort(e.target.value)}/></label><label className="checkpoint-token-visibility"><input type="checkbox" checked={showToken} onChange={(e) => setShowToken(e.target.checked)}/>Show pairing token</label><button className="secondary-button" disabled={busy}>Connect companion</button></form></div> : <>
      <div className="checkpoint-toolbar"><span>{projects.length} open Git projects · {projects.filter((p) => p.enabled).length} enabled</span><div><button className="ghost-button" disabled={busy} onClick={() => setRevision((v) => v+1)}>Refresh projects</button><button className="ghost-button" disabled={busy || running} onClick={() => {localStorage.removeItem("jiraya.companion");setPaired(false);setOnline(false);setProjects([]);setRegistration(null);setJob(null);}}>Forget pairing</button></div></div>
      {registration ? <div className="checkpoint-registration" aria-label="Workspace registration">
        <p className="checkpoint-note">{registration.session_count} registered editor or terminal sessions · registration expires after 45 seconds without a heartbeat.</p>
        {!registration.session_count && <p className="checkpoint-note">Browser pairing is complete. No editor has registered yet. In VS Code, open your repository folder and run <strong>Jiraya: Show Workspace Diagnostics</strong>. Other IDEs can use the companion’s <code>watch</code> command.</p>}
        {!!registration.session_count && !projects.length && <p className="checkpoint-note">Your editor is reporting, but no Git repositories were found. Open the repository folder itself; a parent folder containing several projects is not scanned recursively.</p>}
        {registration.items.some((item) => item.folders.length > 0) && <details><summary>Registration details</summary><ul>{registration.items.flatMap((item, windowIndex) => item.folders.map((folder, index) => <li key={`${windowIndex}-${index}`}><strong>{folder.path}</strong><span> — {folder.error || `Git root: ${folder.root}`}</span></li>))}</ul></details>}
      </div> : <p className="checkpoint-note">Restart the updated local companion to see registration diagnostics. Open a Git project in a paired VS Code window, or register it with the companion’s watch command.</p>}
      <div className="checkpoint-projects">{projects.map((project) => <article className="checkpoint-project" key={project.id}><div><strong>{project.repository || project.path}</strong><p title={project.path}>{project.path}</p><small>{project.error || `${project.branch} · ${project.files?.length || 0} changed saved files${project.unsaved ? " · Unsaved editor files" : ""}`}</small>{!!project.files?.length && <details><summary>Review changed filenames</summary><ul>{project.files.slice(0,30).map((file) => <li key={file}>{file}</li>)}</ul>{project.files.length > 30 && <small>And {project.files.length-30} more</small>}</details>}</div><div className="checkpoint-policy"><label>Target branch<input aria-label={`Target branch for ${project.repository || project.path}`} value={targets[project.id] ?? project.target_branch ?? project.branch ?? ""} disabled={busy || running || !!project.error || project.enabled} onChange={(e) => setTargets((current) => ({...current, [project.id]: e.target.value}))}/></label><button className={project.enabled ? "secondary-button" : "primary-button"} disabled={busy || running || !online || !!project.error} onClick={() => configure(project, !project.enabled)}>{project.enabled ? "Disable checkpoint" : "Enable checkpoint"}</button></div></article>)}</div>
      <div className="checkpoint-run"><label>Checkpoint commit message<input maxLength={200} value={message} onChange={(e) => setMessage(e.target.value)}/></label><button className="primary-button" onClick={checkpoint} disabled={!online || busy || running || !message.trim() || !projects.some((project) => project.enabled)}>{running ? "Checkpoint running…" : "Checkpoint all enabled open projects"}</button></div>
      <p className="checkpoint-note">Save your files first. Each project must be on its configured branch. Closed projects and reported unsaved buffers are skipped. A failed push keeps the local commit for a later retry.</p>
      {job && <div className="checkpoint-results" role="status"><strong>{running ? "Working through your projects…" : job.message || "Checkpoint results"}</strong>{job.results.map((result) => <div className="checkpoint-result" key={result.id}><div><strong>{result.repository}</strong><span>{result.branch}{result.commit && <> · <a className="service-link" href={`https://github.com/${result.repository}/commit/${result.commit}`} target="_blank" rel="noreferrer">{result.commit.slice(0,8)}</a></>}</span>{result.detail && <p>{result.detail}</p>}{!!result.remaining_commits && <p>{result.remaining_commits} newer local commits remain to be pushed.</p>}{!!result.remaining_files && <p>{result.remaining_files} files changed after the checkpoint snapshot.</p>}</div><span className={`state-badge ${["PUSHED", "UP_TO_DATE"].includes(result.status) ? "done" : "warning"}`}>{result.status.replaceAll("_", " ")}</span></div>)}</div>}
    </>}
  </section>;
}
