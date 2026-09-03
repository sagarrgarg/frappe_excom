import { lazy, Suspense, useEffect, useState } from "react";
import { FrappeProvider } from "frappe-react-sdk";
import { Toaster } from "sonner";
import { resolveUiMode, applyDensity, getDensity, type UiMode } from "./lib/ui-flag";

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
      <Toaster
        richColors
        position="top-right"
        toastOptions={{ style: { background: "var(--ex-surface)", border: "1px solid var(--ex-border)", color: "var(--ex-ink-1)", boxShadow: "var(--ex-shadow)" } }}
      />
      <Suspense fallback={null}>
        {mode === "next" ? <NextRouter /> : <LegacyApp />}
      </Suspense>
    </FrappeProvider>
  );
}

export default App;
