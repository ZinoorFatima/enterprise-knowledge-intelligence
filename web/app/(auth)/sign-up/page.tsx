import { Suspense } from "react";
import { SignUpForm } from "@/components/auth/auth-forms";

export const metadata = { title: "Create account" };

export default function SignUpPage() {
  return (
    <>
      <h1 className="text-title-lg text-foreground-strong">Create your account</h1>
      <p className="text-ui-sm mt-1 mb-6 text-muted-foreground">
        A personal workspace is created for you automatically.
      </p>
      <Suspense fallback={<div className="h-80" />}>
        <SignUpForm />
      </Suspense>
    </>
  );
}
