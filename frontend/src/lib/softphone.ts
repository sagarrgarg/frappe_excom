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
  /** Only ever "Browser" here — a call on the agent's handset never reaches the SDK. */
  transport?: "Browser" | "Phone";
  /** Which line is carrying this call. Every control has to act on that line's own client. */
  account: string | null;
}

/** One line's own registration, so the UI can say which desk is down rather than "the softphone". */
export interface LineState {
  account: string;
  registration: RegistrationState;
  error: string;
}

export interface SoftphoneState {
  registration: RegistrationState;
  /**
   * The line the browser is currently signed in to. Exactly one, ever: the Plivo SDK keeps a
   * single client on `window._PlivoInstance` and hands it back to every later construction, so a
   * second line cannot be registered alongside the first — it replaces it.
   */
  activeAccount: string | null;
  error: string;
  micDenied: boolean;
  call: CallState;
  /** Last quality sample the SDK reported, for the "the line was terrible" conversation. */
  quality: Record<string, unknown> | null;
  /** Per line, keyed by account name. */
  lines: Record<string, LineState>;
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
  account: null,
};

const INITIAL: SoftphoneState = {
  registration: "idle",
  error: "",
  micDenied: false,
  call: EMPTY_CALL,
  quality: null,
  lines: {},
  activeAccount: null,
};

