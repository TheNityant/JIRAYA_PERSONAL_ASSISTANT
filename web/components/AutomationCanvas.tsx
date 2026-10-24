"use client";

import { useEffect, useMemo } from "react";
import { Background, Controls, Edge, Handle, Node, NodeProps, Position, ReactFlow, useNodesState } from "@xyflow/react";
import type { AutomationInput, ConnectionCatalogItem } from "@/lib/api";
import { calendarScheduling, actionLabels, actionTarget, isConnected, services } from "@/lib/automation-graph";
import ServiceIcon from "./ServiceIcon";

type CardData = {label: string; detail: string; kicker: string; service: string; connected?: boolean; active?: boolean; role: string; badge?: string; color?: string};
type CardNode = Node<CardData, "card">;
function Card({data}: NodeProps<CardNode>) {
  return <div className={`graph-card ${data.active ? "active" : ""} ${data.connected === false ? "disconnected" : ""} role-${data.role}`} style={{"--service-color": data.color || "#9cc4ae"} as React.CSSProperties}>
    {data.role !== "source" && <Handle type="target" position={Position.Left}/>}
    <div className="graph-card-top"><span className="service-icon"><ServiceIcon service={data.service}/></span><span className="graph-kicker">{data.kicker}</span>{data.badge && <span className="graph-badge">{data.badge}</span>}</div>
    <strong>{data.label}</strong><p>{data.detail}</p>
    {data.role !== "evidence" && data.role !== "work" && <Handle type="source" position={Position.Right}/>}
  </div>;
}
const nodeTypes = {card: Card};

