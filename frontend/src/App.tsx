import { lazy, Suspense, useEffect, useState } from "react";
import { FrappeProvider } from "frappe-react-sdk";
import { SWRConfig } from "swr";
import { Toaster } from "sonner";
import { resolveUiMode, applyDensity, getDensity, type UiMode } from "./lib/ui-flag";

/**
 * Retry policy for every SWR fetch: a 429 waits 20s (retrying sooner just extends the block),
 * other 4xx never retry, 5xx/network back off 5s → 10s → 20s and stop after three tries.
 */
function retryPolicy(err: { httpStatus?: number } | undefined, _key: string, _cfg: unknown, revalidate: (o: { retryCount: number }) => void, { retryCount }: { retryCount: number }) {
  const st = err?.httpStatus;
  if (st === 429) { setTimeout(() => revalidate({ retryCount }), 20_000); return; }
  if (st && st >= 400 && st < 500) return;
  if (retryCount >= 3) return;
  setTimeout(() => revalidate({ retryCount }), 5_000 * 2 ** retryCount);
}


const LegacyApp = lazy(() => import("./LegacyApp").then((m) => ({ default: m.LegacyApp })));
const NextRouter = lazy(() => import("./routes").then((m) => ({ default: m.NextRouter })));

const getSiteName = (): string => {
  // Priority: frappe boot → env var → current hostname
  const fromBoot = (window as any).frappe?.boot?.sitename;
  if (fromBoot) return fromBoot;

  const fromEnv = import.meta.env.VITE_SITE_NAME;
  if (fromEnv) return fromEnv;

  return window.location.hostname;
};

const getSocketPort = (): string | undefined => {
  const fromEnv = import.meta.env.VITE_SOCKET_PORT;
  if (fromEnv) return String(fromEnv);

  const fromBoot = (window as any).frappe?.boot?.socketio_port;
  if (fromBoot != null) return String(fromBoot);

  return undefined;
};


/**
 * One bundle, two trees (UX-001 §10.2). `?ui=next|legacy` → localStorage → per-user default → legacy.
 * Both trees share hooks/* and the FrappeProvider; there is one data layer.
 */
function App() {
  const [mode] = useState<UiMode>(resolveUiMode);
  useEffect(() => { if (mode === "next") applyDensity(getDensity()); }, [mode]);
  return (
    <FrappeProvider
      url={import.meta.env.VITE_FRAPPE_PATH ?? ""}
      socketPort={getSocketPort()}
      siteName={getSiteName()}
    >
      <SWRConfig value={{ onErrorRetry: retryPolicy, focusThrottleInterval: 15_000 }}>
      <Toaster
        richColors
        position="top-right"
        toastOptions={{ style: { background: "var(--ex-surface)", border: "1px solid var(--ex-border)", color: "var(--ex-ink-1)", boxShadow: "var(--ex-shadow)" } }}
      />
      <Suspense fallback={null}>
        {mode === "next" ? <NextRouter /> : <LegacyApp />}
      </Suspense>
      </SWRConfig>
    </FrappeProvider>
  );
}

export default App;
