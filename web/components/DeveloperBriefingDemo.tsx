"use client";

import { useState } from "react";

const examples = [
  "TheNityant/JIRAYA_PERSONAL_ASSISTANT",
  "TheNityant/LLM_visualizer",
  "TheNityant/HABIT_TRACKER_Full_Stack_App",
];
const activity = [
  {pulls: 3, issues: 2, checks: "1 check needs review", next: "Review the example failing CI check before merging the next change."},
  {pulls: 1, issues: 4, checks: "All example checks passed", next: "Review the example pull request and choose the next issue to tackle."},
  {pulls: 2, issues: 1, checks: "No example workflow runs", next: "Add a build-and-test workflow to catch regressions automatically."},
];

export default function DeveloperBriefingDemo({repositories = []}: {repositories?: string[]}) {
  const names = [...new Set(repositories.length ? repositories : examples)];
  const [selected, setSelected] = useState<string[]>(names.slice(0, 2));
  const [query, setQuery] = useState("");
  const [report, setReport] = useState<string[]>([]);
  const selection = selected.filter(name => names.includes(name));
  const visibleReport = report.filter(name => names.includes(name));

  function toggle(name: string) {
    setSelected(current => current.includes(name) ? current.filter(item => item !== name) : [...current.filter(item => names.includes(item)), name].slice(0, 3));
    setReport([]);
  }

  return <section className="briefing-demo" aria-label="Repository briefing demo">
    <div className="briefing-demo-banner"><span className="state-badge warning">DEMO · SAMPLE DATA</span><span>Runs in your browser · No credentials required</span></div>
    <div className="briefing-demo-heading"><span className="eyebrow">Jiraya / Developer intelligence</span><h2>Your projects.<br/>One clear next step.</h2><p>A preview of the repository briefing experience. Repository names identify your projects; every activity count, status and recommendation below is an illustrative example.</p></div>
    <div className="briefing-demo-stats"><div><strong>{selection.length.toString().padStart(2, "0")}</strong><span>Projects selected</span></div><div><strong>{selection.reduce((total, name) => total + activity[names.indexOf(name) % activity.length].pulls, 0)}</strong><span>Sample pull requests</span></div><div><strong>Preview</strong><span>Hermes connection pending</span></div></div>
    <label className="briefing-search">Choose up to 3 projects<input aria-label="Search demo repositories" placeholder="Search your projects…" value={query} onChange={event => setQuery(event.target.value)}/></label>
    <div className="briefing-demo-projects">{names.filter(name => name.toLowerCase().includes(query.toLowerCase())).map(name => {
      const sample = activity[names.indexOf(name) % activity.length];
      return <label className={selection.includes(name) ? "selected" : ""} key={name}><div className="briefing-demo-project-title"><input type="checkbox" checked={selection.includes(name)} disabled={!selection.includes(name) && selection.length >= 3} onChange={() => toggle(name)}/><span>{name}</span></div><div className="briefing-demo-project-meta"><span>{sample.pulls} sample PRs</span><span>{sample.issues} sample issues</span></div><p>{sample.checks}</p></label>;
    })}</div>
    {!names.some(name => name.toLowerCase().includes(query.toLowerCase())) && <p className="checkpoint-note">No matching projects. Try another search.</p>}
    <div className="briefing-demo-actions"><button className="primary-button" disabled={!selection.length} onClick={() => setReport([...selection])}>{report.length ? "Regenerate sample briefing" : "Generate sample briefing"}<span aria-hidden="true"> ↗</span></button><span>No GitHub or model requests. Nothing is committed, pushed or sent.</span></div>
    {!!visibleReport.length && <div className="briefing-demo-report" role="status"><div className="briefing-demo-report-header"><h3>Your sample briefing</h3><span className="state-badge warning">SIMULATED</span></div><p className="checkpoint-note">Example recommendations for {visibleReport.length} selected projects. These are not findings from your repositories.</p>{visibleReport.map((name, index) => <article key={name}><span className="eyebrow">Suggested next step {index + 1}</span><h4>{name}</h4><p>{activity[names.indexOf(name) % activity.length].next}</p><a className="service-link" href={`https://github.com/${name}`} target="_blank" rel="noreferrer">Open repository ↗</a></article>)}<p className="checkpoint-note">Once Hermes is configured, use Live briefing to summarize actual GitHub activity.</p></div>}
  </section>;
}
