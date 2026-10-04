import Link from "next/link";

export const metadata = { title: "Sign-in problem" };

// Auth.js error codes mapped to copy a person can act on.
const MESSAGES: Record<string, string> = {
  OAuthAccountNotLinked:
    "An account already exists with this email address. Sign in with your password first, then link Google from settings.",
  AccessDenied:
    "Google did not confirm that this email address is verified, so we can't link it to an account.",
  Configuration: "Sign-in is misconfigured on the server. Please contact your administrator.",
  Verification: "That sign-in link has expired or was already used.",
};

export default async function AuthErrorPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const message =
    (error && MESSAGES[error]) ?? "Something went wrong while signing you in.";

  return (
    <>
      <h1 className="text-title-lg text-foreground-strong">Sign-in problem</h1>
      <p className="text-ui-sm mt-3 text-muted-foreground">{message}</p>
      <Link
        href="/sign-in"
        className="text-ui mt-6 inline-flex h-10 w-full items-center justify-center rounded-md bg-primary font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover"
      >
        Back to sign in
      </Link>
    </>
  );
}
