import { getServerEnv } from "@/lib/server/env";
import { Button } from "@/components/ui/button";

// Reads server env at render time (throws if unset); force-dynamic keeps Next
// from evaluating this during static prerender at build time.
export const dynamic = "force-dynamic";

export default function LoginPage() {
  const env = getServerEnv();
  const authorize = new URL(env.dingtalkAuthorizeUrl);
  authorize.searchParams.set("redirect_uri", env.dingtalkRedirectUri);
  authorize.searchParams.set("response_type", "code");
  authorize.searchParams.set("client_id", env.dingtalkClientId);
  authorize.searchParams.set("scope", "openid");
  authorize.searchParams.set("prompt", "consent");
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 p-6">
      <h1 className="text-2xl font-semibold">智能简历筛选 · HR 工作台</h1>
      <p className="text-muted-foreground">请使用钉钉登录</p>
      <Button size="lg" render={<a href={authorize.toString()}>使用钉钉登录</a>} />
    </main>
  );
}
