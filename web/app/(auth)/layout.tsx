import Link from "next/link";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-surface px-6 py-12">
      <Link href="/" className="mb-8 flex items-center gap-2.5">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
          <rect x="1" y="1" width="20" height="20" rx="5" fill="var(--primary)" />
          <rect x="5" y="7" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
          <rect x="5" y="12" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
          <rect x="14" y="9.5" width="3.5" height="3" rx="1.5" fill="var(--primary-foreground)" />
        </svg>
        <span className="text-title-sm text-foreground-strong">Knowledge Intelligence</span>
      </Link>
      <div className="w-full max-w-[400px] rounded-xl border border-border bg-surface-raised p-7 shadow-sm">
        {children}
      </div>
    </div>
  );
}
