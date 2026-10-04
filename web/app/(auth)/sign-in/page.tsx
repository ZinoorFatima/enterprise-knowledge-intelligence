import { Suspense } from "react";
import { SignInForm } from "@/components/auth/auth-forms";

export const metadata = { title: "Sign in" };

export default function SignInPage() {
  return (
    <>
      <h1 className="text-title-lg text-foreground-strong">Sign in</h1>
      <p className="text-ui-sm mt-1 mb-6 text-muted-foreground">
        Access your document corpus and ask questions across it.
      </p>
      <Suspense fallback={<div className="h-64" />}>
        <SignInForm />
      </Suspense>
    </>
  );
}
