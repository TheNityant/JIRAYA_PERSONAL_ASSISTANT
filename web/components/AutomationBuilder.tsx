"use client";

import GitHubWorkspace from "@/components/GitHubWorkspace";
import { branchNameError, repositoryError } from "@/lib/github-validation";
import CalendarWorkspace from "@/components/CalendarWorkspace";
import AutomationCanvas from "@/components/AutomationCanvas";
import { actionLabels, actionsFor, isConnected, routeReady, services } from "@/lib/automation-graph";

import { useEffect, useState } from "react";
import {
  api,
  Automation,
  AutomationInput,
  AutomationRunResponse,
  ConnectionCatalogItem,
  GmailRuntimeStatus,
  JirayaNotification,
  Obligation,
} from "@/lib/api";

const emptyAutomation: AutomationInput = {
  name: "New automation",
  description: "",
  enabled: true,
  source: "gmail",
  trigger_type: "NEW_EMAIL",
  match_config: {
    sender_contains: "",
    subject_contains: "",
    body_contains: "",
    semantic_condition: "",
    semantic_threshold: 0.75,
  },
  context_config: { instructions: "" },
  action_config: { type: "DRAFT_REPLY", instructions: "" },
  approval_policy: "REQUIRED",
};

type View = "AUTOMATIONS" | "NOTIFICATIONS" | "OBLIGATIONS" | "CALENDAR" | "GITHUB";

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function nice(value: string): string {
  return value.toLowerCase().replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

function when(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function toInput(item: Automation): AutomationInput {
  return {
    name: item.name,
    description: item.description,
    enabled: item.enabled,
    source: item.source,
    trigger_type: item.trigger_type,
    match_config: { ...item.match_config },
    context_config: { ...item.context_config },
    action_config: { ...item.action_config },
    approval_policy: item.approval_policy,
  };
}

function notificationLabel(category: string): string {
  switch (category) {
    case "ACTION_REQUIRED": return "Needs you";
    case "AUTOMATION_EXECUTED": return "Done by Jiraya";
    case "INFORMATION": return "FYI";
    case "STATE_CHANGED": return "State changed";
    default: return nice(category);
  }
}

function notificationIcon(category: string): string {
  switch (category) {
    case "ACTION_REQUIRED": return "!";
    case "AUTOMATION_EXECUTED": return "✓";
    case "INFORMATION": return "i";
    case "STATE_CHANGED": return "↻";
    default: return "•";
  }
}

export default function AutomationBuilder() {
  const [activeView, setActiveView] = useState<View>("AUTOMATIONS");
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [selected, setSelected] = useState<Automation | null>(null);
  const [draft, setDraft] = useState<AutomationInput>(emptyAutomation);
  const [status, setStatus] = useState("Loading workspace...");
  const [configurationError, setConfigurationError] = useState("");
  const [syncNotice, setSyncNotice] = useState<{error: boolean; message: string} | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [runResult, setRunResult] = useState<AutomationRunResponse | null>(null);
  const [executionDraft, setExecutionDraft] = useState("");
  const [connections, setConnections] = useState<ConnectionCatalogItem[]>([]);
  const [notifications, setNotifications] = useState<JirayaNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [notificationFilter, setNotificationFilter] = useState("ALL");
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [obligationFilter, setObligationFilter] = useState<"OPEN" | "RESOLVED">("OPEN");
  const [runtime, setRuntime] = useState<GmailRuntimeStatus | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const canSave = routeReady(draft, connections);
  const allowedActions = actionsFor(draft.source, connections);

  async function refresh(preferredId?: string) {
    const list = await api.listAutomations();
    setAutomations(list);
    const next = list.find((item) => item.id === preferredId) ?? list[0] ?? null;
    selectAutomation(next);
    setStatus(list.length ? "Ready" : "Create your first automation");
  }

  async function refreshConnections() {
    const response = await api.listConnections();
    setConnections(response.catalog);
  }

  async function refreshRuntime() {
    const runtimeResponse = await api.gmailRuntimeStatus();
    setRuntime(runtimeResponse);
  }

  async function refreshOperations() {
    const [notificationResponse, obligationResponse, runtimeResponse] = await Promise.all([
      api.listNotifications(),
      api.listObligations(),
      api.gmailRuntimeStatus(),
    ]);
    setNotifications(notificationResponse.items);
    setUnreadCount(notificationResponse.unread_count);
    setObligations(obligationResponse);
    setRuntime(runtimeResponse);
  }

  useEffect(() => {
    const loaded = Promise.all([refresh(), refreshConnections(), refreshOperations()]);
    loaded.catch((error) => setStatus(`Backend unavailable: ${error.message}`));
    const query = new URLSearchParams(window.location.search);
    const connectionStatus = query.get("status");
    const provider = query.get("connection");
    if (provider && connectionStatus) {
      loaded.then(() => setStatus(
        connectionStatus === "connected"
          ? `${provider} connected successfully`
          : `${provider} connection failed: ${query.get("detail") ?? "unknown error"}`,
      )).catch(() => undefined);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    const operations = activeView === "NOTIFICATIONS" || activeView === "OBLIGATIONS";
    const timer = window.setInterval(() => {
      if (operations) refreshOperations().catch(() => undefined);
      else Promise.all([refreshRuntime(), refreshConnections()]).catch(() => undefined);
    }, operations ? 5000 : 15000);
    if (operations) refreshOperations().catch(() => undefined);
    return () => window.clearInterval(timer);
  }, [activeView]);

  useEffect(() => {
    if (!selected && !isDirty && !isConnected(connections, draft.source)) {
      const first = connections.find((item) => item.status === "CONNECTED");
      if (first) setDraft((current) => ({...current, source: first.id as AutomationInput["source"],
        trigger_type: first.id === "gmail" ? "NEW_EMAIL" : first.id === "github" ? "GITHUB_ISSUE_CHANGED" : "CALENDAR_CHANGED",
        action_config: {...current.action_config, type: first.id === "gmail" ? "DRAFT_REPLY" : "NOTIFY"}}));
    }
  }, [connections, selected, isDirty, draft.source]);

  function selectAutomation(item: Automation | null) {
    setConfigurationError(""); setSyncNotice(null);
    setSelected(item);
    setPreview(null);
    setRunResult(null);
    setExecutionDraft("");
    setIsDirty(false);
    setLastSavedAt(item?.updated_at ?? null);
    if (!item) {
      setDraft(emptyAutomation);
      return;
    }
    setDraft(toInput(item));
  }

  function updateDraft(mutator: (current: AutomationInput) => AutomationInput) {
    setConfigurationError("");
    setDraft((current) => mutator(current));
    setIsDirty(true);
  }

  function updateMatch(key: string, value: unknown) {
    updateDraft((current) => ({
      ...current,
      match_config: { ...current.match_config, [key]: value },
    }));
  }

  function updateContext(key: string, value: unknown) {
    updateDraft((current) => ({
      ...current,
      context_config: { ...current.context_config, [key]: value },
    }));
  }

  function updateAction(key: string, value: unknown) {
    updateDraft((current) => ({
      ...current,
      action_config: { ...current.action_config, [key]: value },
    }));
  }

  function selectSource(source: AutomationInput["source"]) {
    updateDraft((current) => ({...current, source,
      trigger_type: source === "gmail" ? "NEW_EMAIL" : source === "github" ? "GITHUB_ISSUE_CHANGED" : "CALENDAR_CHANGED",
      action_config: {...current.action_config, type: source === "gmail" ? "DRAFT_REPLY" : "NOTIFY"}}));
  }

  function beginAutomation(requested?: AutomationInput["source"], repository?: string, trigger?: AutomationInput["trigger_type"]) {
    const source = requested || (connections.find((item) => item.status === "CONNECTED")?.id as AutomationInput["source"] | undefined) || "gmail";
    selectAutomation(null);
    setDraft({...emptyAutomation, name: requested ? `${source === "github" ? "GitHub" : "Calendar"} watcher` : "New automation", source,
      trigger_type: trigger || (source === "gmail" ? "NEW_EMAIL" : source === "github" ? "GITHUB_ISSUE_CHANGED" : "CALENDAR_CHANGED"),
      action_config: {type: source === "gmail" ? "DRAFT_REPLY" : "NOTIFY", instructions: "", ...(repository ? {repository} : {})}});
    setActiveView("AUTOMATIONS"); setInspectorOpen(true);
  }

  async function save() {
    if (isSaving || !canSave) return;
    const action = String(draft.action_config.type);
    if (draft.source === "github" || action.startsWith("CREATE_GITHUB_")) {
      const problem = repositoryError(String(draft.action_config.repository || ""));
      if (problem) {setConfigurationError(problem); setInspectorOpen(true); return;}
    }
    if (action === "CREATE_GITHUB_BRANCH") {
      const problem = branchNameError(String(draft.action_config.branch || "")) || branchNameError(String(draft.action_config.from_ref || "main"));
      if (problem) {setConfigurationError(problem); setInspectorOpen(true); return;}
    }
    setIsSaving(true);
    setStatus("Saving...");
    try {
      const saved = selected
        ? await api.updateAutomation(selected.id, draft)
        : await api.createAutomation(draft);
      setAutomations((current) => {
        const exists = current.some((item) => item.id === saved.id);
        const next = exists
          ? current.map((item) => (item.id === saved.id ? saved : item))
          : [saved, ...current];
        return [...next].sort(
          (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
        );
      });
      setSelected(saved);
      setDraft(toInput(saved));
      setIsDirty(false);
      setLastSavedAt(saved.updated_at);
      setPreview(null);
      setRunResult(null);
      setExecutionDraft("");
      setStatus(`Saved · ${saved.approval_policy === "AUTO" ? "Automatic" : nice(saved.approval_policy)}`);
    } catch (error: any) {
      setConfigurationError(error.message); setInspectorOpen(true);
      setStatus(`Save failed: ${error.message}`);
    } finally {
      setIsSaving(false);
    }
  }

  async function createPreset() {
    setStatus("Creating preset...");
    try {
      const item = await api.createInternshipPreset();
      await refresh(item.id);
      setStatus("Internship Assistant created");
    } catch (error: any) {
      setStatus(`Preset failed: ${error.message}`);
    }
  }

  async function testLatestEmail() {
    if (!selected || selected.source !== "gmail") return;
    setStatus("Debug: testing latest Gmail...");
    try {
      const result = await api.previewGmail(selected.id);
      setPreview(result);
      setStatus(result.match?.matches ? "Latest email matched" : "Latest email did not match");
    } catch (error: any) {
      setStatus(`Preview failed: ${error.message}`);
    }
  }

  async function runLatestEmail(force = false) {
    if (!selected || isSyncing) return;
    if (selected.source === "github") {
      setIsSyncing(true); setSyncNotice(null);
      try {
        const result = await api.pollGithub();
        const failures = result.errors || [];
        const message = failures.length ? failures.map((failure) => `${failure.repository || "GitHub watcher"}: ${failure.detail}`).join("; ") : result.baselines_initialized ? "Watcher baseline saved. Existing pull requests, issues and runs are history; subsequent matching changes will create notifications." : result.decision === "NO_AUTOMATIONS" ? "No enabled GitHub watchers. Enable and save a watcher before syncing." : result.decision === "NOT_CONNECTED" ? "Reconnect GitHub to start this watcher." : `Sync complete. ${result.processed} changed items checked.`;
        setSyncNotice({error: failures.length > 0 || result.decision === "NOT_CONNECTED", message});
        setStatus(nice(result.decision)); await refreshOperations();
      } catch (error: any) {setSyncNotice({error: true, message: error.message}); setStatus(`GitHub sync failed: ${error.message}`);}
      finally {setIsSyncing(false);}
      return;
    }
    if (selected.source === "google_calendar") {
      try {
        const result = await api.pollCalendar();
        setStatus(nice(result.decision));
        await refreshOperations();
      } catch (error: any) { setStatus(`Calendar sync failed: ${error.message}`); }
      return;
    }
    setStatus("Debug: running automation on latest Gmail...");
    try {
      const result = await api.runGmailAutomation(selected.id, undefined, force);
      setRunResult(result);
      setExecutionDraft(result.execution?.draft_body ?? "");
      setStatus(`Execution: ${nice(result.decision)}`);
      await refreshOperations();
    } catch (error: any) {
      setStatus(`Execution failed: ${error.message}`);
    }
  }

  async function approveExecution() {
    if (!runResult?.execution) return;
    setStatus("Executing approved action...");
    try {
      const result = await api.approveExecution(runResult.execution.id, executionDraft);
      setRunResult(result);
      setExecutionDraft(result.execution?.draft_body ?? executionDraft);
      setStatus(result.verification_status ? `Send result: ${result.verification_status}` : nice(result.decision));
      await refreshOperations();
    } catch (error: any) {
      setStatus(`Approval failed: ${error.message}`);
    }
  }

  async function removeSelected() {
    if (!selected) return;
    if (!window.confirm(`Delete automation “${selected.name}”?`)) return;
    await api.deleteAutomation(selected.id);
    await refresh();
  }

  async function connect(item: ConnectionCatalogItem) {
    setStatus(`Opening ${item.label} authorization...`);
    try {
      const result = await api.startConnection(item.id as "gmail" | "google_calendar" | "github");
      window.location.assign(result.authorization_url);
    } catch (error: any) { setStatus(`Connection unavailable: ${error.message}`); }
  }

  async function disconnect(item: ConnectionCatalogItem) {
    setStatus(`Disconnecting ${item.label}...`);
    try {
      if (item.id === "google_calendar") await api.disconnectCalendar();
      else if (item.id === "gmail") await api.disconnectGoogle();
      else if (item.id === "github") await api.disconnectGithub();
      await refreshConnections();
      await refreshRuntime();
        setStatus(`${item.label} disconnected`);
    } catch (error: any) { setStatus(`Disconnect failed: ${error.message}`); }
  }

  async function markRead(item: JirayaNotification) {
    if (!item.read_at) await api.markNotificationRead(item.id);
    await refreshOperations();
    const executionId = item.metadata.execution_id;
    if (typeof executionId === "string") {
      try {
        const result = await api.getExecution(executionId);
        const automation = automations.find((a) => a.id === result.automation_id);
        if (automation) selectAutomation(automation);
        setRunResult({decision: result.status, execution: result});
        setExecutionDraft(result.draft_body);
        setActiveView("AUTOMATIONS");
      } catch (error: any) { setStatus(`Execution unavailable: ${error.message}`); }
    }
  }

  const execution = runResult?.execution;
  const approvable =
    ["DRAFT_REPLY", "CREATE_CALENDAR_EVENT", "CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"].includes(execution?.action_type ?? "") &&
    execution?.approval_policy === "REQUIRED" &&
    ["AWAITING_APPROVAL", "BLOCKED_BY_POLICY"].includes(execution.status);

  const filteredNotifications = notifications.filter(
    (item) => notificationFilter === "ALL" || item.category === notificationFilter,
  );
  const filteredObligations = obligations.filter((item) =>
    obligationFilter === "OPEN" ? item.status === "OPEN" : item.status !== "OPEN",
  );

  const monitorConfiguredButUnhealthy = Boolean(
    runtime?.background_monitor_configured && !runtime.background_monitor_enabled,
  );

  return (
    <main className={`workspace ${activeView !== "AUTOMATIONS" ? "wide-view" : ""} ${inspectorOpen ? "config-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">J</div>
          <div>
            <strong>Jiraya</strong>
            <span>Personal operations AI</span>
          </div>
        </div>

        <nav className="primary-nav">
          <button className={activeView === "GITHUB" ? "active" : ""} onClick={() => setActiveView("GITHUB")}>GitHub</button>
          <button className={activeView === "CALENDAR" ? "active" : ""} onClick={() => setActiveView("CALENDAR")}>Calendar</button>
          <button className={activeView === "AUTOMATIONS" ? "active" : ""} onClick={() => setActiveView("AUTOMATIONS")}>Automations</button>
          <button className={activeView === "NOTIFICATIONS" ? "active" : ""} onClick={() => setActiveView("NOTIFICATIONS")}>
            Notifications {unreadCount > 0 && <span className="nav-badge">{unreadCount}</span>}
          </button>
          <button className={activeView === "OBLIGATIONS" ? "active" : ""} onClick={() => setActiveView("OBLIGATIONS")}>Obligations</button>
        </nav>

        <div className="sidebar-title-row">
          <span>Automations</span>
          <button className="icon-button" onClick={() => beginAutomation()} title="New automation">+</button>
        </div>

        <div className="automation-list">
          {automations.map((item) => (
            <button
              key={item.id}
              className={`automation-list-item ${selected?.id === item.id && activeView === "AUTOMATIONS" ? "active" : ""}`}
              onClick={() => { selectAutomation(item); setActiveView("AUTOMATIONS"); }}
            >
              <span className={`status-dot ${item.enabled ? "on" : "off"}`} />
              <div>
                <strong>{item.name}</strong>
                <span>{item.source} · {item.approval_policy === "AUTO" ? "automatic" : item.trigger_type.toLowerCase().replaceAll("_", " ")}</span>
              </div>
            </button>
          ))}
          {!automations.length && (
            <button className="preset-card" onClick={createPreset}>
              <strong>Start with Internship Assistant</strong>
              <span>Create an editable Gmail automation.</span>
            </button>
          )}
        </div>

        <div className="sidebar-title-row connections-heading"><span>Connections</span></div>
        <div className="connection-list">
          {connections.map((item) => (
            <div className="connection-card" key={item.id}>
              <div className="connection-copy">
                <strong>{item.label}</strong>
                <span>{item.account ?? item.description}</span>
              </div>
              {item.status === "CONNECTED" ? (
                <button className="connection-action connected" onClick={() => disconnect(item)} title={`Disconnect ${item.label}`}>Connected</button>
              ) : item.status === "AVAILABLE" ? (
                <button className="connection-action" onClick={() => connect(item)}>Connect</button>
              ) : <span className="connection-coming" title="The app owner needs to enable this connection.">Unavailable</span>}
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <span className="small-label">Runtime</span>
          <strong className="runtime-line">
            <span className={`status-dot ${runtime?.background_monitor_enabled ? "on" : "off"}`} />
            {runtime?.background_monitor_enabled
              ? `Gmail monitoring · ${runtime.poll_seconds ?? "—"}s`
              : monitorConfiguredButUnhealthy
                ? "Gmail monitor needs attention"
                : "Gmail monitor paused"}
          </strong>
          {runtime?.mailbox && <span className="runtime-mailbox">{runtime.mailbox}</span>}
          {runtime?.last_poll_status === "ERROR" && <span className="runtime-mailbox">Last poll failed: {runtime.last_error ?? "unknown error"}</span>}
          {runtime?.last_poll_status === "OK" && runtime.last_poll_decision && <span className="runtime-mailbox">Last poll: {nice(runtime.last_poll_decision)}</span>}
          <strong>{status}</strong>
        </div>
      </aside>

      {activeView === "AUTOMATIONS" ? (
        <>
          <section className="canvas-shell">
            <header className="topbar">
              <div>
                <span className="eyebrow">Automation Builder</span>
                <h1>{draft.name}</h1>
                <span className="small-label">
                  {isDirty ? "Unsaved changes" : lastSavedAt ? `Saved ${when(lastSavedAt)}` : "Not saved yet"}
                </span>
              </div>
              <div className="topbar-actions">
                {selected && <button className="secondary-button" onClick={() => beginAutomation()}>New automation</button>}
                {selected?.source === "gmail" && <button className="ghost-button" onClick={testLatestEmail}>Debug · Test match</button>}
                {selected && <button className="ghost-button" disabled={isSyncing} onClick={() => runLatestEmail(false)}>{selected.source === "gmail" ? "Debug · Run latest" : selected.source === "github" ? "Sync GitHub" : "Sync Calendar"}</button>}
                <button className="secondary-button configuration-toggle" onClick={() => setInspectorOpen(!inspectorOpen)} aria-expanded={inspectorOpen}>Configure</button>
                <button className="primary-button" onClick={save} disabled={isSaving || !canSave}>{isSaving ? "Saving..." : selected ? "Save changes" : "Create automation"}</button>
              </div>
            </header>

            {syncNotice && <div className={`sync-notice ${syncNotice.error ? "error" : ""}`} role={syncNotice.error ? "alert" : "status"}>{syncNotice.message}</div>}
            <AutomationCanvas draft={draft} connections={connections} onSource={selectSource} onAction={(action) => updateAction("type", action)}/>

            {preview && (
              <div className={`preview-strip ${preview.match?.matches ? "matched" : "not-matched"}`}>
                <div><span className="small-label">Debug preview</span><strong>{preview.message?.subject ?? "No inbox message"}</strong></div>
                {preview.match && <div className="preview-result"><strong>{preview.match.matches ? "MATCH" : "NO MATCH"}</strong><span>{Math.round(preview.match.confidence * 100)}% · {preview.match.reason}</span></div>}
              </div>
            )}

            {runResult && (
              <div className="execution-panel">
                <div className="execution-header">
                  <div><span className="small-label">Automation execution</span><strong>{nice(runResult.decision)}</strong></div>
                  {runResult.match && <span className="execution-confidence">{Math.round(runResult.match.confidence * 100)}% match</span>}
                </div>
                {runResult.action_route && <div className="route-note"><strong>{nice(runResult.action_route.decision)}</strong><span>{runResult.action_route.reason}</span></div>}
                {execution?.action_type === "DRAFT_REPLY" && executionDraft && (
                  <div className="draft-workspace">
                    <label>Generated reply<textarea rows={8} value={executionDraft} onChange={(event) => setExecutionDraft(event.target.value)} readOnly={execution.status === "VERIFIED_SUCCESS"} /></label>
                    <div className="execution-actions">
                      {execution.approval_policy === "DRAFT_ONLY" && <span className="execution-note">Draft-only policy: Jiraya will not send this message.</span>}
                      {execution.status === "BLOCKED_BY_POLICY" && <span className="execution-note warning">External actions are disabled.</span>}
                      {execution.status === "VERIFIED_SUCCESS" && <span className="execution-note success">Sent and verified by Gmail.</span>}
                      {approvable && <button className="primary-button" onClick={approveExecution}>Approve &amp; Send</button>}
                      {runResult.decision === "EXISTING_EXECUTION" && <button className="secondary-button" onClick={() => runLatestEmail(true)}>Regenerate draft</button>}
                    </div>
                  </div>
                )}
                {execution && ["CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"].includes(execution.action_type) && <div className="draft-workspace">
                  <strong>GitHub proposal · {str(execution.output.repository)}</strong>
                  {execution.output.proposal ? <>
                    <p>{str((execution.output.proposal as Record<string, unknown>).title) || `Create branch ${str((execution.output.proposal as Record<string, unknown>).branch)} from ${str((execution.output.proposal as Record<string, unknown>).from_ref)}`}</p>
                    <p style={{whiteSpace: "pre-wrap"}}>{str((execution.output.proposal as Record<string, unknown>).body)}</p>
                  </> : <p>{str(execution.output.reason)}</p>}
                  {approvable && <button className="primary-button" onClick={approveExecution}>Approve GitHub action</button>}
                  {execution.status === "DRAFT_READY" && <p>Draft only: GitHub will not be changed.</p>}
                  {execution.status === "BLOCKED_BY_POLICY" && <p>External actions are disabled.</p>}
                  {execution.status === "VERIFIED_SUCCESS" && <p>Completed and verified on GitHub.</p>}
                  {execution.status === "UNOBSERVED" && <p>Outcome needs review. Check GitHub before taking further action.</p>}
                </div>}
                {execution?.action_type === "CREATE_CALENDAR_EVENT" && (
                  <div className="draft-workspace">
                    <strong>Proposed Calendar event</strong>
                    {execution.output.proposal ? <>
                      <p><strong>{str((execution.output.proposal as Record<string, unknown>).summary)}</strong></p>
                      <p>{when(str((execution.output.proposal as Record<string, unknown>).start))} — {when(str((execution.output.proposal as Record<string, unknown>).end))}</p>
                      <p>{str((execution.output.proposal as Record<string, unknown>).description)}</p>
                    </> : <p>{str(execution.output.reason)}</p>}
                    {approvable && <button className="primary-button" onClick={approveExecution}>Approve &amp; Create event</button>}
                    {execution.status === "VERIFIED_SUCCESS" && <p>Created and verified in Calendar.</p>}
                    {execution.status === "DRAFT_READY" && <p>Draft only: no event will be created.</p>}
                    {execution.status === "BLOCKED_BY_POLICY" && <p>External actions are disabled.</p>}
                  </div>
                )}
                {execution?.action_type === "NOTIFY" && <div className="notification-result"><strong>{str(execution.output.title) || "Jiraya notification"}</strong><p>{str(execution.output.message)}</p></div>}
              </div>
            )}
          </section>

          <aside className="inspector" aria-label="Automation configuration">
            <button className="ghost-button inspector-close" onClick={() => setInspectorOpen(false)}>Close configuration ×</button>
            <div className="inspector-heading"><span className="eyebrow">Configuration</span><h2>Automation logic</h2><p>Define what Jiraya should notice, what context it may use, and what action it may take.</p></div>
            {configurationError && <div className="route-notice" role="alert">{configurationError}</div>}
            <label>Name<input value={draft.name} onChange={(e) => updateDraft((current) => ({ ...current, name: e.target.value }))} /></label>
            <label>Description<textarea value={draft.description} onChange={(e) => updateDraft((current) => ({ ...current, description: e.target.value }))} /></label>
            <label>Trigger source<select aria-label="Trigger source" value={draft.source} onChange={(e) => selectSource(e.target.value as AutomationInput["source"])}>
              {services.filter((service) => isConnected(connections, service.id) || service.id === draft.source).map((service) => <option key={service.id} value={service.id} disabled={!isConnected(connections, service.id)}>{service.label}{!isConnected(connections, service.id) ? " · not connected" : ""}</option>)}
            </select></label>
            {!canSave && <div className="route-notice">Connect the selected trigger and action services to activate this workflow. You can connect accounts in the sidebar.</div>}
            {draft.source === "github" && <label>GitHub event<select value={draft.trigger_type} onChange={(e) => updateDraft((current) => ({...current, trigger_type: e.target.value as AutomationInput["trigger_type"]}))}><option value="GITHUB_ISSUE_CHANGED">Issue changed</option><option value="GITHUB_PR_CHANGED">Pull request changed</option><option value="GITHUB_WORKFLOW_CHANGED">Workflow run changed</option></select></label>}
            {(draft.source === "github" || ["CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"].includes(str(draft.action_config.type))) && <label>GitHub repository<input placeholder="TheNityant/repository-name" value={str(draft.action_config.repository)} onChange={(e) => updateAction("repository", e.target.value)}/></label>}
            <div className="section-divider" /><span className="section-title">Match {draft.source === "gmail" ? "incoming Gmail" : draft.source === "github" ? "GitHub changes" : "Calendar changes"}</span>
            <label>Semantic condition<textarea rows={5} placeholder="Example: A professor tells me an assignment/submission deadline." value={str(draft.match_config.semantic_condition)} onChange={(e) => updateMatch("semantic_condition", e.target.value)} /></label>
            <div className="two-column">
              {draft.source === "gmail" && <label>Sender contains<input value={str(draft.match_config.sender_contains)} onChange={(e) => updateMatch("sender_contains", e.target.value)} /></label>}
              <label>{draft.source === "gmail" ? "Subject contains" : draft.source === "github" ? "Issue / workflow title contains" : "Event title contains"}<input value={str(draft.match_config.subject_contains)} onChange={(e) => updateMatch("subject_contains", e.target.value)} /></label>
            </div>
            <label>Body contains<input value={str(draft.match_config.body_contains)} onChange={(e) => updateMatch("body_contains", e.target.value)} /></label>
            <label>Personal context / operating instructions<textarea rows={4} value={str(draft.context_config.instructions)} onChange={(e) => updateContext("instructions", e.target.value)} /></label>
            <div className="section-divider" /><span className="section-title">Action &amp; policy</span>
            <label>Action<select aria-label="Action" value={str(draft.action_config.type) || "DRAFT_REPLY"} onChange={(e) => updateAction("type", e.target.value)}>
              {!allowedActions.includes(str(draft.action_config.type)) && <option disabled value={str(draft.action_config.type)}>{actionLabels[str(draft.action_config.type)] || "Select an action"} · connection required</option>}
              {allowedActions.map((action) => <option key={action} value={action}>{actionLabels[action]}</option>)}
            </select></label>
            <p className="form-note">Only actions supported by your connected services are available.</p>
            {draft.action_config.type === "CREATE_GITHUB_BRANCH" && <>
              <label>New GitHub branch<input value={str(draft.action_config.branch)} onChange={(e) => updateAction("branch", e.target.value)} placeholder="feature/email-follow-up"/></label>
              <label>Source GitHub branch<input value={str(draft.action_config.from_ref) || "main"} onChange={(e) => updateAction("from_ref", e.target.value)}/></label>
            </>}
            {draft.action_config.type === "CREATE_CALENDAR_EVENT" && <div className="policy-explainer">Provide your timezone and any default event duration in context. Jiraya prepares a dated event from matching emails; unclear times produce a notification. Calendar invitations are not sent.</div>}
            <label>Action instructions<textarea rows={4} value={str(draft.action_config.instructions)} onChange={(e) => updateAction("instructions", e.target.value)} /></label>
            <label>Approval policy<select value={draft.approval_policy} onChange={(e) => updateDraft((current) => ({ ...current, approval_policy: e.target.value as AutomationInput["approval_policy"] }))}><option value="DRAFT_ONLY">Draft only</option><option value="REQUIRED">Ask before action</option><option value="AUTO">Automatic</option></select></label>
            {draft.approval_policy === "AUTO" && <div className="policy-explainer">Automatic means no approval prompt for a normal match. Jiraya acts when the configured action safely covers the message; changed or out-of-scope situations become a notification instead of silently switching back to approval.</div>}
            <label className="toggle-row"><span><strong>Automation enabled</strong><small>Enabled automations are watched automatically by their connected service.</small></span><input type="checkbox" checked={draft.enabled} onChange={(e) => updateDraft((current) => ({ ...current, enabled: e.target.checked }))} /></label>
            {selected && <button className="danger-link" onClick={removeSelected}>Delete automation</button>}
          </aside>
        </>
      ) : (
        <section className={`operations-page ${["GITHUB", "CALENDAR"].includes(activeView) ? "service-page" : ""}`}>
          {activeView === "GITHUB" ? <GitHubWorkspace onCreateAutomation={(repository, trigger) => beginAutomation("github", repository, trigger)} /> : activeView === "CALENDAR" ? <CalendarWorkspace onCreateAutomation={() => beginAutomation("google_calendar")} /> : activeView === "NOTIFICATIONS" ? (
            <>
              <header className="operations-header"><div><span className="eyebrow">Notification center</span><h1>What Jiraya noticed and did</h1><p>“Needs you” means you have work to review. “Done by Jiraya” is a completed automatic action. FYI is informational. State changes explain obligation lifecycle updates.</p></div><span className="unread-summary">{unreadCount} unread</span></header>
              <div className="filter-tabs">
                {["ALL", "ACTION_REQUIRED", "AUTOMATION_EXECUTED", "INFORMATION", "STATE_CHANGED"].map((filter) => <button key={filter} className={notificationFilter === filter ? "active" : ""} onClick={() => setNotificationFilter(filter)}>{filter === "ALL" ? "All" : notificationLabel(filter)}</button>)}
              </div>
              <div className="operations-list">
                {filteredNotifications.map((item) => (
                  <button className={`notification-card ${item.read_at ? "read" : "unread"} severity-${item.severity.toLowerCase()}`} key={item.id} onClick={() => markRead(item)}>
                    <div className="notification-card-top"><span className="notification-category">{notificationIcon(item.category)} · {notificationLabel(item.category)}</span><span>{when(item.created_at)}</span></div>
                    <strong>{item.title}</strong><p>{item.message}</p>
                    <div className="notification-meta"><span>{nice(item.source)}</span>{item.obligation_id && <span>Obligation linked</span>}{!item.read_at && <span>Unread</span>}</div>
                  </button>
                ))}
                {!filteredNotifications.length && <div className="empty-state"><strong>No notifications here.</strong><span>Background automation events will appear automatically.</span></div>}
              </div>
            </>
          ) : (
            <>
              <header className="operations-header"><div><span className="eyebrow">Obligations</span><h1>Work that remains true over time</h1><p>Only work that still requires an outcome stays open. A simple “okay, I’ll check” from the other person is not a new task for you; Jiraya records it as an informational thread update instead.</p></div></header>
              <div className="filter-tabs"><button className={obligationFilter === "OPEN" ? "active" : ""} onClick={() => setObligationFilter("OPEN")}>Open</button><button className={obligationFilter === "RESOLVED" ? "active" : ""} onClick={() => setObligationFilter("RESOLVED")}>Resolved / closed</button></div>
              <div className="operations-list obligation-grid">
                {filteredObligations.map((item) => (
                  <article className={`obligation-card ${item.status === "OPEN" ? "open" : "closed"}`} key={item.id}>
                    <div className="obligation-card-top"><span>{nice(item.status)}</span><span>{when(item.updated_at)}</span></div>
                    <strong>{item.title}</strong><p>{item.description}</p>
                    <div className="notification-meta"><span>{nice(item.source)}</span><span>{nice(item.obligation_type)}</span>{item.external_thread_id && <span>Gmail thread</span>}</div>
                    {item.resolution && Object.keys(item.resolution).length > 0 && <div className="resolution-box"><span className="small-label">Resolution</span><p>{str(item.resolution.reason) || nice(item.status)}</p></div>}
                  </article>
                ))}
                {!filteredObligations.length && <div className="empty-state"><strong>No {obligationFilter.toLowerCase()} obligations.</strong><span>Requests, deadlines and later changes will appear here as Jiraya observes them.</span></div>}
              </div>
            </>
          )}
        </section>
      )}
    </main>
  );
}