export default function AutomationCanvas({draft, connections, onSource, onAction}: {draft: AutomationInput; connections: ConnectionCatalogItem[]; onSource: (source: AutomationInput["source"]) => void; onAction: (action: string) => void}) {
  const action = String(draft.action_config.type || "DRAFT_REPLY");
  const target = actionTarget(action);
  const scheduling = calendarScheduling(draft);
  const nodes = useMemo<CardNode[]>(() => {
    const cards: CardNode[] = [];
    for (const [i, service] of services.entries()) {
      const connected = isConnected(connections, service.id);
      cards.push({id: `source:${service.id}`, type: "card", position: {x: 0, y: i*185}, data: {service: service.id, color: service.color, kicker: "Trigger service", label: service.label, detail: connected ? service.description : "Connect to use as a trigger", role: "source", connected, active: draft.source === service.id, badge: connected ? "Connected" : "Offline"}});
      cards.push({id: `target:${service.id}`, type: "card", position: {x: 900, y: i*185}, data: {service: service.id, color: service.color, kicker: "Action service", label: service.label, detail: service.id === "gmail" ? "Send a contextual reply" : service.id === "google_calendar" ? "Create a dated event" : "Create an issue or branch", role: "destination", connected, active: target === service.id, badge: target === service.id ? "Selected" : undefined}});
    }
    cards.push(
      {id: "match", type: "card", position: {x: 300, y: 95}, data: {label: "Understand the event", detail: String(draft.match_config.semantic_condition || "Match your filters and conditions"), kicker: "Match + context", service: "logic", role: "logic", active: true}},
      {id: "policy", type: "card", position: {x: 300, y: 300}, data: {label: target === "notify" ? "In-app notification" : draft.approval_policy === "AUTO" ? "Automatic" : draft.approval_policy === "DRAFT_ONLY" ? "Draft only" : "Ask before action", detail: target === "notify" ? "Save an update without an external write" : draft.approval_policy === "AUTO" ? "Act within your configured instructions" : draft.approval_policy === "DRAFT_ONLY" ? "Prepare a proposal without performing a write" : "Keep the proposal ready for your review", kicker: "Your policy", service: "check", role: "logic", active: true}},
      {id: "action", type: "card", position: {x: 600, y: 205}, data: {label: actionLabels[action] || action, detail: String(draft.action_config.instructions || "Follow your action instructions"), kicker: "Configured action", service: target, role: "logic", active: true}},
      {id: "target:notify", type: "card", position: {x: 900, y: 555}, data: {label: "Notifications", detail: "Deliver an update inside Jiraya", kicker: "Workspace", service: "notify", role: "destination", active: target === "notify"}},
      {id: "evidence", type: "card", position: {x: 600, y: 500}, data: {label: target === "notify" ? "Notification saved" : "Verify the outcome", detail: target === "notify" ? "Keep a durable record in your workspace" : "Check the provider and record the result", kicker: "Execution evidence", service: "check", role: "evidence"}},
    );
    if (scheduling) cards.push({id:"availability",type:"card",position:{x:600,y:0},data:{label:"Check Calendar availability",detail:"Check free/busy and working hours; recheck before sending",kicker:"Read-only scheduling context",service:"google_calendar",role:"logic",active:true}});
    if (action === "DRAFT_REPLY") cards.push({id: "work", type: "card", position: {x: 300, y: 555}, data: {label: "Track later changes", detail: "Reconcile the email thread and outstanding work", kicker: "After a reply", service: "refresh", role: "work"}});
    return cards.map((card) => ({...card, style: {width: 235}, draggable: true}));
  }, [draft.source, draft.approval_policy, draft.match_config.semantic_condition, draft.action_config.instructions, action, scheduling, connections]);
  const [displayNodes, setNodes, onNodesChange] = useNodesState<CardNode>(nodes);
  useEffect(() => {setNodes((current) => nodes.map((node) => ({...node, position: current.find((n) => n.id === node.id)?.position || node.position})));}, [nodes, setNodes]);
  const edges = useMemo<Edge[]>(() => {
    const all: Edge[] = services.filter((s) => isConnected(connections, s.id)).map((s) => ({id: `in:${s.id}`, source: `source:${s.id}`, target: "match", className: draft.source === s.id ? "route-active" : "route-available", animated: draft.source === s.id, type: "smoothstep"}));
    for (const [source, destination] of [["match", "policy"], ["policy", "action"], ["action", `target:${target}`], [`target:${target}`, "evidence"]]) all.push({id: `${source}->${destination}`, source, target: destination, className: "route-active", type: "smoothstep", animated: true});
    if (scheduling) all.push({id:"read-calendar",source:"source:google_calendar",target:"availability",className:"route-active",type:"smoothstep",animated:true},{id:"schedule-reply",source:"availability",target:"action",className:"route-active",type:"smoothstep",animated:true});
    if (action === "DRAFT_REPLY") all.push({id: "follow-up", source: `target:${target}`, target: "work", className: "route-available", type: "smoothstep"});
    return all;
  }, [connections, draft.source, target, action, scheduling]);
  return <div className="flow-area">
    <div className="graph-toolbar"><div><span className="eyebrow">Connected service network</span><p>Choose a trigger. Follow the action to its destination.</p></div><span className="status-pill"><i/>{connections.filter((c) => c.status === "CONNECTED").length} connected</span></div>
    <ReactFlow nodes={displayNodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onNodeClick={(_, node) => {
      if (node.id.startsWith("source:") && node.data.connected) onSource(node.data.service as AutomationInput["source"]);
      if (node.id.startsWith("target:") && (node.data.connected || node.data.service === "notify") && (draft.source === "gmail" || node.data.service === "notify")) onAction(node.data.service === "notify" ? "NOTIFY" : node.data.service === "gmail" ? "DRAFT_REPLY" : node.data.service === "github" ? "CREATE_GITHUB_ISSUE" : "CREATE_CALENDAR_EVENT");
    }} fitView fitViewOptions={{padding: .12}} minZoom={.2} maxZoom={1.6} proOptions={{hideAttribution: true}}><Background gap={22} size={1} color="#29333e"/><Controls showInteractive={false}/></ReactFlow>
    <div className="graph-legend"><span><i className="legend-line"/>Selected route</span><span><i className="legend-line available"/>Connected trigger</span><span>One trigger → one configured action</span></div>
  </div>;
}
