"use client";
import { useEffect, useState } from "react";
import { api, DeveloperBriefingResult, GithubRepository } from "@/lib/api";
import DeveloperBriefingDemo from "./DeveloperBriefingDemo";

export default function DeveloperBriefing({repositories}: {repositories: GithubRepository[]}) {
  const [demo, setDemo] = useState(false);
  const [configuration, setConfiguration] = useState<{configured: boolean; message: string} | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<DeveloperBriefingResult | null>(null);
  async function check() {
    try {setConfiguration(await api.developerBriefingStatus());setError("");}
    catch (e: any) {setConfiguration(null);setError(e.message);}
  }
  useEffect(() => {check();}, []);
  const names = repositories.map(repository => repository.full_name);
  const selection = selected.filter(name => names.includes(name));
  function toggle(name: string) {
    setSelected(current => current.includes(name) ? current.filter(value => value !== name) : [...current.filter(value => names.includes(value)), name]);
    setResult(null);setError("");
  }
  async function generate() {
    if (busy || !selection.length) return;
    setBusy(true);setError("");setResult(null);
    try {setResult(await api.createDeveloperBriefing(selection));}
    catch (e: any) {setError(e.message || "The briefing could not be generated.");}
    finally {setBusy(false);}
  }
  return <section className="developer-briefing" aria-label="Developer briefing">
    <div className="section-header"><div><span className="eyebrow">Hermes · Read-only pilot</span><h2>Across your projects</h2><p>Turn recent PRs, issues and workflow runs into a briefing with suggested next steps.</p></div><div className="briefing-mode-actions"><button className="secondary-button" disabled={busy} onClick={() => setDemo(value => !value)}>{demo ? "Live briefing" : "Try demo without credentials"}</button>{!demo && <button className="ghost-button" disabled={busy} onClick={check}>Check configuration</button>}</div></div>
    {demo ? <DeveloperBriefingDemo repositories={repositories.map(repository => repository.full_name)}/> : <div className="briefing-body">
      <p className="checkpoint-note" role="status">{configuration?.message || "Checking Hermes configuration…"}</p>
      <label className="briefing-search">Choose up to 3 repositories<input aria-label="Filter briefing repositories" placeholder="Find a repository…" value={query} disabled={busy} onChange={e => setQuery(e.target.value)}/></label>
      <div className="briefing-repositories">{repositories.filter(repository => repository.full_name.toLowerCase().includes(query.toLowerCase())).map(repository => <label key={repository.id}><input type="checkbox" checked={selection.includes(repository.full_name)} disabled={busy || (!selection.includes(repository.full_name) && selection.length >= 3)} onChange={() => toggle(repository.full_name)}/><span>{repository.full_name}</span></label>)}</div>
      {!repositories.length && <p>No accessible repositories are loaded.</p>}
      <p className="checkpoint-note">{selection.length} selected. Generating sends these repositories’ recent titles, states, timestamps and source links to the configured Hermes service. No code, issue bodies, comments or GitHub credentials are sent. Suggestions do not execute actions.</p>
      <button className="primary-button" disabled={busy || !configuration?.configured || !selection.length} onClick={generate}>{busy ? "Generating briefing…" : "Generate developer briefing"}</button>
      {busy && <p className="checkpoint-note" role="status">Reading selected repositories and waiting for Hermes. This can take a few minutes. Closing the page may not cancel remote processing.</p>}
      {error && <div className="service-alert" role="alert">{error}</div>}
      {result && <div className="briefing-result" role="status"><h3>{result.provider === "hermes" ? "Hermes briefing" : "Activity coverage"}</h3><p className="checkpoint-note">Snapshot of {result.repositories.join(", ")} · {new Date(result.generated_at).toLocaleString()}</p><pre>{result.summary}</pre>
        <details><summary>Sources and coverage</summary><p>{result.scope}</p><ul>{result.coverage.map((source,index) => <li key={index}>{source.repository} · {source.kind}: {source.error || `${source.loaded} loaded (up to ${source.limit})`}</li>)}</ul><ol>{result.sources.map(source => <li key={source.id}><strong>[{source.id}]</strong> <a className="service-link" href={source.url} target="_blank" rel="noreferrer">{source.repository} · {source.title}</a></li>)}</ol></details>
        <p className="checkpoint-note">AI-generated analysis can be wrong. Verify recommendations using the linked sources. No changes were made by this briefing.</p>
      </div>}
    </div>}
  </section>;
}
