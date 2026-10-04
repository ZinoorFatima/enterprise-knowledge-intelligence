import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";

const LINKS = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/evaluation", label: "Evaluation" },
  { href: "/pricing", label: "Pricing" },
  { href: "/docs", label: "Docs" },
];

export function MarketingNav() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1120px] items-center gap-8 px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <Logo />
          <span className="text-title-sm text-foreground-strong">
            Knowledge Intelligence
          </span>
        </Link>

        <nav className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="text-ui rounded-md px-3 py-1.5 text-muted-foreground transition-colors duration-[120ms] hover:bg-surface hover:text-foreground"
            >
              {l.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <ThemeToggle />
          <Link
            href="/sign-in"
            className="text-ui rounded-md px-3 py-1.5 font-medium text-muted-foreground transition-colors duration-[120ms] hover:bg-surface hover:text-foreground"
          >
            Sign in
          </Link>
          <Link
            href="/sign-up"
            className="text-ui inline-flex h-9 items-center rounded-md bg-primary px-3.5 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover"
          >
            Start free
          </Link>
        </div>
      </div>
    </header>
  );
}

function Logo() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden>
      {/* Two lanes converging — the same idea as the pipeline diagram's centre. */}
      <rect x="1" y="1" width="20" height="20" rx="5" fill="var(--primary)" />
      <rect x="5" y="7" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
      <rect x="5" y="12" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
      <rect x="14" y="9.5" width="3.5" height="3" rx="1.5" fill="var(--primary-foreground)" />
    </svg>
  );
}
