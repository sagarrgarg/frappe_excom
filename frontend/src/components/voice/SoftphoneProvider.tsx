import { createContext, useContext, type ReactNode } from "react";
import { useSoftphone, type SoftphoneApi } from "@/hooks/useSoftphone";
import { IncomingCallToast } from "./IncomingCallToast";
import { ActiveCallBar } from "./ActiveCallBar";

/**
 * Mounted once, in AppShell, above the router.
 *
 * The registration has to survive navigation — an agent who walks from the inbox to the pipeline
 * must still be reachable — so this sits outside every route. The SDK object itself lives in
 * `lib/softphone.ts`, which is what makes that safe under StrictMode.
 */

const Ctx = createContext<SoftphoneApi | null>(null);

export function SoftphoneProvider({ children }: { children: ReactNode }) {
  const api = useSoftphone();

  return (
    <Ctx.Provider value={api}>
      {children}
      <IncomingCallToast api={api} />
      <ActiveCallBar api={api} />
    </Ctx.Provider>
  );
}

/**
 * Null when calling is not configured on this site, so a caller can render nothing rather than
 * crash. Every consumer has to handle that — most sites will not have a voice line.
 */
export function useSoftphoneContext(): SoftphoneApi | null {
  return useContext(Ctx);
}
