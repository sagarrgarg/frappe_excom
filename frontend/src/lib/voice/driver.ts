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
  /** The ringing call went away. `callId` names it when the vendor knows (WhatsApp does). */
  | { type: "incomingCancelled"; callId?: string | null }
  | { type: "outgoing"; callId: string | null }
  | { type: "connected"; callId: string | null }
  | { type: "ended"; reason?: string }
  | { type: "quality"; metrics: Record<string, unknown> };

export type DriverEmit = (event: DriverEvent) => void;

/**
 * Signalling the server relays to a driver whose vendor has no socket of its own — WhatsApp calls,
 * where the SDP travels over Graph API webhooks and Frappe realtime instead of a vendor SDK.
 */
export interface DriverSignal {
  type: "offer" | "answer" | "status" | "cancel" | "terminate";
  provider_call_id?: string;
  /** Excom Call name. */
  call?: string;
  sdp?: string;
  from?: string;
  status?: string;
  reason?: string;
}

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
  /** `timeout`: the pop ran out rather than the agent pressing Decline. */
  reject(callId: string | null, opts?: { timeout?: boolean }): void;
  hangup(): void;
  setMuted(muted: boolean): void;
  sendDigit(tone: string): void;

  /** Server-relayed signalling. Only drivers without their own socket implement it. */
  signal?(message: DriverSignal): void;

  /**
   * Forget an incoming call the coordinator will not show (the agent is on another call), without
   * telling the server anything. Only drivers that queue offers implement it.
   */
  dismiss?(callId: string | null): void;
}

/** Which driver serves a line, from the `sdk` name the backend's descriptor carries. */
export async function makeDriver(sdk: string, init: DriverInit): Promise<SoftphoneDriver> {
  if (sdk === "whatsapp-webrtc") {
    const { WhatsAppDriver } = await import("./whatsapp-driver");
    return new WhatsAppDriver(init);
  }
  if (sdk === "@twilio/voice-sdk") {
    const { TwilioDriver } = await import("./twilio-driver");
    return new TwilioDriver(init);
  }
  const { PlivoDriver } = await import("./plivo-driver");
  return new PlivoDriver(init);
}
