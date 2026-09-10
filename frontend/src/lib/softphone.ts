/**
 * The softphone, as a module-level singleton.
 *
 * The SDK holds a SIP registration over a WebSocket. That registration is what makes an agent
 * reachable, so it must outlive every route change and must never exist twice. Both rules force it
 * out of React: a hook that owns it drops the registration on unmount, and StrictMode's double
 * mount would open two.
 *
 * So this module owns the object, and React subscribes to it.
 *
 * The vendor lives here and nowhere else in the frontend. Components speak `SoftphoneState` and
 * `CallState`; they never see a Plivo type.
 */

export type RegistrationState =
  | "idle"          // nothing attempted yet
  | "loading"       // fetching a token / loading the SDK
  | "registering"   // socket opening
  | "registered"    // reachable
  | "failed"        // token or login rejected
  | "unsupported";  // no WebRTC in this browser, or the network blocks it

export type CallPhase = "none" | "ringing" | "outgoing" | "connected" | "ending";

export interface CallState {
  phase: CallPhase;
  callUUID: string | null;
  direction: "Inbound" | "Outbound" | null;
  peerNumber: string;
  /** Excom's own record, once the backend has told us about it. */
  callName: string | null;
  threadId: string | null;
  displayName: string;
  muted: boolean;
  startedAt: number | null;
}

export interface SoftphoneState {
  registration: RegistrationState;
  error: string;
  micDenied: boolean;
  call: CallState;
  /** Last quality sample the SDK reported, for the "the line was terrible" conversation. */
  quality: Record<string, unknown> | null;
}

const EMPTY_CALL: CallState = {
  phase: "none",
  callUUID: null,
  direction: null,
  peerNumber: "",
  callName: null,
  threadId: null,
  displayName: "",
  muted: false,
  startedAt: null,
};

const INITIAL: SoftphoneState = {
  registration: "idle",
  error: "",
  micDenied: false,
  call: EMPTY_CALL,
  quality: null,
};

type Listener = (state: SoftphoneState) => void;

/**
 * Lifecycle tracing. On by default: a softphone that silently fails to register is the single
 * hardest thing to diagnose in this feature, and the agent reporting it cannot see server logs.
 * Turn it off with `localStorage.excom_softphone_debug = "0"`.
 */
const DEBUG = (() => {
  try {
    return localStorage.getItem("excom_softphone_debug") !== "0";
  } catch {
    return true;
  }
})();

export interface BootOptions {
  token: string;
  options: Record<string, unknown>;
  /** Called when the SDK reports an incoming call, so the app can resolve the caller. */
  onIncoming?: (callUUID: string, from: string) => void;
  /**
   * Called when an outgoing leg gets its uuid. That does not happen synchronously inside `call()`,
   * so this is the only correct moment to register the leg with the server.
   */
  onOutgoing?: (callUUID: string, to: string) => void;
  /** Called when a call ends, with the uuid, so the app can refresh the thread. */
  onEnded?: (callUUID: string | null) => void;
  /** Called with quality samples worth persisting. */
  onQuality?: (callUUID: string | null, metrics: Record<string, unknown>) => void;
}

class Softphone {
  private state: SoftphoneState = INITIAL;
  private listeners = new Set<Listener>();
  private sdk: any = null;
  private client: any = null;
  private booting: Promise<void> | null = null;
  private hooks: Omit<BootOptions, "token" | "options"> = {};

  // ── subscription ──────────────────────────────────────────────────────────

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState = (): SoftphoneState => this.state;

