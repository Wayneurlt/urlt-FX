"use client";
import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CandlestickChart } from "lucide-react";
import { LoginForm } from "@/features/auth/login-form";

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();
  const prefetch = React.useCallback((to: string) => router.prefetch(to), [router]);
  return <LoginForm next={params.get("next")} onSuccess={(to) => router.replace(to)} onPrefetch={prefetch} />;
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm rounded-lg border border-border bg-surface p-6 shadow-xl">
        <div className="mb-5 flex items-center gap-2">
          <CandlestickChart className="size-5 text-primary" aria-hidden="true" />
          <h1 className="text-base font-semibold">urlt-FX operations</h1>
        </div>
        <React.Suspense fallback={null}>
          <LoginInner />
        </React.Suspense>
        <p className="mt-4 text-xs text-muted-foreground">Authorised operators only. Sessions use secure cookies.</p>
      </div>
    </main>
  );
}
