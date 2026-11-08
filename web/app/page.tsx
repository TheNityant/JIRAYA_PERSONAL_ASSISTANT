import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import ServiceIcon from "@/components/ServiceIcon";

export const metadata: Metadata = {
  title: "Jiraya Personal Assistant · Connected-service automations",
  description: "Learn how Jiraya connects Gmail, Google Calendar and GitHub for personal AI automations, with optional local Git tools and Google Meet attendance.",
};

const services = [
  {id: "gmail", name: "Gmail", detail: "Match incoming email against your instructions and prepare or send replies according to your approval settings."},
  {id: "google_calendar", name: "Google Calendar", detail: "Create events from meeting emails, check availability in configured scheduling workflows and monitor event changes."},
  {id: "github", name: "GitHub", detail: "Watch issue, pull request and workflow changes, and configure supported repository actions. Pair local tools for Git checkpoints."},
  {id: "meet", name: "Google Meet attendance", detail: "Opt into cloud meeting attendance and notes when the operator has deployed its worker, or use a paired computer for local attendance. Host admission may be needed."},
];

export default async function Home({searchParams}: {searchParams: Promise<Record<string, string | string[] | undefined>>}) {
  // Existing backends return OAuth results to /?connection=... . Keep that return
  // working while ordinary visitors receive a public, independent homepage.
  const query = await searchParams;
  const provider = typeof query.connection === "string" ? query.connection : "";
  const status = typeof query.status === "string" ? query.status : "";
  if (["google", "google_calendar", "github"].includes(provider) && ["connected", "error"].includes(status)) {
    const result = new URLSearchParams({connection: provider, status});
    if (typeof query.detail === "string") result.set("detail", query.detail.slice(0, 1000));
    redirect(`/app?${result}`);
  }

  return <div className="landing-page">
    <header className="landing-nav">
      <Link className="landing-brand" href="/" aria-label="Jiraya home"><span className="brand-mark">J</span><span>Jiraya<span className="landing-brand-caption">Personal Assistant</span></span></Link>
      <nav aria-label="Main navigation"><a href="#services">Services</a><a href="#how-it-works">How it works</a><Link href="/privacy">Privacy</Link><Link className="landing-signin" href="/app">Sign in</Link></nav>
    </header>
    <main>
      <section className="landing-hero">
        <div>
          <span className="eyebrow">Personal workflows · Connected services · AI interpretation</span>
          <h1>Jiraya Personal Assistant</h1>
          <p className="landing-headline">Turn everyday requests into connected workflows.</p>
          <p className="landing-description">Jiraya is a personal automation application that connects Gmail, Google Calendar and GitHub. Define what to look for, choose an action and decide whether it needs your approval. Jiraya uses AI to interpret requests and records execution outcomes so you can review what happened.</p>
          <div className="landing-actions"><Link className="landing-primary" href="/app">Open workspace <span aria-hidden="true">↗</span></Link><a className="landing-secondary" href="#how-it-works">Explore how it works</a></div>
          <p className="landing-caption">Read about the service here without signing in. An account is needed to create automations and connect your services.</p>
        </div>
        <aside className="landing-example" aria-label="Example email-to-calendar workflow">
          <span className="eyebrow">Example workflow</span><h2>A meeting request, organized.</h2>
          <ol>
            <li><span className="landing-step-icon"><ServiceIcon service="gmail"/></span><div><strong>A meeting email arrives</strong><p>Your saved rule checks the message against your conditions.</p></div></li>
            <li><span className="landing-step-icon"><ServiceIcon service="logic"/></span><div><strong>Interpret the details</strong><p>AI proposes a date, time, duration and the supplied meeting link.</p></div></li>
            <li><span className="landing-step-icon"><ServiceIcon service="google_calendar"/></span><div><strong>Create a Calendar event</strong><p>The configured approval policy determines whether to ask you first.</p></div></li>
          </ol>
          <p className="landing-example-note">An illustration of a configured automation, not a live account or execution.</p>
        </aside>
      </section>
      <section id="services" className="landing-section">
        <span className="eyebrow">Connect what you use</span><h2>Services with a specific job to do.</h2><p className="landing-section-intro">Choose your connections separately. Connecting one service does not authorize every other service.</p>
        <div className="landing-service-grid">{services.map(service => <article key={service.id}><span className={`landing-service-icon ${service.id}`}><ServiceIcon service={service.id} size={25}/></span><h3>{service.name}</h3><p>{service.detail}</p></article>)}</div>
        <p className="landing-caption">Optional cloud Meet notes transcribe admitted meetings with AssemblyAI and summarize available Google or captured transcripts. Cloud mode requires its own worker and explicit consent; voice participation is not enabled.</p>
      </section>
      <section id="how-it-works" className="landing-section">
        <span className="eyebrow">From setup to execution</span><h2>You set the rules and approval policy.</h2>
        <div className="landing-how-grid">
          <article><span>01</span><h3>Connect your accounts</h3><p>Create a Jiraya account, then authorize Gmail, Calendar or GitHub through the provider’s consent screen. You do not need to create your own OAuth application.</p></article>
          <article><span>02</span><h3>Build an automation</h3><p>Select a trigger, describe the conditions and choose a supported action. Use approval mode to review proposed changes, or enable automatic execution for a tested rule.</p></article>
          <article><span>03</span><h3>Review the outcome</h3><p>Monitor notifications and execution results. Disable a rule or disconnect a service whenever you want to stop future access through that connection.</p></article>
        </div>
      </section>
      <section className="landing-data landing-section" aria-labelledby="landing-data-title">
        <div><span className="eyebrow">Your data and permissions</span><h2 id="landing-data-title">Know what you are connecting.</h2><p>Jiraya processes authorized email, Calendar and GitHub information to run the features you configure. Relevant email content and instructions may be sent to Nebius Token Factory for AI inference. Account, connection and automation records are stored to operate your workspace.</p><p>Enabled cloud automations can continue after you close the website. Local Git and local Meet attendance require a running companion; cloud Meet attendance requires a dedicated running cloud worker. Provider authorization, availability and organization policies can affect access.</p></div>
        <div className="landing-data-links"><Link href="/privacy">Read the Privacy Policy ↗</Link><Link href="/terms">Read the Terms of Service ↗</Link><a href="mailto:nityant.tiwari2404@gmail.com">Contact the operator ↗</a></div>
      </section>
    </main>
    <footer className="public-site-footer"><div><strong>Jiraya Personal Assistant</strong><p>Operated by Nityant Tiwari · Questions or data-deletion requests: <a href="mailto:nityant.tiwari2404@gmail.com">nityant.tiwari2404@gmail.com</a></p></div><nav aria-label="Public information"><Link href="/privacy">Privacy Policy</Link><Link href="/terms">Terms of Service</Link><Link href="/app">Open workspace</Link></nav></footer>
  </div>;
}
