import type { DriverEmit, DriverInit, DriverSignal, SoftphoneDriver } from "./driver";
import { errorSummary } from "../errors";
import { finalSdp, normalizeIceServers, waitForIceGathering } from "./webrtc";

/**
 * WhatsApp Business Calling, behind the driver interface.
 *
 * There is no vendor SDK. The browser speaks WebRTC straight to Meta's relay and the session
 * descriptions travel through Excom: Meta's offer arrives by realtime (`signal({type: "offer"})`),
 * our answer goes back over `api.whatsapp_calling.answer`, which sends `pre_accept` and `accept`.
 * For a call the agent places, the offer goes out over `start_call` and Meta's answer comes back
 * by `signal({type: "answer"})`.
 *
 * Meta is ICE-lite, takes no trickle ICE and never renegotiates, and supplies no STUN/TURN. So
 * every description is sent whole (gathering complete), and the ICE servers come from Excom.
 *
 * WhatsApp counts the first media packet as "accepted", so the microphone track is not on the
 * sender (`replaceTrack(null)`) until Meta has accepted: our accept for an incoming call, the
 * ACCEPTED status for one we placed. Swapping the track in needs no renegotiation.
 *
 * Every call has its own state object (`Leg`). Each step re-checks after its `await` that the leg
 * is still the current one: a hang-up during the microphone prompt or the ICE wait must not post
 * an answer or place the call, and whatever was opened for an abandoned leg is closed.
 *
 * `call(destination, params)` takes the conversation in `params.thread`; `destination` is only the
 * name shown. Registration means "this browser can do WebRTC"; the heartbeat that puts the agent in
 * the ring set is the coordinator's, as for every line.
 */

const API = "excom.excom.api.whatsapp_calling";

interface Pending {
  callId: string;
  call: string;
  sdp: string;
  from: string;
}

/** preparing: nothing sent yet · posting: answer / start_call in flight · posted: Meta has it. */
type Stage = "preparing" | "posting" | "posted";

interface Leg {
  token: number;
  callId: string;
  call: string;
  direction: "in" | "out";
  stage: Stage;
  connected: boolean;
  /** Set before the async apply, so a second answer (signal and start_call response) is ignored. */
  answerApplied: boolean;
  pc: RTCPeerConnection | null;
  stream: MediaStream | null;
  track: MediaStreamTrack | null;
  sender: RTCRtpSender | null;
}

function env(): any {
  return globalThis as any;
}

