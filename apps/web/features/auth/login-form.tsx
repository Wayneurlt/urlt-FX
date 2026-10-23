"use client";
import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FieldError } from "@/components/common/misc";
import { authApi } from "@/lib/api/endpoints";
import { errorMessage, isApiError } from "@/lib/api/client";
import { qk } from "@/lib/query-keys";

const schema = z.object({
  username: z.string().trim().min(1, "Username is required"),
  password: z.string().min(1, "Password is required"),
});
type LoginValues = z.infer<typeof schema>;

/** Only allow same-site relative redirects. */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/login") || next === "/") return "/dashboard";
  return next;
}

export function LoginForm({
  next,
  onSuccess,
  onPrefetch,
}: {
  next?: string | null;
  onSuccess: (to: string) => void;
  /** Warm up the destination page while the user types (first load can be slow in dev). */
  onPrefetch?: (to: string) => void;
}) {
  const qc = useQueryClient();
  const [error, setError] = React.useState<string | null>(null);
  const [redirecting, setRedirecting] = React.useState(false);
  const target = safeNext(next);
  React.useEffect(() => {
    onPrefetch?.(target);
  }, [onPrefetch, target]);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({ resolver: zodResolver(schema), defaultValues: { username: "", password: "" } });

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      const res = await authApi.login(values);
      qc.setQueryData(qk.me, res);
      setRedirecting(true);
      onSuccess(target);
    } catch (e) {
      if (isApiError(e) && e.status === 429) {
        const retry = e.details?.["retry_after_seconds"];
        setError(`Too many failed attempts.${typeof retry === "number" ? ` Try again in ${retry}s.` : " Try again later."}`);
      } else if (isApiError(e) && e.status === 401) {
        setError(e.message || "Invalid username or password.");
      } else {
        setError(errorMessage(e));
      }
    }
  });

  if (redirecting) {
    return (
      <div role="status" aria-live="polite" className="flex items-center gap-3 py-6 text-sm">
        <span className="size-4 animate-spin rounded-full border-2 border-primary border-t-transparent" aria-hidden="true" />
        Signed in. Opening your {target === "/dashboard" ? "dashboard" : "page"}…
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4" aria-label="Sign in">
      <div className="space-y-1.5">
        <Label htmlFor="username">Username</Label>
        <Input
          id="username"
          autoComplete="username"
          aria-invalid={!!errors.username}
          aria-describedby={errors.username ? "username-error" : undefined}
          {...register("username")}
        />
        <FieldError id="username-error" message={errors.username?.message} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          aria-invalid={!!errors.password}
          aria-describedby={errors.password ? "password-error" : undefined}
          {...register("password")}
        />
        <FieldError id="password-error" message={errors.password?.message} />
      </div>
      {error ? (
        <p role="alert" className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-sm text-loss">
          {error}
        </p>
      ) : null}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
