import type { DriverEmit, DriverInit, SoftphoneDriver } from "./driver";

/**
 * Twilio's Voice SDK, behind the driver interface.
 *
 * Shorter than the Plivo driver, and the differences are all in our favour:
 *
 * * **Not a singleton.** Several `Device` objects may live on one page, so `exclusive` is false and
 *   this line can stay registered while the Indian Plivo line is too. That is what ends the "one
 *   desk at a time" limitation for an agent who works both.
 * * **No vendor ringtone to silence.** Twilio does not play one, so Excom's own ring is the only
 *   one and there is nothing to fight.
 * * **The call object is handed to us**, rather than being addressed by id. `answer`, `hangup` and
 *   the digits all go to the call we are holding, so the id arguments are accepted and ignored.
 */
export class TwilioDriver implements SoftphoneDriver {
  readonly vendor = "twilio";
  readonly exclusive = false;

  private device: any = null;
  private active: any = null;
  private readonly options: Record<string, unknown>;
  private readonly emit: DriverEmit;

  constructor({ options, emit }: DriverInit) {
    this.options = options;
    this.emit = emit;
  }

  async start(token: string): Promise<void> {
    if (typeof window === "undefined" || !window.RTCPeerConnection) {
      throw new Error("This browser cannot make calls.");
    }

    let Device: any;
    try {
      // Dynamic, like the Plivo side: most sessions never open a softphone.
      const mod: any = await import("@twilio/voice-sdk");
      Device = mod?.Device ?? mod?.default?.Device;
    } catch {
      Device = (window as any).Twilio?.Device;
    }
    if (typeof Device !== "function") {
      throw new Error("The calling library could not be loaded.");
    }

    this.device = new Device(token, this.options);
    this.wire();
    // `register()` is what opens the signalling socket and makes the identity reachable. Without
    // it the device can place calls but never receives one.
    await this.device.register();
  }

  refresh(token: string): void {
    try {
      this.device?.updateToken?.(token);
    } catch {
      /* the device keeps its old token until expiry; a failed refresh is not fatal mid-call */
    }
  }

  destroy(): void {
    try {
      this.active?.disconnect?.();
    } catch {
      /* already gone */
    }
    try {
      this.device?.destroy?.();
    } catch {
      /* nothing useful to do on teardown */
    }
    this.active = null;
    this.device = null;
  }

  call(destination: string, params: Record<string, string>): void {
    if (!this.device) throw new Error("The softphone is not connected.");
    // Twilio has no SIP headers on a browser leg. Everything the answer URL needs travels as
    // ordinary parameters instead, and `To` is the one its TwiML App reads to know the
    // destination. The whole set is capped at 800 bytes by the SDK.
    const connectParams: Record<string, string> = { To: destination, ...params };
    // `connect` resolves with the Call once the SDK has one; the events below do the rest.
    Promise.resolve(this.device.connect({ params: connectParams }))
      .then((call: any) => this.adopt(call, "outgoing"))
      .catch((err: any) =>
        this.emit({ type: "ended", reason: err?.message || "The call could not be placed." }),
      );
  }

  answer(): void {
    try {
      this.active?.accept?.();
    } catch {
      /* the call went away between the pop and the click */
    }
  }

  reject(): void {
    try {
      this.active?.reject?.();
    } catch {
      /* already gone */
    }
    this.active = null;
  }

  hangup(): void {
    try {
      this.active?.disconnect?.();
    } catch {
      /* already gone */
    }
  }

  setMuted(muted: boolean): void {
    try {
      this.active?.mute?.(muted);
    } catch {
      /* no call to mute */
    }
  }

  sendDigit(tone: string): void {
    try {
      this.active?.sendDigits?.(tone);
    } catch {
      /* no call to send into */
    }
  }

  // ── wiring ────────────────────────────────────────────────────────────────

  private wire() {
    const d = this.device;
    if (!d) return;

    d.on("registered", () => this.emit({ type: "registered" }));
    d.on("unregistered", () => this.emit({ type: "unregistered" }));

    d.on("error", (error: any) => {
      const code = Number(error?.code || 0);
      const message = error?.message || "The softphone reported an error.";
      // 31401 is the microphone being refused, which is a permission problem rather than a
      // registration one — telling the agent their line is down would send them to the wrong fix.
      if (code === 31401) {
        this.emit({ type: "micDenied", denied: true });
        return;
      }
      // 31204/31205 are token problems: expired, or signed by the wrong key.
      this.emit({ type: "registrationFailed", reason: message });
    });

    d.on("incoming", (call: any) => {
      this.adopt(call, "incoming");
      const from =
        call?.parameters?.From || call?.customParameters?.get?.("From") || "";
      this.emit({ type: "incoming", callId: call?.parameters?.CallSid || null, from });
    });

    // Refreshing before expiry is what keeps an agent reachable through a shift. The coordinator
    // already re-mints on a timer; this is the SDK's own warning, and asking for one now is
    // cheaper than discovering the token lapsed when a call arrives.
    d.on("tokenWillExpire", () => this.emit({ type: "registrationFailed", reason: "token-expiring" }));
  }

  /** Take ownership of a Call object and follow it to the end. */
  private adopt(call: any, kind: "incoming" | "outgoing") {
    if (!call) return;
    this.active = call;
    const id = () => call?.parameters?.CallSid || null;

    if (kind === "outgoing") this.emit({ type: "outgoing", callId: id() });

    call.on("accept", () => this.emit({ type: "connected", callId: id() }));
    call.on("cancel", () => {
      this.active = null;
      this.emit({ type: "incomingCancelled" });
    });
    call.on("disconnect", () => {
      this.active = null;
      this.emit({ type: "ended" });
    });
    call.on("reject", () => {
      this.active = null;
      this.emit({ type: "ended" });
    });
    call.on("error", (err: any) => {
      this.active = null;
      this.emit({ type: "ended", reason: err?.message || "" });
    });
    call.on("sample", (sample: any) => {
      if (sample && typeof sample === "object") this.emit({ type: "quality", metrics: sample });
    });
  }
}