  private set(patch: Partial<SoftphoneState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l(this.state));
  }

  private setCall(patch: Partial<CallState>) {
    this.set({ call: { ...this.state.call, ...patch } });
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────

  get isRegistered() {
    return this.state.registration === "registered";
  }

  /** Idempotent. Calling it again while a boot is in flight joins that boot. */
  async boot({ token, options, ...hooks }: BootOptions): Promise<void> {
    this.hooks = hooks;
    if (this.client) {
      // Already up: just refresh the credential.
      return this.relogin(token);
    }
    if (this.booting) return this.booting;

    this.booting = this.doBoot(token, options).finally(() => {
      this.booting = null;
    });
    return this.booting;
  }

  private async doBoot(token: string, options: Record<string, unknown>) {
    this.set({ registration: "loading", error: "" });

    if (typeof window === "undefined" || !window.RTCPeerConnection) {
      this.set({ registration: "unsupported", error: "This browser cannot make calls." });
      return;
    }

    let Ctor: any;
    try {
      // Dynamic so the SDK is not in the main bundle: most sessions never open a softphone, and
      // the package is large.
      const mod: any = await import("plivo-browser-sdk");
      Ctor = mod?.default ?? mod?.Plivo ?? (window as any).Plivo;
    } catch {
      Ctor = (window as any).Plivo;
    }
    if (typeof Ctor !== "function") {
      this.set({ registration: "unsupported", error: "The calling library could not be loaded." });
      return;
    }

    try {
      if (DEBUG) console.info("[excom softphone] booting with options", options);
      this.sdk = new Ctor(options);
      this.client = this.sdk.client;
      if (!this.client) throw new Error("The calling library started but exposed no client.");
      this.wire();
      this.set({ registration: "registering" });
      this.login(token);
      // A registration that never completes leaves the agent thinking they are on the queue.
      // Say so instead of sitting on "registering" for ever.
      window.setTimeout(() => {
        if (this.state.registration === "registering") {
          this.set({
            registration: "failed",
            error:
              "The softphone did not connect within 20 seconds. This is usually a network blocking WebSocket or UDP traffic.",
          });
        }
      }, 20_000);
    } catch (e: any) {
      if (DEBUG) console.error("[excom softphone] boot failed", e);
      this.set({ registration: "failed", error: e?.message || "The softphone could not start." });
    }
  }

  private login(token: string) {
    const c = this.client;
    if (!c) return;
    // The docs name this differently across SDK versions and platforms, so try the token methods in
    // turn. Whichever exists is the one this build ships. Never log in with the SIP password: the
    // browser is not allowed to hold it.
    const fn =
      typeof c.loginWithAccessToken === "function"
        ? c.loginWithAccessToken
        : typeof c.loginWithJwtToken === "function"
          ? c.loginWithJwtToken
          : null;
    if (!fn) {
      this.set({
        registration: "failed",
        error: "This version of the calling library cannot use a login token.",
      });
      return;
    }
    fn.call(c, token);
  }

  /** Swap in a fresh token without dropping an active call. */
  async relogin(token: string) {
    if (!this.client) return;
    try {
      this.login(token);
    } catch {
      /* the SDK re-registers on its own timer; a failed refresh is not fatal mid-call */
    }
  }

  shutdown() {
    try {
      this.client?.logout?.();
    } catch {
      /* nothing useful to do if logout throws on teardown */
    }
    this.sdk = null;
    this.client = null;
    this.state = INITIAL;
    this.listeners.forEach((l) => l(this.state));
  }

  // ── events ────────────────────────────────────────────────────────────────

  private on(event: string, handler: (...args: any[]) => void) {
    try {
      this.client?.on?.(event, (...args: any[]) => {
        // A softphone that will not connect gives the agent nothing to report. This trace is the
        // difference between "it doesn't work" and a fixable answer, and it costs one console line
        // per lifecycle event — call events are rare, so it is not noise.
        if (DEBUG) console.info(`[excom softphone] ${event}`, ...args);
        handler(...args);
      });
    } catch {
      /* unknown events differ between SDK versions; an unwired one is not fatal */
    }
  }

  private wire() {
    this.on("onLogin", () => this.set({ registration: "registered", error: "" }));
    this.on("onLoginFailed", (reason: any) =>
      this.set({
        registration: "failed",
        error: typeof reason === "string" ? reason : "The softphone could not sign in.",
      }),
    );
    this.on("onLogout", () => this.set({ registration: "idle" }));
    this.on("onWebrtcNotSupported", () =>
      this.set({
        registration: "unsupported",
        error: "Calling in the browser is not available on this network or browser.",
      }),
    );

    this.on("onMediaPermission", (result: any) => {
      const denied = result?.status === "failure" || result?.error;
      this.set({ micDenied: Boolean(denied) });
    });

    this.on("onIncomingCall", (...args: any[]) => {
      const info = pickCallInfo(args);
      this.setCall({
        phase: "ringing",
        callUUID: info.callUUID,
        direction: "Inbound",
        peerNumber: info.from,
        displayName: info.from,
        muted: false,
        startedAt: null,
      });
      this.hooks.onIncoming?.(info.callUUID ?? "", info.from);
    });

    this.on("onIncomingCallCanceled", () => this.endLocally());

    this.on("onCalling", (...args: any[]) => {
      const info = pickCallInfo(args);
      const uuid = info.callUUID ?? this.state.call.callUUID;
      const known = this.state.call.callUUID;
      this.setCall({ phase: "outgoing", callUUID: uuid, direction: "Outbound" });
      // Fire once, when the uuid first appears. `call()` returns before the SDK has one, so this
      // is the earliest the server can be told which leg belongs to which conversation.
      if (uuid && uuid !== known) this.hooks.onOutgoing?.(uuid, this.state.call.peerNumber);
    });

    const connected = (...args: any[]) => {
      const info = pickCallInfo(args);
      this.setCall({
        phase: "connected",
        callUUID: info.callUUID ?? this.state.call.callUUID,
        startedAt: this.state.call.startedAt ?? Date.now(),
      });
    };
    this.on("onCallAnswered", connected);
    this.on("onCallConnected", connected);

    this.on("onCallTerminated", () => this.endLocally());
    this.on("onCallFailed", (reason: any) => {
      this.set({ error: typeof reason === "string" ? reason : "" });
      this.endLocally();
    });

    this.on("mediaMetrics", (metrics: any) => {
      if (!metrics || typeof metrics !== "object") return;
      this.set({ quality: metrics });
      this.hooks.onQuality?.(this.state.call.callUUID, metrics);
    });
  }

  private endLocally() {
    const uuid = this.state.call.callUUID;
    this.set({ call: EMPTY_CALL });
    this.hooks.onEnded?.(uuid);
  }

  // ── actions ───────────────────────────────────────────────────────────────

  answer() {
    const uuid = this.state.call.callUUID;
    if (!uuid) return;
    this.client?.answer?.(uuid);
  }

  reject() {
    this.client?.reject?.(this.state.call.callUUID ?? undefined);
    this.endLocally();
  }

  hangup() {
    this.setCall({ phase: "ending" });
    this.client?.hangup?.();
  }

  toggleMute(): boolean {
    const next = !this.state.call.muted;
    if (next) this.client?.mute?.();
    else this.client?.unmute?.();
    this.setCall({ muted: next });
    return next;
  }

  sendDigit(tone: string) {
    this.client?.sendDtmf?.(tone);
  }

  /** Place a call. `headers` are the SIP headers the backend told us to attach. */
  call(destination: string, headers: Record<string, string>, meta: Partial<CallState> = {}) {
    if (!this.client) throw new Error("The softphone is not connected.");
    this.setCall({
      phase: "outgoing",
      direction: "Outbound",
      peerNumber: destination,
      displayName: meta.displayName || destination,
      threadId: meta.threadId ?? null,
      callName: null,
      muted: false,
      startedAt: null,
    });
    this.client.call(destination, headers);
  }

  /** Bind Excom's own call record to the leg the SDK is holding. */
  attachRecord(callName: string, threadId?: string | null) {
    this.setCall({ callName, threadId: threadId ?? this.state.call.threadId });
  }

  setPeer(displayName: string) {
    this.setCall({ displayName });
  }
}

/**
 * Extract a call uuid and a peer number from an SDK callback, whose argument shape varies by
 * version. Reading positionally would break on an upgrade; reading by name does not.
 */
function pickCallInfo(args: any[]): { callUUID: string | null; from: string } {
  let callUUID: string | null = null;
  let from = "";
  for (const arg of args) {
    if (typeof arg === "string" && !from) from = arg;
    if (arg && typeof arg === "object") {
      callUUID = callUUID ?? arg.callUUID ?? arg.callUuid ?? arg.callId ?? null;
      from = from || arg.src || arg.from || arg.callerName || "";
    }
  }
  return { callUUID, from };
}

export const softphone = new Softphone();

// A handle an agent can read in the console when the softphone will not connect:
//   __excomSoftphone.state()   -> registration, error, mic permission, current call
// Read-only, and it exposes nothing the page does not already hold.
if (typeof window !== "undefined") {
  (window as any).__excomSoftphone = {
    state: () => softphone.getState(),
    registered: () => softphone.isRegistered,
  };
}
