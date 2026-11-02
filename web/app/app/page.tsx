import type { Metadata } from "next";
import Link from "next/link";
import AccountGate from "@/components/AccountGate";

export const metadata: Metadata = {title: "Workspace · Jiraya Personal Assistant"};

export default function WorkspacePage() {
  return <><AccountGate/><footer className="public-site-footer"><nav aria-label="Public information"><Link href="/">About Jiraya</Link><Link href="/privacy">Privacy Policy</Link><Link href="/terms">Terms of Service</Link><a href="mailto:nityant.tiwari2404@gmail.com">Contact support</a></nav></footer></>;
}
