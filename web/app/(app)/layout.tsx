import { redirect } from "next/navigation";
import Link from "next/link";
import { auth, signOut } from "@/auth";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Guard #2 of three, and the one that makes the middleware matcher a
 * performance optimization rather than a correctness dependency: any route
 * added under (app) is protected here even if someone forgets the matcher.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user) redirect("/sign-in");

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex h-[52px] shrink-0 items-center gap-4 border-b border-border px-4">
        <Link href="/library" className="flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 22 22" aria-hidden>
            <rect x="1" y="1" width="20" height="20" rx="5" fill="var(--primary)" />
            <rect x="5" y="7" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
            <rect x="5" y="12" width="8" height="2.5" rx="1.25" fill="var(--primary-foreground)" opacity="0.75" />
            <rect x="14" y="9.5" width="3.5" height="3" rx="1.5" fill="var(--primary-foreground)" />
          </svg>
          <span className="text-ui font-semibold text-foreground-strong">Knowledge Intelligence</span>
        </Link>
        <nav className="flex items-center gap-1">
          {[
            { href: "/library", label: "Library" },
            { href: "/ask", label: "Ask" },
            { href: "/eval", label: "Quality" },
          ].map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="text-ui rounded-md px-2.5 py-1 text-muted-foreground transition-colors duration-[120ms] hover:bg-surface hover:text-foreground"
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-ui-sm text-muted-foreground">{session.user.email}</span>
          <ThemeToggle />
          {/* POST form, not a GET link: a sign-out link can be CSRF'd or
              triggered by a prefetch. */}
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/" });
            }}
          >
            <button
              type="submit"
              className="text-ui rounded-md px-2.5 py-1 text-muted-foreground transition-colors duration-[120ms] hover:bg-surface hover:text-foreground"
            >
              Sign out
            </button>
          </form>
        </div>
      </header>
      <main className="flex-1">{children}</main>
    </div>
  );
}
