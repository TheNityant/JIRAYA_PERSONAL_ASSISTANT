"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  Edge,
  MarkerType,
  Node,
  ReactFlow,
} from "@xyflow/react";

import { api, Automation, AutomationInput } from "@/lib/api";

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

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export default function AutomationBuilder() {
  const [automations, setAutomations] = useState<Automation[]>([]);
  const [selected, setSelected] = useState<Automation | null>(null);
  const [draft, setDraft] = useState<AutomationInput>(emptyAutomation);
  const [status, setStatus] = useState("Loading automations...");
  const [preview, setPreview] = useState<any>(null);

  async function refresh(preferredId?: string) {
    const list = await api.listAutomations();
    setAutomations(list);
    const next = list.find((item) => item.id === preferredId) ?? list[0] ?? null;
    selectAutomation(next);
    setStatus(list.length ? "Ready" : "Create your first automation");
  }

  useEffect(() => {
    refresh().catch((error) => setStatus(`Backend unavailable: ${error.message}`));
  }, []);

  function selectAutomation(item: Automation | null) {
    setSelected(item);
    setPreview(null);
    if (!item) {
      setDraft(emptyAutomation);
      return;
    }
    setDraft({
      name: item.name,
      description: item.description,
      enabled: item.enabled,
      source: item.source,
      trigger_type: item.trigger_type,
      match_config: { ...item.match_config },
      context_config: { ...item.context_config },
      action_config: { ...item.action_config },
      approval_policy: item.approval_policy,
    });
  }

  const nodes = useMemo<Node[]>(() => {
    const semantic = str(draft.match_config.semantic_condition) || "Any configured match";
    const action = str(draft.action_config.type) || "DRAFT_REPLY";
    const items = [
      ["trigger", "Trigger", "Gmail · New email", 0],
      ["match", "Match", semantic, 190],
      ["context", "Context", str(draft.context_config.instructions) || "User context", 380],
      ["action", "Action", action.replaceAll("_", " "), 570],
      ["approval", "Approval", draft.approval_policy.replaceAll("_", " "), 760],
      ["verify", "Verifier", "Prove the action happened", 950],
      ["reconcile", "Reconciliator", "Observe later world changes", 1140],
      ["obligation", "Obligation", "Track remaining work", 1330],
    ] as const;
    return items.map(([id, title, detail, x]) => ({
      id,
      position: { x, y: 180 },
      data: {
        label: (
          <div className="flow-node">
            <span className="flow-node-kicker">{title}</span>
            <strong>{detail}</strong>
          </div>
        ),
      },
      style: { width: 166 },
    }));
  }, [draft]);

  const edges = useMemo<Edge[]>(() => {
    const ids = ["trigger", "match", "context", "action", "approval", "verify", "reconcile", "obligation"];
    return ids.slice(0, -1).map((id, index) => ({
      id: `${id}-${ids[index + 1]}`,
      source: id,
      target: ids[index + 1],
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
  }, []);

  function updateMatch(key: string, value: unknown) {
    setDraft((current) => ({
      ...current,
      match_config: { ...current.match_config, [key]: value },
    }));
  }

  function updateContext(key: string, value: unknown) {
    setDraft((current) => ({
      ...current,
      context_config: { ...current.context_config, [key]: value },
    }));
  }

  function updateAction(key: string, value: unknown) {
    setDraft((current) => ({
      ...current,
      action_config: { ...current.action_config, [key]: value },
    }));
  }

  async function save() {
    setStatus("Saving...");
    try {
      const saved = selected
        ? await api.updateAutomation(selected.id, draft)
        : await api.createAutomation(draft);
      await refresh(saved.id);
      setStatus("Saved");
    } catch (error: any) {
      setStatus(`Save failed: ${error.message}`);
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
    if (!selected) return;
    setStatus("Testing against latest unread Gmail...");
    try {
      const result = await api.previewGmail(selected.id);
      setPreview(result);
      setStatus(result.match?.matches ? "Latest email matched" : "Latest email did not match");
    } catch (error: any) {
      setStatus(`Preview failed: ${error.message}`);
    }
  }

  async function removeSelected() {
    if (!selected) return;
    if (!window.confirm(`Delete automation “${selected.name}”?`)) return;
    await api.deleteAutomation(selected.id);
    await refresh();
  }

  return (
    <main className="workspace">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">J</div>
          <div>
            <strong>Jiraya</strong>
            <span>Personal operations AI</span>
          </div>
        </div>

        <div className="sidebar-title-row">
          <span>Automations</span>
          <button className="icon-button" onClick={() => selectAutomation(null)} title="New automation">+</button>
        </div>

        <div className="automation-list">
          {automations.map((item) => (
            <button
              key={item.id}
              className={`automation-list-item ${selected?.id === item.id ? "active" : ""}`}
              onClick={() => selectAutomation(item)}
            >
              <span className={`status-dot ${item.enabled ? "on" : "off"}`} />
              <div>
                <strong>{item.name}</strong>
                <span>{item.source} · {item.trigger_type.toLowerCase().replaceAll("_", " ")}</span>
              </div>
            </button>
          ))}
          {!automations.length && (
            <button className="preset-card" onClick={createPreset}>
              <strong>Start with Internship Assistant</strong>
              <span>Turn the current Gmail demo into an editable automation.</span>
            </button>
          )}
        </div>

        <div className="sidebar-footer">
          <span className="small-label">Runtime</span>
          <strong>{status}</strong>
        </div>
      </aside>

      <section className="canvas-shell">
        <header className="topbar">
          <div>
            <span className="eyebrow">Automation Builder</span>
            <h1>{draft.name}</h1>
          </div>
          <div className="topbar-actions">
            {selected && <button className="secondary-button" onClick={testLatestEmail}>Test latest Gmail</button>}
            <button className="primary-button" onClick={save}>{selected ? "Save changes" : "Create automation"}</button>
          </div>
        </header>

        <div className="flow-area">
          <ReactFlow nodes={nodes} edges={edges} fitView minZoom={0.45} maxZoom={1.4} proOptions={{ hideAttribution: true }}>
            <Background gap={22} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>

        {preview && (
          <div className={`preview-strip ${preview.match?.matches ? "matched" : "not-matched"}`}>
            <div>
              <span className="small-label">Latest Gmail preview</span>
              <strong>{preview.message?.subject ?? "No unread message"}</strong>
            </div>
            {preview.match && (
              <div className="preview-result">
                <strong>{preview.match.matches ? "MATCH" : "NO MATCH"}</strong>
                <span>{Math.round(preview.match.confidence * 100)}% · {preview.match.reason}</span>
              </div>
            )}
          </div>
        )}
      </section>

      <aside className="inspector">
        <div className="inspector-heading">
          <span className="eyebrow">Configuration</span>
          <h2>User-owned logic</h2>
          <p>Nothing here is tied to the phrase “internship inquiry”. This definition is stored in PostgreSQL and interpreted at runtime.</p>
        </div>

        <label>
          Name
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>

        <label>
          Description
          <textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        </label>

        <div className="section-divider" />
        <span className="section-title">Match incoming Gmail</span>

        <label>
          Semantic condition
          <textarea
            rows={5}
            placeholder="Example: An employer is asking to schedule or continue an internship interview process."
            value={str(draft.match_config.semantic_condition)}
            onChange={(e) => updateMatch("semantic_condition", e.target.value)}
          />
        </label>

        <div className="two-column">
          <label>
            Sender contains
            <input value={str(draft.match_config.sender_contains)} onChange={(e) => updateMatch("sender_contains", e.target.value)} />
          </label>
          <label>
            Subject contains
            <input value={str(draft.match_config.subject_contains)} onChange={(e) => updateMatch("subject_contains", e.target.value)} />
          </label>
        </div>

        <label>
          Body contains
          <input value={str(draft.match_config.body_contains)} onChange={(e) => updateMatch("body_contains", e.target.value)} />
        </label>

        <label>
          Personal context / operating instructions
          <textarea
            rows={4}
            placeholder="Example: I cannot interview Wednesday. Prefer Friday after 1 PM."
            value={str(draft.context_config.instructions)}
            onChange={(e) => updateContext("instructions", e.target.value)}
          />
        </label>

        <div className="section-divider" />
        <span className="section-title">Action & policy</span>

        <label>
          Action
          <select value={str(draft.action_config.type) || "DRAFT_REPLY"} onChange={(e) => updateAction("type", e.target.value)}>
            <option value="DRAFT_REPLY">Draft reply</option>
            <option value="NOTIFY">Notify me</option>
          </select>
        </label>

        <label>
          Action instructions
          <textarea
            rows={4}
            value={str(draft.action_config.instructions)}
            onChange={(e) => updateAction("instructions", e.target.value)}
          />
        </label>

        <label>
          Approval policy
          <select
            value={draft.approval_policy}
            onChange={(e) => setDraft({ ...draft, approval_policy: e.target.value as AutomationInput["approval_policy"] })}
          >
            <option value="DRAFT_ONLY">Draft only</option>
            <option value="REQUIRED">Ask before action</option>
            <option value="AUTO">Automatic</option>
          </select>
        </label>

        <label className="toggle-row">
          <span>
            <strong>Automation enabled</strong>
            <small>Paused automations remain saved but do not match events.</small>
          </span>
          <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
        </label>

        {selected && <button className="danger-link" onClick={removeSelected}>Delete automation</button>}
      </aside>
    </main>
  );
}
