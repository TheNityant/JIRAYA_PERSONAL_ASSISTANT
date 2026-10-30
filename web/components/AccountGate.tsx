"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, API_BASE, AccountState, ConnectionCatalogItem } from "@/lib/api";
import AutomationBuilder from "@/components/AutomationBuilder";

export default function AccountGate() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [signup, setSignup] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connections, setConnections] = useState<ConnectionCatalogItem[] | null>(null);
  const [onboarding, setOnboarding] = useState(true);

  async function refresh(checkSession = false) {
    try {
      const state = await api.account();
      setAccount(state);
      if (checkSession && state.auth_required && !state.user) {
        setConnections(null);
        setError("Your password was accepted, but the browser did not retain the sign-in session. Check the deployment's cookie settings or use the same-origin API proxy.");
        return;
      }
      setError("");
      if (state.user || !state.auth_required) {
        const [result, savedRules] = await Promise.all([api.listConnections(), api.listAutomations()]);
        setConnections(result.catalog);
        setOnboarding(!savedRules.length && !result.catalog.some((item) => item.status === "CONNECTED"));
      }
    } catch (e: any) { setError(e.message); }
  }

  useEffect(() => {
    // Keep local cookies and existing OAuth callbacks on the same host.
    if (window.location.hostname === "localhost" && new URL(API_BASE, window.location.origin).hostname === "127.0.0.1") {
      const url = new URL(window.location.href); url.hostname = "127.0.0.1";
      window.location.replace(url.toString()); return;
    }
    void refresh();
    const expired = () => {setAccount((state) => state ? {...state, user: null} : state); setConnections(null); setError("Your session expired. Sign in again.");};
    window.addEventListener("jiraya-session-expired", expired);
    return () => window.removeEventListener("jiraya-session-expired", expired);
  }, []);

  async function authenticate(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError("");
    try {
      if (signup) await api.register(name, email, password);
      else await api.login(email, password);
      setPassword(""); await refresh(true);
    } catch (e: any) {setError(e.message);}
    finally {setBusy(false);}
  }

  async function connect(item: ConnectionCatalogItem) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await api.startConnection(item.id as "gmail" | "google_calendar" | "github");
      window.location.assign(response.authorization_url);
    } catch (e: any) {setError(e.message); setBusy(false);}
  }

  if (!account) return <main className="account-shell"><section className="account-card"><h1>Jiraya</h1><p>{error || "Opening your workspace…"}</p>{error && <button onClick={() => refresh()}>Try again</button>}</section></main>;
  if (account.auth_required && !account.user) return <main className="account-shell"><section className="account-card">
    <span className="eyebrow">Jiraya · Personal operations</span><h1>{signup ? "Create your account" : "Welcome back"}</h1>
    <p>Sign in, connect your accounts, and manage your automations here.</p>
    <form onSubmit={authenticate}>
      {signup && <label>Your name<input required maxLength={255} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)}/></label>}
      <label>Email<input required type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}/></label>
      <label>Password<input required type="password" minLength={12} maxLength={128} autoComplete={signup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)}/></label>
      {signup && <small>Use at least 12 characters.</small>}
      {error && <p role="alert" className="account-error">{error}</p>}
      <button disabled={busy} className="primary-button">{busy ? "Please wait…" : signup ? "Create account" : "Sign in"}</button>
    </form>
    {account.registration_enabled && <button className="ghost-button" disabled={busy} onClick={() => {setSignup(!signup); setError("");}}>{signup ? "Already have an account? Sign in" : "New to Jiraya? Create account"}</button>}
  </section></main>;
  return <>
    <div className="account-bar"><span>{account.user?.display_name || "Local workspace"}</span>{account.user && <button disabled={busy} onClick={async () => {setBusy(true); try {await api.logout(); setConnections(null); setAccount({...account, user: null});} catch (e: any) {setError(e.message);} finally {setBusy(false);}}}>Sign out</button>}</div>
    {error && <p role="alert" className="account-error">{error}</p>}
    {onboarding && connections && account.auth_required ? <main className="account-shell"><section className="account-card setup-card">
      <span className="eyebrow">Set up your workspace</span><h1>Connect your accounts</h1><p>Choose a service and authorize Jiraya on its website. You can connect more accounts later.</p>
      {connections.map((item) => <article className="connection-card" key={item.id}><div className="connection-copy"><strong>{item.label}</strong><span>{item.status === "SETUP_REQUIRED" ? "This connection has not been enabled by the app owner yet." : item.account || item.description}</span></div><button disabled={busy || item.status !== "AVAILABLE"} onClick={() => connect(item)}>{item.status === "CONNECTED" ? "Connected" : item.status === "SETUP_REQUIRED" ? "Unavailable" : "Connect"}</button></article>)}
      <button className="primary-button" onClick={() => setOnboarding(false)}>Continue to workspace</button>
    </section></main> : <AutomationBuilder accountId={account.user?.id || "local"} />}
  </>;
}