async function post<T>(method: string, args: Record<string, unknown>): Promise<T> {
  const res = await env().fetch(`/api/method/${API}.${method}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Frappe-CSRF-Token": env().csrf_token || "",
    },
    body: JSON.stringify(args),
  });
  const body: any = await res.json().catch(() => ({}));
  if (!res.ok) throw { httpStatus: res.status, ...body };
  return body?.message as T;
}

/** Fire and forget; the server settles what this could not. */
function tell(method: string, args: Record<string, unknown>) {
  post(method, args).catch(() => {
    /* the person may have hung up first; the terminate webhook settles it */
  });
}

/** For a page that is closing: a beacon carries the CSRF token in the form, as Frappe accepts. */
function beacon(method: string, args: Record<string, string>) {
  const url = `/api/method/${API}.${method}`;
  try {
    const form = new FormData();
    for (const [k, v] of Object.entries(args)) form.append(k, v);
    form.append("csrf_token", env().csrf_token || "");
    if (env().navigator?.sendBeacon?.(url, form)) return;
  } catch {
    /* fall through to keepalive */
  }
  try {
    void env().fetch(url, {
      method: "POST",
      keepalive: true,
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": env().csrf_token || "" },
      body: JSON.stringify(args),
    });
  } catch {
    /* the TTL and Meta's own timeout end it */
  }
}

function reasonOf(e: unknown, fallback: string): string {
  if (e instanceof Error) return e.message || fallback;
  return errorSummary(e as any, fallback);
}

export class WhatsAppDriver implements SoftphoneDriver {
  readonly vendor = "whatsapp";
  readonly exclusive = false;

  private readonly emit: DriverEmit;
  private readonly iceServers: RTCIceServer[];
  private audio: HTMLAudioElement | null = null;
  /** Offers the coordinator is showing, by Meta's call id. */
  private pending = new Map<string, Pending>();
  private current: Leg | null = null;
  private seq = 0;
  private muted = false;
  private readonly onPageHide = () => this.pageClosing();

  constructor({ options, emit }: DriverInit) {
    this.emit = emit;
    this.iceServers = normalizeIceServers(options?.iceServers) as RTCIceServer[];
  }

  async start(): Promise<void> {
    if (!env().RTCPeerConnection || !env().navigator?.mediaDevices?.getUserMedia) {
      throw new Error("This browser cannot take WhatsApp calls.");
    }
    env().addEventListener?.("pagehide", this.onPageHide);
    this.emit({ type: "registered" });
  }

  refresh(): void {
    /* nothing to refresh: the Frappe session is the credential */
  }

  destroy(): void {
    env().removeEventListener?.("pagehide", this.onPageHide);
    const leg = this.current;
    if (leg) this.abandon(leg, true);
    this.pending.clear();
  }

  // ── server signalling ───────────────────────────────────────────────────

  signal(message: DriverSignal): void {
    const id = message.provider_call_id || "";
    const leg = this.current;
    switch (message.type) {
      case "offer":
        // Already on a call: stay silent; the call rings the agent's colleagues.
        if (leg || !message.sdp || !message.call || !id) return;
        if (this.pending.has(id)) return; // a repeated pop (realtime and the reload replay)
        this.pending.set(id, { callId: id, call: message.call, sdp: message.sdp, from: message.from || "" });
        this.emit({ type: "incoming", callId: id, from: message.from || "" });
        return;
      case "cancel":
        // Answered elsewhere. A leg still preparing its own answer gives up; one whose answer is
        // already with the server (this tab answered) ignores it.
        if (this.dropPending(id)) return;
        if (leg && leg.callId === id && leg.direction === "in" && leg.stage === "preparing") {
          this.finish(leg, "A colleague answered this call.", false);
        }
        return;
      case "answer":
        if (leg?.direction === "out" && message.sdp && (!leg.callId || leg.callId === id)) {
          void this.applyAnswer(leg, message.sdp);
        }
        return;
      case "status":
        // Only this agent's own outbound call gets statuses; one that beats start_call's response
        // names the call before we know its id.
        if (!leg || leg.direction !== "out" || (leg.callId && leg.callId !== id)) return;
        if (!leg.callId) leg.callId = id;
        if (message.status === "ACCEPTED") void this.markConnected(leg);
        if (message.status === "REJECTED") this.finish(leg, "They declined the call.", false);
        return;
      case "terminate":
        if (this.dropPending(id)) return;
        // Meta has ended it already: nothing to post back.
        if (leg && (leg.callId === id || (!leg.callId && leg.direction === "out" && leg.stage === "posting"))) {
          this.finish(leg, message.reason || undefined, false);
        }
        return;
    }
  }

  dismiss(callId: string | null): void {
    if (callId) this.pending.delete(callId);
  }

  // ── calls ───────────────────────────────────────────────────────────────

  call(_destination: string, params: Record<string, string>): void {
    void this.placeCall(params.thread || "");
  }

  answer(callId: string | null): void {
    void this.acceptCall(callId);
  }

  reject(callId: string | null, opts?: { timeout?: boolean }): void {
    const p = this.takePending(callId);
    if (p) tell("decline", { call: p.call, timeout: opts?.timeout ? 1 : 0 });
  }

  hangup(): void {
    const leg = this.current;
    if (leg) this.abandon(leg, true);
    this.emit({ type: "ended" });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    const leg = this.current;
    if (leg?.connected && leg.track) leg.track.enabled = !muted;
  }

  sendDigit(tone: string): void {
    try {
      this.current?.sender?.dtmf?.insertDTMF(tone);
    } catch {
      /* DTMF is in-band only (RFC 4733); a peer that did not negotiate it ignores the tone */
    }
  }

  // ── internals ───────────────────────────────────────────────────────────

  private takePending(callId: string | null): Pending | null {
    const p = (callId && this.pending.get(callId)) || (!callId && this.pending.size === 1 ? [...this.pending.values()][0] : null);
    if (p) this.pending.delete(p.callId);
    return p || null;
  }

  private dropPending(id: string): boolean {
    if (!id || !this.pending.delete(id)) return false;
    this.emit({ type: "incomingCancelled", callId: id });
    return true;
  }

  private begin(direction: "in" | "out", callId = "", call = ""): Leg {
    const leg: Leg = {
      token: ++this.seq,
      callId,
      call,
      direction,
      stage: "preparing",
      connected: false,
      answerApplied: false,
      pc: null,
      stream: null,
      track: null,
      sender: null,
    };
    this.current = leg;
    return leg;
  }

  /** Is this leg still the call in hand? Checked after every await. */
  private alive(leg: Leg): boolean {
    return this.current?.token === leg.token;
  }

  private async placeCall(thread: string) {
    if (!thread) {
      this.emit({ type: "ended", reason: "Open the WhatsApp conversation to call this person." });
      return;
    }
    if (this.current) {
      this.emit({ type: "ended", reason: "You are already on a WhatsApp call." });
      return;
    }
    const leg = this.begin("out");
    try {
      const pc = await this.openPeer(leg);
      if (!this.alive(leg)) return;
      await pc.setLocalDescription(await pc.createOffer());
      if (!this.alive(leg)) return;
      await waitForIceGathering(pc);
      if (!this.alive(leg)) return;
      const sdp = finalSdp(pc.localDescription?.sdp);
      leg.stage = "posting";
      const res = await post<{ call: string; provider_call_id: string; sdp_answer?: string }>("start_call", { thread, sdp });
      leg.stage = "posted";
      leg.callId = res.provider_call_id;
      leg.call = res.call;
      if (!this.alive(leg)) {
        // Hung up while WhatsApp was placing the call: end it there too.
        tell("hangup", { call: res.call, provider_call_id: res.provider_call_id });
        return;
      }
      this.emit({ type: "outgoing", callId: res.provider_call_id });
      if (res.sdp_answer) await this.applyAnswer(leg, res.sdp_answer);
    } catch (e) {
      if (leg.stage === "posting") leg.stage = "preparing"; // start_call failed: no call at Meta
      this.finish(leg, reasonOf(e, "The WhatsApp call could not be placed."), true);
    }
  }

  private async acceptCall(callId: string | null) {
    const p = this.takePending(callId);
    if (!p || this.current) return;
    // One call at a time: the other offers ringing here are passed to colleagues.
    for (const other of this.pending.values()) tell("decline", { call: other.call });
    this.pending.clear();
    const leg = this.begin("in", p.callId, p.call);
    try {
      const pc = await this.openPeer(leg, p.sdp);
      if (!this.alive(leg)) return;
      await pc.setLocalDescription(await pc.createAnswer());
      if (!this.alive(leg)) return;
      await waitForIceGathering(pc);
      if (!this.alive(leg)) return;
      const sdp = finalSdp(pc.localDescription?.sdp);
      leg.stage = "posting";
      await post("answer", { call: p.call, sdp });
      leg.stage = "posted";
      if (!this.alive(leg)) {
        // Hung up while Meta was accepting: end the call that now exists.
        tell("hangup", { call: p.call, provider_call_id: p.callId });
        return;
      }
      await this.markConnected(leg);
    } catch (e) {
      const refused = leg.stage === "posting";
      if (refused) leg.stage = "preparing"; // the answer was refused: no call to end, only to decline
      if (!this.alive(leg)) {
        this.release(leg);
        if (refused) tell("decline", { call: p.call });
        return;
      }
      this.finish(leg, reasonOf(e, "The WhatsApp call could not be answered."), true);
    }
  }

  private async applyAnswer(leg: Leg, sdp: string) {
    if (leg.answerApplied || !leg.pc || leg.pc.remoteDescription) return;
    leg.answerApplied = true;
    try {
      await leg.pc.setRemoteDescription({ type: "answer", sdp });
    } catch (e) {
      if (this.alive(leg)) this.finish(leg, reasonOf(e, "WhatsApp's answer could not be applied."), true);
    }
  }

  /** A peer with a sender for the microphone (but no track on it yet), and the offer applied if given. */
  private async openPeer(leg: Leg, remoteOffer?: string): Promise<RTCPeerConnection> {
    let stream: MediaStream;
    try {
      stream = await env().navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch (e: any) {
      if (e?.name === "NotAllowedError") this.emit({ type: "micDenied", denied: true });
      throw new Error("The microphone is not available. Allow it in the browser address bar.");
    }
    leg.stream = stream;
    if (!this.alive(leg)) {
      this.release(leg); // hung up during the microphone prompt
      return Promise.reject(new Error("abandoned"));
    }
    this.emit({ type: "micDenied", denied: false });

    const Peer = env().RTCPeerConnection;
    const pc: RTCPeerConnection = new Peer({ iceServers: this.iceServers, bundlePolicy: "max-bundle", rtcpMuxPolicy: "require" });
    leg.pc = pc;
    pc.ontrack = (ev) => this.play(ev.streams?.[0] ?? new MediaStream([ev.track]));
    pc.onconnectionstatechange = () => {
      if (!this.alive(leg)) return;
      if (pc.connectionState === "failed") {
        this.finish(leg, "The call's audio could not connect. A TURN server may be needed on this network.", true);
      }
    };

    if (remoteOffer) await pc.setRemoteDescription({ type: "offer", sdp: remoteOffer });
    if (!this.alive(leg)) return pc;
    const track = stream.getAudioTracks()[0];
    if (!track) throw new Error("No microphone was found.");
    leg.track = track;
    // One audio track, one stream: all Meta accepts. Silent (no track) until Meta accepts.
    leg.sender = pc.addTrack(track, stream);
    await leg.sender.replaceTrack(null);
    return pc;
  }

  private async markConnected(leg: Leg) {
    if (!this.alive(leg) || leg.connected) return;
    leg.connected = true;
    if (leg.track) leg.track.enabled = !this.muted;
    try {
      await leg.sender?.replaceTrack(leg.track);
    } catch {
      /* the sender is gone with the peer */
    }
    if (!this.alive(leg)) return;
    this.emit({ type: "connected", callId: leg.callId || null });
  }

  /**
   * Leave this leg: close what it opened and, if `notify`, tell the server — a decline for an
   * incoming call never answered, a hang-up for a call Meta knows. A post still in flight posts
   * its own hang-up when it returns (see the `alive` checks after `post`).
   */
  private abandon(leg: Leg, notify: boolean) {
    if (this.current?.token === leg.token) this.current = null;
    this.release(leg);
    if (!notify) return;
    if (leg.stage === "posted" && (leg.call || leg.callId)) {
      tell("hangup", { call: leg.call, provider_call_id: leg.callId });
    } else if (leg.stage === "preparing" && leg.direction === "in" && leg.call) {
      tell("decline", { call: leg.call });
    }
  }

  private finish(leg: Leg, reason: string | undefined, notify: boolean) {
    if (!this.alive(leg)) {
      this.release(leg);
      return;
    }
    this.abandon(leg, notify);
    this.emit({ type: "ended", reason });
  }

  /** The tab is closing or reloading: a live call cannot survive it, so end it at Meta. */
  private pageClosing() {
    const leg = this.current;
    if (!leg) return;
    this.current = null;
    this.release(leg);
    if (leg.stage !== "preparing" && (leg.call || leg.callId)) {
      beacon("hangup", { call: leg.call, provider_call_id: leg.callId });
    } else if (leg.direction === "in" && leg.call) {
      beacon("decline", { call: leg.call });
    }
  }

  private play(stream: MediaStream) {
    try {
      if (!this.audio) {
        this.audio = new Audio();
        this.audio.autoplay = true;
      }
      this.audio.srcObject = stream;
      void this.audio.play().catch(() => {
        /* autoplay is allowed after the click that answered or placed the call */
      });
    } catch {
      /* no audio element in this environment */
    }
  }

  /** Close the peer and stop the microphone of one leg. Safe to call twice. */
  private release(leg: Leg) {
    try {
      leg.pc?.close();
    } catch {
      /* already closed */
    }
    leg.pc = null;
    leg.sender = null;
    leg.stream?.getTracks().forEach((t) => {
      try {
        t.stop();
      } catch {
        /* already stopped */
      }
    });
    leg.stream = null;
    leg.track = null;
    if (this.audio && !this.current) {
      try {
        this.audio.pause();
        this.audio.srcObject = null;
      } catch {
        /* nothing playing */
      }
    }
  }
}
