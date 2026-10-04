import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      orgId: string;
      role: "owner" | "admin" | "member" | "viewer";
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    uid: string;
    orgId: string;
    role: "owner" | "admin" | "member" | "viewer";
  }
}

export {};
