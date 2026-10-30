"use client";

import { useEffect, useState } from "react";
import { branchNameError } from "@/lib/github-validation";
import WorkspaceCheckpoint from "./WorkspaceCheckpoint";
import DeveloperBriefing from "./DeveloperBriefing";
import ServiceIcon from "./ServiceIcon";
import ServiceMonitor from "./ServiceMonitor";
import { api, GithubItem, GithubRepository, ServiceStatus } from "@/lib/api";

export default function GitHubWorkspace({onCreateAutomation, accountId = "local"}: {accountId?: string; onCreateAutomation: (repository: string, trigger: "GITHUB_ISSUE_CHANGED" | "GITHUB_PR_CHANGED" | "GITHUB_WORKFLOW_CHANGED") => void}) {
  const [service, setService] = useState<ServiceStatus | null>(null);
  const [repos, setRepos] = useState<GithubRepository[]>([]);
  const [repository, setRepository] = useState("");
  const [items, setItems] = useState<{issues: GithubItem[]; pulls: GithubItem[]; runs: GithubItem[]}>({issues: [], pulls: [], runs: []});
  const [tab, setTab] = useState<"issues" | "pulls" | "runs">("issues");
  const [loading, setLoading] = useState(true);
  const [activityLoading, setActivityLoading] = useState(false);
  const [actionTab, setActionTab] = useState<"issue" | "branch" | "workflow">("issue");
  const [search, setSearch] = useState("");
  const [activityErrors, setActivityErrors] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [branchesLoading, setBranchesLoading] = useState(false);
  const [branchesError, setBranchesError] = useState("");
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
      finally {if (!cancelled) setLoading(false);}
    }
    load();
    const timer = window.setInterval(load, 60000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [revision]);

  useEffect(() => {
    if (!repository) return;
    let cancelled = false;
    const [owner, repo] = repository.split("/");
    setActivityLoading(true);
    setItems({issues: [], pulls: [], runs: []});
    setActivityErrors([]);
    Promise.allSettled([api.listGithubIssues(owner, repo), api.listGithubPulls(owner, repo), api.listGithubWorkflowRuns(owner, repo)])
      .then((results) => {
        if (cancelled) return;
        const rows = results.map((r) => r.status === "fulfilled" ? r.value.items : []);
        setActivityLoading(false);
        setItems({issues: rows[0].filter((i) => !i.pull_request), pulls: rows[1], runs: rows[2]});
        setActivityErrors(results.flatMap((result, index) => result.status === "rejected" ? [`${["Issues", "Pull requests", "Workflows"][index]}: ${result.reason.message}`] : []));
      });
    return () => { cancelled = true; };
  }, [repository, revision]);

  useEffect(() => {
    if (!repository || actionTab !== "branch") return;
    let cancelled = false;
    const [owner, repo] = repository.split("/");
    setBranchesLoading(true); setBranchesError(""); setBranches([]);
    api.listGithubBranches(owner, repo).then((result) => {
      if (cancelled) return;
      const names = result.items.map((item) => item.name);
      setBranches(names);
      const defaultBranch = repos.find((item) => item.full_name === repository)?.default_branch;
      setRef((current) => names.includes(current) ? current : defaultBranch && names.includes(defaultBranch) ? defaultBranch : names[0] || "");
    }).catch((e: Error) => {if (!cancelled) setBranchesError(e.message);})
      .finally(() => {if (!cancelled) setBranchesLoading(false);});
    return () => {cancelled = true;};
  }, [repository, actionTab, revision]);

  function chooseRepository(value: string) {
    setRepository(value);
    setRef(repos.find((r) => r.full_name === value)?.default_branch || "main");
    setFeedback("");
  }

  async function action(kind: "issue" | "branch" | "workflow") {
    if (busy || !repository) return;
    if (kind === "branch") {
      const problem = branchNameError(branch);
      if (problem) {setError(`New branch name: ${problem}`); return;}
      if (!branches.includes(ref)) {setError("Select an existing source branch."); return;}
      if (branches.includes(branch)) {setError("That branch already exists. Choose a different new branch name."); return;}
    }
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

  const visible = items[tab].filter((item) => `${item.title || item.name || ""} ${item.number || ""}`.toLowerCase().includes(search.toLowerCase()));
  const selectedRepo = repos.find((item) => item.full_name === repository);
  const time = (value?: string) => value ? new Date(value).toLocaleDateString([], {month: "short", day: "numeric"}) : "";
  return <div className="service-workspace github-workspace">
    <header className="service-header"><div className="service-title"><span className="service-app-icon github"><ServiceIcon service="github" size={25}/></span><div><span className="eyebrow">Your connected workspace</span><h1>GitHub</h1><p>{service?.connected ? `Connected as ${service.account}` : "Your repositories and the work around them."}</p></div></div><div className="service-header-actions"><button className="secondary-button icon-label" disabled={busy || loading} onClick={() => setRevision((v) => v+1)}><ServiceIcon service="refresh" size={15}/>Refresh</button><button className="primary-button" disabled={!service?.connected || !repository} onClick={() => onCreateAutomation(repository, tab === "pulls" ? "GITHUB_PR_CHANGED" : tab === "runs" ? "GITHUB_WORKFLOW_CHANGED" : "GITHUB_ISSUE_CHANGED")}>Create watcher</button></div></header>
    <WorkspaceCheckpoint accountId={accountId}/>
    {error && <div className="service-alert" role="alert">{error}</div>}
    {activityErrors.map((message) => <div key={message} className="service-alert" role="alert">{message}</div>)}
    {feedback && <div className="service-alert success" role="status">{feedback}</div>}
    {loading && <div className="loading-state" role="status">Loading your GitHub workspace…</div>}
    {!loading && !service?.connected ? <div className="service-empty"><ServiceIcon service="github" size={38}/><h2>Your development activity, in one place</h2><p>Connect GitHub to inspect repositories, track changes and automate follow-up work.</p><button className="primary-button" onClick={async () => {try {const result = await api.startConnection("github"); window.location.assign(result.authorization_url);} catch (e: any) {setError(e.message);}}}>Connect GitHub</button></div> : service?.connected && <>
      <DeveloperBriefing repositories={repos}/>
      <div className="service-stats"><div><span>Accessible repositories</span><strong>{repos.length}<small>in this view</small></strong></div><div><span>Selected repository</span><strong className="stat-text">{repository.split("/")[1] || "Choose a repository"}</strong><small>{selectedRepo?.private ? "Private repository" : "Repository activity and actions"}</small></div><ServiceMonitor service={service} label="GitHub"/></div>
      <div className="repo-toolbar"><div className="repo-picker"><ServiceIcon service="github"/><label htmlFor="repository-select">Repository</label><select id="repository-select" value={repository} onChange={(e) => chooseRepository(e.target.value)}><option value="">Choose a repository</option>{repos.map((repo) => <option key={repo.id} value={repo.full_name}>{repo.full_name}{repo.private ? " · private" : ""}</option>)}</select></div>{selectedRepo && <a className="service-link" href={selectedRepo.html_url} target="_blank" rel="noreferrer">View repository ↗</a>}</div>
      {!repos.length && <div className="service-empty compact"><h3>No repositories available</h3><p>Check the connected account's repository permissions, then refresh.</p></div>}
      {repository && <div className="github-columns"><section className="activity-panel"><div className="section-header"><div><span className="eyebrow">Development activity</span><h2>Keep track of the work</h2></div></div><div className="activity-toolbar"><div className="activity-tabs">{(["issues", "pulls", "runs"] as const).map((key) => <button key={key} className={tab === key ? "active" : ""} onClick={() => {setTab(key);setSearch("");}}>{key === "pulls" ? "Pull requests" : key === "runs" ? "Workflows" : "Issues"}<span>{items[key].length}</span></button>)}</div><input aria-label="Filter repository activity" className="activity-search" placeholder="Filter activity…" value={search} onChange={(e) => setSearch(e.target.value)}/></div>
        {activityLoading ? <div className="loading-state" role="status">Loading repository activity…</div> : <div className="activity-list">{visible.map((item) => {
          const state = item.conclusion || item.status || item.state || "updated";
          const tone = ["failure", "cancelled", "timed_out"].includes(state) ? "warning" : ["success", "closed", "completed"].includes(state) ? "done" : "open";
          return <article className="activity-row" key={item.id}><span className={`activity-state-icon ${tone}`}><ServiceIcon service={tab === "runs" ? "check" : "github"} size={17}/></span><div className="activity-content"><h3>{item.html_url ? <a href={item.html_url} target="_blank" rel="noreferrer">{item.title || item.name || "Workflow run"}</a> : item.title || item.name || "Workflow run"}</h3><p>{item.number ? `#${item.number} · ` : ""}{item.user?.login || item.head_branch || repository}{item.updated_at && ` · Updated ${time(item.updated_at)}`}</p></div><span className={`state-badge ${tone}`}>{state.replaceAll("_", " ")}</span></article>;
        })}{!visible.length && <div className="service-empty compact"><ServiceIcon service={tab === "runs" ? "check" : "github"} size={28}/><h3>{search ? "No matching activity" : `No ${tab === "pulls" ? "pull requests" : tab === "runs" ? "workflow runs" : "issues"} to show`}</h3><p>{search ? "Try another filter." : "Activity will appear here when this repository has updates."}</p></div>}</div>}
      </section><aside className="repo-action-panel"><span className="eyebrow">Repository actions</span><h2>Move the work forward</h2><p className="action-panel-intro">Make an explicit change in the selected repository.</p><div className="action-type-tabs">{(["issue", "branch", "workflow"] as const).map((kind) => <button key={kind} disabled={busy} className={actionTab === kind ? "active" : ""} onClick={() => setActionTab(kind)}>{kind === "issue" ? "Issue" : kind === "branch" ? "Branch" : "Workflow"}</button>)}</div>
        <form onSubmit={(e) => {e.preventDefault();action(actionTab);}}>
          {actionTab === "issue" ? <><label>Issue title<input required value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} placeholder="What needs attention?"/></label><label>Description<textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Add the context and expected outcome…"/></label></> : <>
            {actionTab === "branch" ? <label>Source branch<select aria-label="Source branch" required value={ref} disabled={branchesLoading || !branches.length} onChange={(e) => setRef(e.target.value)}><option value="">{branchesLoading ? "Loading branches…" : "Choose an existing branch"}</option>{branches.map((name) => <option key={name} value={name}>{name}</option>)}</select>{branchesError && <span role="alert" className="form-note">{branchesError}</span>}{!branchesLoading && !branchesError && !branches.length && <span className="form-note">This repository has no branches. Create its first commit on GitHub.</span>}</label> : <label>Workflow ref<input required value={ref} onChange={(e) => setRef(e.target.value)}/></label>}
            {actionTab === "branch" ? <label>New branch name<input aria-label="New branch name" required maxLength={240} value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="feature/follow-up"/><span className="form-note">Use a Git branch name without spaces, such as feature/follow-up.</span></label> : <><label>Workflow filename or ID<input required placeholder="build.yml" value={workflow} onChange={(e) => setWorkflow(e.target.value)}/></label><label>Inputs (JSON)<textarea rows={4} value={inputs} onChange={(e) => setInputs(e.target.value)}/></label><p className="form-note">The workflow must support manual dispatch. A request is not a confirmed run result.</p></>}
          </>}
          <button className="primary-button" disabled={busy || (actionTab === "branch" && (branchesLoading || !branches.includes(ref)))}>{busy ? "Working…" : actionTab === "issue" ? "Create issue" : actionTab === "branch" ? "Create branch" : "Request workflow run"}<span aria-hidden="true">↗</span></button>
        </form><div className="action-panel-footer"><ServiceIcon service="check" size={15}/><span>Uses your connected GitHub account and the app's external-action policy.</span></div>
      </aside></div>}
      <p className="workspace-note">Create a watcher to turn repository changes into notifications. The first poll establishes a baseline.</p>
    </>}
  </div>;
}