/** Worst to best. The agent is as reachable as their best line. */
const REGISTRATION_RANK: RegistrationState[] = [
  "unsupported",
  "failed",
  "idle",
  "loading",
  "registering",
  "registered",
];

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
  /** The line this client serves. Boot once per line the agent works. */
  account: string;
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
  /** The one client the SDK will give us, whichever line it is currently signed in to. */
  private sdk: any = null;
  private client: any = null;
  private booting: Promise<void> | null = null;
  private hooks: Omit<BootOptions, "token" | "options" | "account"> = {};
  /** One record per dial the agent asked for, however many times the SDK reports progress. */
  private outgoingReported = false;

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

  /** Record one line's registration and recompute the aggregate the UI reads. */
  private setLine(account: string, patch: Partial<Omit<LineState, "account">>) {
    const previous = this.state.lines[account] ?? {
      account,
      registration: "idle" as RegistrationState,
      error: "",
    };
    const lines = { ...this.state.lines, [account]: { ...previous, ...patch, account } };
    const best = Object.values(lines).reduce<RegistrationState>(
      (winner, line) =>
        REGISTRATION_RANK.indexOf(line.registration) > REGISTRATION_RANK.indexOf(winner)
          ? line.registration
          : winner,
      "idle",
    );
    // Only surface an error while nothing is working. One failed line out of two is worth showing
    // on that line, not as "the softphone is broken".
    const broken = Object.values(lines).filter((l) => l.error);
    this.set({
      lines,
      registration: best,
      error: best === "registered" ? "" : broken[0]?.error || "",
    });
  }

  /** Is the browser signed in to this particular line right now? */
  isRegisteredOn(account: string): boolean {
    return (
      this.state.activeAccount === account && this.state.registration === "registered"
    );
  }

  /**
   * Resolves once this line is actually registered, or false if it does not get there in time.
   *
   * Switching line is not instant — the client signs out, signs back in and re-registers — and
   * dialling into that gap fails with no useful error. Anything that switches in order to place a
   * call has to wait here first.
   */
  waitUntilRegistered(account: string, timeoutMs = 12_000): Promise<boolean> {
    if (this.isRegisteredOn(account)) return Promise.resolve(true);
    return new Promise((resolve) => {
      let done = false;
      const finish = (ok: boolean) => {
        if (done) return;
        done = true;
        unsubscribe();
        window.clearTimeout(timer);
        resolve(ok);
      };
      const timer = window.setTimeout(() => finish(false), timeoutMs);
      const unsubscribe = this.subscribe((state) => {
        if (state.activeAccount !== account) return;
        if (state.registration === "registered") finish(true);
        if (state.registration === "failed" || state.registration === "unsupported") finish(false);
      });
    });
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────

  get isRegistered() {
    return this.state.registration === "registered";
  }

  /**
   * Sign the browser in to a line.
   *
   * Calling it for a second line is a *switch*, not an addition: the SDK's single client is signed
   * out of the first and in to the second. That is forced on us — `new Plivo()` returns the
   * existing `window._PlivoInstance` and discards the options — and pretending otherwise put a
   * call to an Indian number out over the American account.
   */
  async boot({ account, token, options, ...hooks }: BootOptions): Promise<void> {
    this.hooks = hooks;

    if (this.client && this.state.activeAccount === account) {
      // Already signed in to this line: just refresh the credential.
      return this.relogin(account, token);
    }
    if (this.client) {
      // A different line. The client cannot be handed over, so it is replaced.
      if (this.state.call.phase !== "none") {
        throw new Error("Finish the current call before switching line.");
      }
      return this.switchTo(account, token, options);
    }
    if (this.booting) return this.booting;

    this.booting = this.doBoot(account, token, options).finally(() => {
      this.booting = null;
    });
    return this.booting;
  }

  /**
   * Move to another line by throwing the client away and building a new one.
   *
   * Logging the same client out and back in as a different Plivo account does not work: it
   * re-registers happily and then fails the first INVITE with `Authentication Error`, because the
   * credentials it answers the auth challenge with belong to the account it used to be. It also
   * keeps the options it was constructed with, so the American line would go on using the Indian
   * line's `clientRegion`.
   *
   * `new Plivo()` returns `window._PlivoInstance` whenever that exists, so clearing it is what
   * makes a second construction real.
   */
  private async switchTo(
    account: string,
    token: string,
    options: Record<string, unknown>,
  ): Promise<void> {
    const previous = this.state.activeAccount;
    if (previous) this.setLine(previous, { registration: "idle", error: "" });

    try {
      this.client?.logout?.();
    } catch {
      /* a client that will not log out cleanly is about to be discarded anyway */
    }
    teardownVendorAudio();
    try {
      delete (window as any)._PlivoInstance;
    } catch {
      /* if the global cannot be cleared the SDK hands back the old client and login still runs */
    }

    this.sdk = null;
    this.client = null;
    this.booting = null;
    this.set({ activeAccount: null });

    return this.doBoot(account, token, options);
  }

  private async doBoot(account: string, token: string, options: Record<string, unknown>) {
    this.set({ activeAccount: account });
    this.setLine(account, { registration: "loading", error: "" });

    if (typeof window === "undefined" || !window.RTCPeerConnection) {
      this.setLine(account, {
        registration: "unsupported",
        error: "This browser cannot make calls.",
      });
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
      this.setLine(account, {
        registration: "unsupported",
        error: "The calling library could not be loaded.",
      });
      return;
    }

    try {
      if (DEBUG) console.info(`[excom softphone] booting ${account} with options`, options);
      this.sdk = new Ctor(options);
      this.client = this.sdk.client;
      if (!this.client) throw new Error("The calling library started but exposed no client.");

      // Wired per client, and the handlers still read the active line from state rather than
      // closing over it: a switch replaces the client but the events it fires on the way out
      // belong to the line being left.
      this.wire();
      silenceVendorRingtone();
      this.setLine(account, { registration: "registering" });
      this.login(account, token);
      // A registration that never completes leaves the agent thinking they are on the queue.
      // Say so instead of sitting on "registering" for ever.
      window.setTimeout(() => {
        if (this.state.lines[account]?.registration === "registering") {
          this.setLine(account, {
            registration: "failed",
            error:
              "The softphone did not connect within 20 seconds. This is usually a network blocking WebSocket or UDP traffic.",
          });
        }
      }, 20_000);
    } catch (e: any) {
      if (DEBUG) console.error(`[excom softphone] boot failed for ${account}`, e);
      this.setLine(account, {
        registration: "failed",
        error: e?.message || "The softphone could not start.",
      });
    }
  }

  private login(account: string, token: string) {
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
      this.setLine(account, {
        registration: "failed",
        error: "This version of the calling library cannot use a login token.",
      });
      return;
    }
    fn.call(c, token);
  }

  /** Swap in a fresh token without dropping an active call. */
  async relogin(account: string, token: string) {
    if (!this.client) return;
    try {
      this.login(account, token);
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
        if (DEBUG) {
          console.info(`[excom softphone] ${this.state.activeAccount ?? "?"} ${event}`, ...args);
        }
        handler(...args);
      });
    } catch {
      /* unknown events differ between SDK versions; an unwired one is not fatal */
    }
  }

  /**
   * Wire this client's events.
   *
   * Every handler asks state for the active line rather than closing over one — a handler that
   * captured the line it was wired for would keep naming the line the agent has already left.
   * Wiring belongs to the client, not the page: stacking handlers on one shared emitter is what
   * made every event appear twice when two lines were booted together.
   */
  private wire() {
    const on = (event: string, handler: (...args: any[]) => void) => this.on(event, handler);
    const active = () => this.state.activeAccount ?? "";

    on("onLogin", () => this.setLine(active(), { registration: "registered", error: "" }));
    on("onLoginFailed", (reason: any) =>
      this.setLine(active(), {
        registration: "failed",
        error: typeof reason === "string" ? reason : "The softphone could not sign in.",
      }),
    );
    on("onLogout", () => this.setLine(active(), { registration: "idle" }));
    on("onWebrtcNotSupported", () =>
      this.setLine(active(), {
        registration: "unsupported",
        error: "Calling in the browser is not available on this network or browser.",
      }),
    );

    on("onMediaPermission", (result: any) => {
      const denied = result?.status === "failure" || result?.error;
      this.set({ micDenied: Boolean(denied) });
    });

    on("onIncomingCall", (...args: any[]) => {
      // Again here, not only at boot: the SDK builds its audio elements lazily, so the one at boot
      // may have found nothing to silence.
      silenceVendorRingtone();
      const info = pickCallInfo(args);
      this.setCall({
        phase: "ringing",
        callUUID: info.callUUID,
        direction: "Inbound",
        peerNumber: info.from,
        displayName: info.from,
        muted: false,
        startedAt: null,
        account: active(),
      });
      this.hooks.onIncoming?.(info.callUUID ?? "", info.from);
    });

    on("onIncomingCallCanceled", () => this.endLocally());

    on("onCalling", (...args: any[]) => {
      const info = pickCallInfo(args);
      const uuid = info.callUUID ?? this.state.call.callUUID;
      this.setCall({ phase: "outgoing", callUUID: uuid, direction: "Outbound", account: active() });
      // Once per dial the agent actually asked for, not once per event.
      //
      // The SDK re-emits onCalling while it retries the INVITE, with a fresh callUUID each time.
      // Reporting every one of those turned a single click into ten "Outgoing call" rows in the
      // timeline within four seconds. What the agent did was place one call; that is what gets
      // recorded, and the webhooks fill in what became of it.
      if (uuid && !this.outgoingReported) {
        this.outgoingReported = true;
        this.hooks.onOutgoing?.(uuid, this.state.call.peerNumber);
      }
    });

    const connected = (...args: any[]) => {
      const info = pickCallInfo(args);
      this.setCall({
        phase: "connected",
        callUUID: info.callUUID ?? this.state.call.callUUID,
        startedAt: this.state.call.startedAt ?? Date.now(),
      });
    };
    on("onCallAnswered", connected);
    on("onCallConnected", connected);

    on("onCallTerminated", () => this.endLocally());
    on("onCallFailed", (reason: any) => {
      this.set({ error: typeof reason === "string" ? reason : "" });
      this.endLocally();
    });

    on("mediaMetrics", (metrics: any) => {
      if (!metrics || typeof metrics !== "object") return;
      this.set({ quality: metrics });
      this.hooks.onQuality?.(this.state.call.callUUID, metrics);
    });
  }

  private endLocally() {
    const uuid = this.state.call.callUUID;
    this.outgoingReported = false;
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

  /**
   * Place a call on a named line. `headers` are the SIP headers the backend told us to attach.
   *
   * The account is not optional in spirit: the server decides which line carries a destination —
   * an Indian number on the Indian line, an American one on the American line — and dialling the
   * American number through the Indian client would have the carrier bar it.
   */
  call(
    destination: string,
    headers: Record<string, string>,
    meta: Partial<CallState> & { account: string },
  ) {
    if (!this.client) {
      throw new Error("The softphone is not connected.");
    }
    // The guard that the logs earned. The one client is signed in to one line; dialling while it
    // is signed in to another sends the call out over the wrong provider account, which fails as a
    // bare "Busy" with nothing to say the number was never the problem.
    if (this.state.activeAccount !== meta.account) {
      throw new Error("The softphone is signed in to a different line.");
    }
    if (this.state.call.phase !== "none") {
      throw new Error("You are already on a call.");
    }
    this.outgoingReported = false;
    this.setCall({
      phase: "outgoing",
      direction: "Outbound",
      transport: "Browser",
      peerNumber: destination,
      displayName: meta.displayName || destination,
      threadId: meta.threadId ?? null,
      callName: null,
      muted: false,
      startedAt: null,
      account: meta.account,
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
 * Remove the audio elements the SDK appends to the body.
 *
 * Its setup appends a handful of `<audio>` tags with fixed ids — ringtone, ringback, connect tone,
 * the remote stream — and building a second client appends another set. Duplicate ids mean
 * `getElementById` picks whichever came first, so the tone we silenced is not the one that plays.
 * The SDK tags each with `data-devicetype`, which is enough to find them all.
 */
function teardownVendorAudio(): void {
  try {
    for (const el of document.querySelectorAll("audio[data-devicetype]")) el.remove();
  } catch {
    /* a stale element is noisy, never fatal */
  }
}

/**
 * Take the incoming ring away from the SDK.
 *
 * The SDK appends `<audio id="plivo_ringtone" loop src="https://cdn.plivo.com/...">` to the body
 * and plays it on every incoming call. We ring from `lib/ringtone.ts` instead — locally generated,
 * so no CDN has to answer at the moment the phone rings — and two ringers at once is worse than
 * either. Dropping the source leaves the element in place, so the SDK's own `getElementById` still
 * finds something and its `play()` simply resolves to nothing.
 *
 * The ringback on outbound calls is left alone: by then the agent has clicked Call, so the gesture
 * exists and the vendor's own tone is fine.
 */
function silenceVendorRingtone(): void {
  try {
    const el = document.getElementById("plivo_ringtone") as HTMLAudioElement | null;
    if (!el || !el.getAttribute("src")) return;
    el.pause();
    el.removeAttribute("src");
    el.load();
    if (DEBUG) console.info("[excom softphone] vendor ringtone silenced; Excom rings instead");
  } catch {
    /* worst case the vendor tone plays too — noisy, never broken */
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
    lines: () => softphone.getState().lines,
    activeLine: () => softphone.getState().activeAccount,
    registeredOn: (account: string) => softphone.isRegisteredOn(account),
  };
}
