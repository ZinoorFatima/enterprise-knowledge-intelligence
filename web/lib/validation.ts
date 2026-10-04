import { z } from "zod";

/** Shared by the sign-in form, the register route, and authorize(). */
export const credentialsSchema = z.object({
  email: z.string().trim().toLowerCase().email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});

export const registerSchema = z.object({
  name: z.string().trim().min(1, "Enter your name").max(120),
  email: z.string().trim().toLowerCase().email("Enter a valid email address"),
  // 12 chars minimum: this is an enterprise product and the password is the
  // only factor. Length beats composition rules for real-world strength.
  password: z
    .string()
    .min(12, "Use at least 12 characters")
    .max(200, "That password is too long"),
});

export type RegisterInput = z.infer<typeof registerSchema>;
