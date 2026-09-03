import { lazy, Suspense, useEffect, useState } from "react";
import { FrappeProvider } from "frappe-react-sdk";
import { SWRConfig } from "swr";
import { retryPolicy } from "./lib/retry-policy";
import { Toaster } from "sonner";
import { applyDensity, getDensity } from "./lib/ui-flag";



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
 * One tree since P2 closed: the legacy UI was deleted on 2026-09-03.
 * hooks/* and the FrappeProvider are the single data layer.
 */
function App() {
  useEffect(() => { applyDensity(getDensity()); }, []);
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
        <NextRouter />
      </Suspense>
      </SWRConfig>
    </FrappeProvider>
  );
}

export default App;
