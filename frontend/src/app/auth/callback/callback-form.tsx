"use client";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

export default function CallbackForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const code = params.get("code");
    if (!code) {
      setError("缺少授权码");
      return;
    }
    void fetch("/api/auth/callback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ auth_code: code }),
    }).then(async (r) => {
      if (r.ok) router.replace("/candidates");
      else {
        const body = (await r.json().catch(() => ({}))) as { message?: string };
        setError(body.message ?? "登录失败");
      }
    });
  }, [params, router]);
  return error ? <p className="text-destructive">{error}</p> : <p className="text-muted-foreground">登录中…</p>;
}
