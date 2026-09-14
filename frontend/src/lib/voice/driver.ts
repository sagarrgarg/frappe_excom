/**
 * What the softphone needs from a vendor, and nothing more.
 *
 * Two providers now carry calls — Plivo for India, Twilio for everywhere else — and they disagree
 * about almost everything except the shape of a phone call. This is the seam: the coordinator in
 * `lib/softphone.ts` owns registration state, the call, and the listeners React subscribes to; a
 * driver owns one vendor SDK and translates it.
 *
 * The one asymmetry worth naming in the type is `exclusive`. Plivo's SDK keeps a single client on
 * `window._PlivoInstance` and hands it back to every later construction, so two Plivo lines cannot
 * be registered at once — the second replaces the first. Twilio's `Device` has no such rule. So
 * whether a line can sit alongside another is a property of the vendor, not of our design, and the
 * coordinator has to ask rather than assume.
 */

export type DriverEvent =
  | { type: "registered" }
  | { type: "registrationFailed"; reason: string }
  | { type: "unregistered" }
  | { type: "unsupported"; reason: string }
  | { type: "micDenied"; denied: boolean }
  | { type: "incoming"; callId: string | null; from: string }
  | { type: "incomingCancelled" }
  | { type: "outgoing"; callId: string | null }
  | { type: "connected"; callId: string | null }
  | { type: "ended"; reason?: string }
  | { type: "quality"; metrics: Record<string, unknown> };

export type DriverEmit = (event: DriverEvent) => void;

export interface DriverInit {
  /** Vendor options minted server-side by `sdk_descriptor()`. Never a secret. */
  options: Record<string, unknown>;
  /** Where the driver reports everything back to. */
  emit: DriverEmit;
}

export interface SoftphoneDriver {
  /** For tracing, and for the console handle. */
  readonly vendor: string;

  /**
   * True when this vendor allows only one live driver *of its own kind* on the page.
   *
   * It is not a claim about other vendors. Plivo's singleton stops a second Plivo line, and says
   * nothing about a Twilio one running alongside — which is exactly the pairing we have, so an
   * agent on the Indian and the international desk is reachable on both at once.
   */
  readonly exclusive: boolean;

  /** Load the SDK and sign in. Rejects if the vendor cannot run in this browser. */
  start(token: string): Promise<void>;

  /** Swap in a fresh token without dropping a call in progress. */
  refresh(token: string): void;

  /** Sign out and release everything, so another driver may be constructed. */
  destroy(): void;

  /** Place a call. `params` are the vendor-shaped extras the backend handed us. */
  call(destination: string, params: Record<string, string>): void;

  answer(callId: string | null): void;
  reject(callId: string | null): void;
  hangup(): void;
  setMuted(muted: boolean): void;
  sendDigit(tone: string): void;
}

/** Which driver serves a line, from the `sdk` name the backend's descriptor carries. */
export async function makeDriver(sdk: string, init: DriverInit): Promise<SoftphoneDriver> {
  if (sdk === "@twilio/voice-sdk") {
    const { TwilioDriver } = await import("./twilio-driver");
    return new TwilioDriver(init);
  }
  const { PlivoDriver } = await import("./plivo-driver");
  return new PlivoDriver(init);
}
