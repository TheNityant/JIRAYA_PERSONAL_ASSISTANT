import type { Metadata } from "next";
import PolicyLayout from "@/components/PolicyLayout";

export const metadata: Metadata = {title: "Terms of Service · Jiraya Personal Assistant"};

export default function TermsPage() {
  return <PolicyLayout title="Terms of Service">
    <p>These terms describe use of Jiraya Personal Assistant, operated by Nityant Tiwari. By creating an account or using the service, you agree to these terms. Please also read our <a href="/privacy">Privacy Policy</a>.</p>
    <h2>The service</h2>
    <p>Jiraya is a developing personal automation service connecting authorized Gmail, Google Calendar and GitHub accounts with configured workflows and AI interpretation. An optional local companion handles local Git operations and Meet attendance. Features may change, be unavailable or contain errors.</p>
    <h2>Your account and permissions</h2>
    <p>Provide accurate account information, protect your password and pairing credentials, and connect only accounts, repositories and meetings you are authorized to access. Your Jiraya registration does not guarantee Google or GitHub authorization. Provider verification, organization policies, account restrictions and expired or revoked tokens can limit access.</p>
    <h2>Automation and AI</h2>
    <p>You control the rules, service connections and available approval settings. An automation set to automatic may send replies or create or change external data without asking for each action. Test rules with non-sensitive examples before using them broadly. AI can misinterpret dates, instructions, recipients or intent; review outputs and use approval controls where appropriate.</p>
    <p>Signing out or closing the website does not turn off enabled background automations. Disable the relevant rule or disconnect the provider to stop future actions. Local operations require an available companion; Meet attendance requires an awake computer and may require Google browser sign-in and host admission. The current attendance feature does not provide recording, transcription or voice participation.</p>
    <h2>Acceptable use</h2>
    <p>Do not use Jiraya for unauthorized access, spam, harassment, deception, unlawful content or actions that violate others’ privacy or intellectual-property rights. Follow the policies of connected providers. Obtain any required permission before joining meetings or processing another person’s information. Do not upload secrets or information you are not permitted to share with the services described in the Privacy Policy.</p>
    <h2>Your content and external services</h2>
    <p>You retain your rights in your content. You authorize the processing necessary to perform the features you enable. Google, GitHub, hosting providers and AI providers operate independently and their applicable terms also apply. Jiraya cannot guarantee their availability, admission decisions or continued API access.</p>
    <h2>Availability and stopping use</h2>
    <p>The service is provided as available, without a promise of uninterrupted operation, perfect AI accuracy or successful completion of every automation. It should not be relied on as the sole system for critical obligations. Keep appropriate backups and independently check important outcomes. Nothing in these terms excludes rights or responsibilities that applicable law does not allow to be excluded.</p>
    <p>You can stop using Jiraya, disable rules and disconnect providers. Contact <a href="mailto:nityant.tiwari2404@gmail.com">nityant.tiwari2404@gmail.com</a> for account deletion. Disconnecting does not reverse actions already performed. Access may be restricted to address misuse, security risks or provider requirements.</p>
    <h2>Changes and contact</h2>
    <p>Changes to these terms will be published on this page with an updated date. Questions about the service or these terms can be sent to Nityant Tiwari at the email address above.</p>
  </PolicyLayout>;
}
