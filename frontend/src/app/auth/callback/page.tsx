import { Suspense } from "react";
import CallbackForm from "./callback-form";

// CallbackForm uses useSearchParams(), which Next 15 requires to be wrapped in
// a Suspense boundary during static generation/build. force-dynamic keeps this
// segment out of static prerendering entirely (defense in depth alongside Suspense).
export const dynamic = "force-dynamic";

export default function CallbackPage() {
  return (
    <main className="flex min-h-dvh items-center justify-center p-6">
      <Suspense fallback={<p className="text-muted-foreground">登录中…</p>}>
        <CallbackForm />
      </Suspense>
    </main>
  );
}
