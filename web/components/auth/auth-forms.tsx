"use client";

import { signIn } from "next-auth/react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

function safeNext(v: string | null): string {
  if (!v || !v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) {
    return "/library";
  }
  return v;
}

function GoogleButton({ next }: { next: string }) {
  return (
    <button
      type="button"
      onClick={() => signIn("google", { callbackUrl: next })}
      className="text-ui inline-flex h-10 w-full items-center justify-center gap-2.5 rounded-md border border-border-strong bg-surface-raised font-medium text-foreground transition-colors duration-[120ms] hover:bg-surface"
    >
      <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden>
        <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />
        <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z" />
        <path fill="#FBBC05" d="M3.97 10.72a5.4 5.4 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33Z" />
        <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z" />
      </svg>
      Continue with Google
    </button>
  );
}

function Divider() {
  return (
    <div className="my-5 flex items-center gap-3">
      <span className="h-px flex-1 bg-border" />
      <span className="text-caption text-subtle-foreground">or</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

const inputCls =
  "text-ui h-10 w-full rounded-md border border-border-strong bg-background px-3 text-foreground placeholder:text-subtle-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]";

const submitCls =
  "text-ui inline-flex h-10 w-full items-center justify-center rounded-md bg-primary font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover disabled:opacity-60";

export function SignInForm() {
  const params = useSearchParams();
  const router = useRouter();
  const next = safeNext(params.get("next"));
  const [pending, setPending] = useState(false);
  // Auth.js redirects here with ?error=CredentialsSignin on a bad password.
  const [error, setError] = useState<string | null>(
    params.get("error") ? "Incorrect email or password." : null,
  );

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const data = new FormData(e.currentTarget);
    const res = await signIn("credentials", {
      email: String(data.get("email") ?? ""),
      password: String(data.get("password") ?? ""),
      redirect: false,
    });
    setPending(false);
    // One message for every failure mode: distinguishing "no such user" from
    // "wrong password" is an account-enumeration oracle.
    if (!res || res.error) {
      setError("Incorrect email or password.");
      return;
    }
    router.push(next);
    router.refresh();
  }

  return (
    <>
      <GoogleButton next={next} />
      <Divider />
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <label htmlFor="email" className="text-ui-sm mb-1.5 block font-medium text-foreground">
            Work email
          </label>
          <input id="email" name="email" type="email" required autoComplete="email" className={inputCls} />
        </div>
        <div>
          <label htmlFor="password" className="text-ui-sm mb-1.5 block font-medium text-foreground">
            Password
          </label>
          <input id="password" name="password" type="password" required autoComplete="current-password" className={inputCls} />
        </div>
        {error && (
          <p role="alert" className="text-ui-sm rounded-md border border-danger bg-danger-subtle px-3 py-2 text-danger">
            {error}
          </p>
        )}
        <button type="submit" disabled={pending} className={submitCls}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="text-ui-sm mt-5 text-muted-foreground">
        No account?{" "}
        <Link href="/sign-up" className="text-primary underline underline-offset-4">
          Create one
        </Link>
      </p>
    </>
  );
}

export function SignUpForm() {
  const params = useSearchParams();
  const router = useRouter();
  const next = safeNext(params.get("next"));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const data = new FormData(e.currentTarget);
    const payload = {
      name: String(data.get("name") ?? ""),
      email: String(data.get("email") ?? ""),
      password: String(data.get("password") ?? ""),
    };

    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      setPending(false);
      const body = await res.json().catch(() => ({}));
      if (res.status === 429) setError("Too many attempts. Try again in a few minutes.");
      else if (body.error === "INVALID_REQUEST")
        setError(body.issues?.[0]?.message ?? "Check the form and try again.");
      else setError("We couldn't create that account. Try signing in instead.");
      return;
    }

    const signInRes = await signIn("credentials", {
      email: payload.email,
      password: payload.password,
      redirect: false,
    });
    setPending(false);
    if (!signInRes || signInRes.error) {
      router.push("/sign-in");
      return;
    }
    router.push(next);
    router.refresh();
  }

  const tooShort = password.length > 0 && password.length < 12;

  return (
    <>
      <GoogleButton next={next} />
      <Divider />
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <label htmlFor="name" className="text-ui-sm mb-1.5 block font-medium text-foreground">
            Name
          </label>
          <input id="name" name="name" required autoComplete="name" className={inputCls} />
        </div>
        <div>
          <label htmlFor="email" className="text-ui-sm mb-1.5 block font-medium text-foreground">
            Work email
          </label>
          <input id="email" name="email" type="email" required autoComplete="email" className={inputCls} />
        </div>
        <div>
          <label htmlFor="password" className="text-ui-sm mb-1.5 block font-medium text-foreground">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            minLength={12}
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-describedby="pw-hint"
            className={inputCls}
          />
          <p
            id="pw-hint"
            className={`text-caption mt-1.5 ${tooShort ? "text-warning" : "text-muted-foreground"}`}
          >
            At least 12 characters. Length matters more than symbols.
          </p>
        </div>
        {error && (
          <p role="alert" className="text-ui-sm rounded-md border border-danger bg-danger-subtle px-3 py-2 text-danger">
            {error}
          </p>
        )}
        <button type="submit" disabled={pending} className={submitCls}>
          {pending ? "Creating account…" : "Create account"}
        </button>
      </form>
      <p className="text-ui-sm mt-5 text-muted-foreground">
        Already have an account?{" "}
        <Link href="/sign-in" className="text-primary underline underline-offset-4">
          Sign in
        </Link>
      </p>
    </>
  );
}
