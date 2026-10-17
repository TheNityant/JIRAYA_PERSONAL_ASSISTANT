"use client";

import { useEffect, useState } from "react";
import { api, GithubItem, GithubRepository, ServiceStatus } from "@/lib/api";

export default function GitHubWorkspace() {
  const [service, setService] = useState<ServiceStatus | null>(null);
  const [repos, setRepos] = useState<GithubRepository[]>([]);
  const [repository, setRepository] = useState("");
  const [items, setItems] = useState<{issues: GithubItem[]; pulls: GithubItem[]; runs: GithubItem[]}>({issues: [], pulls: [], runs: []});
  const [tab, setTab] = useState<"issues" | "pulls" | "runs">("issues");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [branch, setBranch] = useState("");
  const [ref, setRef] = useState("main");
  const [workflow, setWorkflow] = useState("");
  const [inputs, setInputs] = useState("{}");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const state = await api.githubStatus();
        if (cancelled) return;
        setService(state);
        if (state.connected) {
          const response = await api.listGithubRepos(100);
          if (cancelled) return;
          setRepos(response.items);
          setRepository((current) => current || response.items[0]?.full_name || "");
        }
      } catch (e: any) { if (!cancelled) setError(e.message); }
    }
    load();
    const timer = window.setInterval(load, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [revision]);

  useEffect(() => {
    if (!repository) return;
    let cancelled = false;
    const [owner, repo] = repository.split("/");
    setItems({issues: [], pulls: [], runs: []});
    setError("");
    Promise.allSettled([api.listGithubIssues(owner, repo), api.listGithubPulls(owner, repo), api.listGithubWorkflowRuns(owner, repo)])
      .then((results) => {
        if (cancelled) return;
        const rows = results.map((r) => r.status === "fulfilled" ? r.value.items : []);
        setItems({issues: rows[0].filter((i) => !i.pull_request), pulls: rows[1], runs: rows[2]});
        const failures = results.filter((r) => r.status === "rejected") as PromiseRejectedResult[];
        if (failures.length) setError(failures.map((r) => String(r.reason.message)).join("; "));
      });
    return () => { cancelled = true; };
  }, [repository, revision]);

  useEffect(() => {
    setRef(repos.find((r) => r.full_name === repository)?.default_branch || "main");
  }, [repository]);

  function chooseRepository(value: string) {
    setRepository(value);
    setRef(repos.find((r) => r.full_name === value)?.default_branch || "main");
    setFeedback("");
  }

  async function action(kind: "issue" | "branch" | "workflow") {
    if (busy || !repository) return;
    setBusy(true); setError(""); setFeedback("");
    const [owner, repo] = repository.split("/");
    try {
      if (kind === "issue") {
        const result = await api.createGithubIssue(owner, repo, {title, body});
        setFeedback(`Issue #${result.number} created in ${repository}.`); setTitle(""); setBody("");
      } else if (kind === "branch") {
        await api.createGithubBranch(owner, repo, {new_branch: branch, from_ref: ref});
        setFeedback(`Branch ${branch} created from ${ref}.`); setBranch("");
      } else {
        const parsed = JSON.parse(inputs);
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || Object.values(parsed).some((v) => typeof v !== "string")) throw new Error("Workflow inputs must be a JSON object containing string values.");
        await api.dispatchGithubWorkflow(owner, repo, {workflow_id: workflow, ref, inputs: parsed});
        setFeedback("Workflow dispatch requested. Refresh workflow runs to observe its status; the request does not prove the run succeeded.");
      }
      setRevision((v) => v + 1);
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  }

  return <>
    <header className="operations-header"><div><span className="eyebrow">GitHub</span><h1>Your repositories, connected</h1><p>{service?.connected ? `Connected as ${service.account}` : "Connect GitHub to inspect repositories and automate follow-up work."}</p></div><button disabled={busy} onClick={() => setRevision((v) => v + 1)}>Refresh</button></header>
    {error && <p role="alert">{error}</p>}
    {feedback && <p role="status">{feedback}</p>}
    {!service?.connected ? <button className="primary-button" onClick={() => {window.location.href = api.githubConnectUrl();}}>Connect GitHub</button> : <>
      <p>{service.background_monitor_configured ? `Repository monitoring · ${service.poll_seconds}s` : "Background monitoring disabled"} · {service.monitor?.last_decision?.toLowerCase().replaceAll("_", " ") || "Waiting for first cycle"}{service.monitor?.last_error && ` · ${service.monitor.last_error}`}</p>
      <label>Repository<select value={repository} onChange={(e) => chooseRepository(e.target.value)}><option value="">Select a repository</option>{repos.map((repo) => <option key={repo.id} value={repo.full_name}>{repo.full_name}{repo.private ? " · private" : ""}</option>)}</select></label>
      <div className="filter-tabs">{(["issues", "pulls", "runs"] as const).map((key) => <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{key === "pulls" ? "Pull requests" : key === "runs" ? "Workflow runs" : "Issues"}</button>)}</div>
      <div className="operations-list">{items[tab].map((item) => <article className="obligation-card" key={item.id}><strong>{item.title || item.name || "Workflow run"}</strong><p>{item.conclusion || item.status || item.state}{item.number ? ` · #${item.number}` : ""}</p>{item.html_url && <a href={item.html_url} target="_blank" rel="noreferrer">Open on GitHub</a>}</article>)}{repository && !items[tab].length && <p>No {tab === "pulls" ? "pull requests" : tab === "runs" ? "workflow runs" : "issues"} to show.</p>}</div>
      {repository && <div className="obligation-grid">
        <section className="obligation-card"><h2>Create issue</h2><label>Issue title<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256}/></label><label>Issue description<textarea value={body} onChange={(e) => setBody(e.target.value)}/></label><button className="primary-button" disabled={busy || !title.trim()} onClick={() => action("issue")}>Create issue</button></section>
        <section className="obligation-card"><h2>Create branch</h2><label>Source branch / workflow ref<input value={ref} onChange={(e) => setRef(e.target.value)}/></label><label>New branch<input value={branch} onChange={(e) => setBranch(e.target.value)}/></label><button className="primary-button" disabled={busy || !branch.trim() || !ref.trim()} onClick={() => action("branch")}>Create branch</button></section>
        <section className="obligation-card"><h2>Run workflow</h2><p>The workflow must support workflow_dispatch. Uses the ref entered above.</p><label>Workflow filename or ID<input placeholder="build.yml" value={workflow} onChange={(e) => setWorkflow(e.target.value)}/></label><label>Workflow inputs (JSON)<textarea value={inputs} onChange={(e) => setInputs(e.target.value)}/></label><button className="primary-button" disabled={busy || !workflow.trim() || !ref.trim()} onClick={() => action("workflow")}>Request workflow run</button></section>
      </div>}
      <p>Repository change automations monitor the repository you select in their configuration. Existing activity is baselined before notifications begin.</p>
    </>}
  </>;
}
