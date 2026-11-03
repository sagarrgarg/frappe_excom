import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DriverEvent, DriverInit, SoftphoneDriver } from "../voice/driver";
import { WhatsAppDriver } from "../voice/whatsapp-driver";

/**
 * The WhatsApp driver against a fake RTCPeerConnection / getUserMedia / fetch, and the coordinator
 * routing that must keep a WhatsApp cancel away from a live call on another line.
 */

const SDP = [
  "v=0",
  "o=- 1 2 IN IP4 127.0.0.1",
  "s=-",
  "t=0 0",
  "m=audio 9 UDP/TLS/RTP/SAVPF 111",
  "a=rtpmap:111 opus/48000/2",
  "a=candidate:1 1 udp 2122260223 192.0.2.10 54400 typ host",
  "",
].join("\r\n");

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush(times = 8) {
  for (let i = 0; i < times; i++) await new Promise((r) => setTimeout(r, 0));
}

class FakeTrack {
  kind = "audio";
  enabled = true;
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

class FakeStream {
  tracks = [new FakeTrack()];
  getAudioTracks() {
    return this.tracks;
  }
  getTracks() {
    return this.tracks;
  }
}

class FakeSender {
  track: unknown;
  replaced: unknown[] = [];
  dtmf = null;
  constructor(track: unknown) {
    this.track = track;
  }
  async replaceTrack(t: unknown) {
    this.replaced.push(t);
    this.track = t;
  }
}

class FakePeer {
  static all: FakePeer[] = [];
  static gathering: "complete" | "gathering" = "complete";
  iceGatheringState: string = FakePeer.gathering;
  connectionState = "new";
  localDescription: { type: string; sdp: string } | null = null;
  remoteDescription: { type: string; sdp: string } | null = null;
  remoteSets = 0;
  closed = false;
  senders: FakeSender[] = [];
  listeners = new Set<() => void>();
  ontrack: unknown = null;
  onconnectionstatechange: (() => void) | null = null;
  constructor(public config: unknown) {
    FakePeer.all.push(this);
  }
  async createOffer() {
    return { type: "offer", sdp: SDP };
  }
  async createAnswer() {
    return { type: "answer", sdp: SDP };
  }
  async setLocalDescription(d: { type: string; sdp: string }) {
    this.localDescription = d;
  }
  async setRemoteDescription(d: { type: string; sdp: string }) {
    this.remoteSets += 1;
    await Promise.resolve();
    this.remoteDescription = d;
  }
  addTrack(track: unknown) {
    const s = new FakeSender(track);
    this.senders.push(s);
    return s;
  }
  addEventListener(_t: string, l: () => void) {
    this.listeners.add(l);
  }
  removeEventListener(_t: string, l: () => void) {
    this.listeners.delete(l);
  }
  finishGathering() {
    this.iceGatheringState = "complete";
    this.listeners.forEach((l) => l());
  }
  fail() {
    this.connectionState = "failed";
    this.onconnectionstatechange?.();
  }
  close() {
    this.closed = true;
  }
}

/** Every POST the driver made, by method name, and a hook to hold one back. */
let posts: { method: string; body: any }[] = [];
let hold: Record<string, ReturnType<typeof deferred<any>>> = {};
let replies: Record<string, any> = {};
let mic: ReturnType<typeof deferred<FakeStream>> | null = null;
let streams: FakeStream[] = [];

function methodOf(url: string) {
  return url.split(".").pop() || "";
}

beforeEach(() => {
  posts = [];
  hold = {};
  replies = {};
  mic = null;
  streams = [];
  FakePeer.all = [];
  FakePeer.gathering = "complete";
  vi.stubGlobal("RTCPeerConnection", FakePeer);
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: () => {
        if (mic) return mic.promise;
        const s = new FakeStream();
        streams.push(s);
        return Promise.resolve(s);
      },
    },
    sendBeacon: vi.fn(() => true),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: any) => {
      const method = methodOf(url);
      posts.push({ method, body: init?.body ? JSON.parse(init.body) : {} });
      const message = hold[method] ? await hold[method].promise : (replies[method] ?? {});
      return { ok: true, status: 200, json: async () => ({ message }) };
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function driver() {
  const events: DriverEvent[] = [];
  const d = new WhatsAppDriver({ options: {}, emit: (e) => events.push(e) });
  return { d, events };
}

function offer(d: WhatsAppDriver, id = "wacid.1", call = "CALL-1") {
  d.signal({ type: "offer", provider_call_id: id, call, sdp: SDP, from: "+91 98" });
}

const sent = (method: string) => posts.filter((p) => p.method === method);

describe("WhatsApp driver: abandoned calls", () => {
  it("a hang-up during the microphone prompt posts no answer, declines, and stops the microphone", async () => {
    const { d } = driver();
    offer(d);
    mic = deferred<FakeStream>();
    d.answer("wacid.1");
    await flush();
    d.hangup();
    const stream = new FakeStream();
    mic.resolve(stream);
    await flush();
    expect(sent("answer")).toEqual([]);
    expect(sent("decline")).toHaveLength(1);
    expect(stream.tracks[0].stopped).toBe(true);
    expect(FakePeer.all).toHaveLength(0);
  });

  it("a hang-up during the ICE wait posts no answer and closes the peer", async () => {
    FakePeer.gathering = "gathering";
    const { d } = driver();
    offer(d);
    d.answer("wacid.1");
    await flush();
    expect(FakePeer.all).toHaveLength(1);
    d.hangup();
    FakePeer.all[0].finishGathering();
    await flush();
    expect(sent("answer")).toEqual([]);
    expect(FakePeer.all[0].closed).toBe(true);
    expect(streams[0].tracks[0].stopped).toBe(true);
  });

  it("a hang-up while the answer is with the server ends the call that now exists", async () => {
    const { d, events } = driver();
    offer(d);
    hold.answer = deferred();
    d.answer("wacid.1");
    await flush();
    expect(sent("answer")).toHaveLength(1);
    d.hangup();
    hold.answer.resolve({ call: "CALL-1" });
    await flush();
    expect(sent("hangup")).toEqual([{ method: "hangup", body: { call: "CALL-1", provider_call_id: "wacid.1" } }]);
    expect(sent("decline")).toEqual([]);
    expect(events.filter((e) => e.type === "connected")).toEqual([]);
    expect(FakePeer.all[0].closed).toBe(true);
  });

  it("a hang-up while placing the call never posts start_call", async () => {
    const { d } = driver();
    mic = deferred<FakeStream>();
    d.call("Asha", { thread: "THREAD-1" });
    await flush();
    d.hangup();
    const stream = new FakeStream();
    mic.resolve(stream);
    await flush();
    expect(sent("start_call")).toEqual([]);
    expect(sent("hangup")).toEqual([]);
    expect(stream.tracks[0].stopped).toBe(true);
  });

  it("a hang-up during the outgoing ICE wait never posts start_call and closes the peer", async () => {
    FakePeer.gathering = "gathering";
    const { d } = driver();
    d.call("Asha", { thread: "THREAD-1" });
    await flush();
    d.hangup();
    FakePeer.all[0].finishGathering();
    await flush();
    expect(sent("start_call")).toEqual([]);
    expect(FakePeer.all[0].closed).toBe(true);
    expect(streams[0].tracks[0].stopped).toBe(true);
  });

  it("a hang-up while start_call is in flight hangs up the call it placed", async () => {
    const { d } = driver();
    hold.start_call = deferred();
    d.call("Asha", { thread: "THREAD-1" });
    await flush();
    d.hangup();
    hold.start_call.resolve({ call: "CALL-9", provider_call_id: "wacid.9" });
    await flush();
    expect(sent("hangup")).toEqual([{ method: "hangup", body: { call: "CALL-9", provider_call_id: "wacid.9" } }]);
  });

  it("a failed connection ends the call at Meta too", async () => {
    const { d, events } = driver();
    offer(d);
    d.answer("wacid.1");
    await flush();
    expect(events.some((e) => e.type === "connected")).toBe(true);
    FakePeer.all[0].fail();
    expect(sent("hangup")).toHaveLength(1);
    expect(events.at(-1)?.type).toBe("ended");
  });

  it("closing the page sends a beacon hang-up for a live call", async () => {
    const { d } = driver();
    offer(d);
    d.answer("wacid.1");
    await flush();
    (d as any).pageClosing();
    expect((globalThis.navigator as any).sendBeacon).toHaveBeenCalledTimes(1);
    const [url, form] = (globalThis.navigator as any).sendBeacon.mock.calls[0];
    expect(url).toContain("whatsapp_calling.hangup");
    expect((form as FormData).get("call")).toBe("CALL-1");
    expect((form as FormData).has("csrf_token")).toBe(true);
  });
});

describe("WhatsApp driver: media and answers", () => {
  it("keeps the microphone off the sender until Meta accepts an outgoing call", async () => {
    const { d, events } = driver();
    replies.start_call = { call: "CALL-2", provider_call_id: "wacid.2" };
    d.call("Asha", { thread: "THREAD-1" });
    await flush();
    const sender = FakePeer.all[0].senders[0];
    expect(sender.track).toBeNull();
    // the connection coming up is not "connected": the person has not picked up
    FakePeer.all[0].connectionState = "connected";
    FakePeer.all[0].onconnectionstatechange?.();
    expect(events.some((e) => e.type === "connected")).toBe(false);
    d.signal({ type: "status", provider_call_id: "wacid.2", status: "ACCEPTED" });
    await flush();
    expect(sender.track).toBe(streams[0].tracks[0]);
    expect(events.some((e) => e.type === "connected")).toBe(true);
  });

  it("applies Meta's answer once when the signal and the start_call response both carry it", async () => {
    const { d } = driver();
    hold.start_call = deferred();
    d.call("Asha", { thread: "THREAD-1" });
    await flush();
    d.signal({ type: "answer", provider_call_id: "wacid.3", sdp: SDP });
    hold.start_call.resolve({ call: "CALL-3", provider_call_id: "wacid.3", sdp_answer: SDP });
    d.signal({ type: "answer", provider_call_id: "wacid.3", sdp: SDP });
    await flush();
    expect(FakePeer.all[0].remoteSets).toBe(1);
  });

  it("answers an incoming call with the microphone added only after the accept", async () => {
    const { d, events } = driver();
    hold.answer = deferred();
    offer(d);
    d.answer("wacid.1");
    await flush();
    const sender = FakePeer.all[0].senders[0];
    expect(sender.track).toBeNull();
    hold.answer.resolve({});
    await flush();
    expect(sender.track).toBe(streams[0].tracks[0]);
    expect(events.some((e) => e.type === "connected")).toBe(true);
  });
});

describe("WhatsApp driver: two calls ringing", () => {
  it("keeps offers by call id: a cancel or terminate drops only its own", async () => {
    const { d, events } = driver();
    offer(d, "wacid.A", "CALL-A");
    offer(d, "wacid.B", "CALL-B");
    expect(events.filter((e) => e.type === "incoming")).toHaveLength(2);
    d.signal({ type: "cancel", provider_call_id: "wacid.B" });
    expect(events.at(-1)).toEqual({ type: "incomingCancelled", callId: "wacid.B" });
    d.signal({ type: "terminate", provider_call_id: "wacid.Z" });
    expect(events.filter((e) => e.type === "incomingCancelled")).toHaveLength(1);
    d.answer("wacid.A");
    await flush();
    expect(sent("answer")[0].body.call).toBe("CALL-A");
  });

  it("forgets a dismissed offer, so its cancel says nothing", () => {
    const { d, events } = driver();
    offer(d, "wacid.A", "CALL-A");
    d.dismiss("wacid.A");
    d.signal({ type: "cancel", provider_call_id: "wacid.A" });
    expect(events.filter((e) => e.type === "incomingCancelled")).toEqual([]);
  });

  it("a timeout decline says so", () => {
    const { d } = driver();
    offer(d);
    d.reject("wacid.1", { timeout: true });
    expect(sent("decline")[0].body).toEqual({ call: "CALL-1", timeout: 1 });
  });
});

// ── the coordinator ──────────────────────────────────────────────────────────

class FakeVoiceDriver implements SoftphoneDriver {
  static last: FakeVoiceDriver | null = null;
  readonly vendor = "fake";
  readonly exclusive = false;
  hungUp = 0;
  constructor(public init: DriverInit) {
    FakeVoiceDriver.last = this;
  }
  async start() {
    this.init.emit({ type: "registered" });
  }
  refresh() {}
  destroy() {}
  call() {}
  answer() {
    this.init.emit({ type: "connected", callId: "plivo-1" });
  }
  reject() {}
  hangup() {
    this.hungUp += 1;
  }
  setMuted() {}
  sendDigit() {}
}

vi.mock("../voice/driver", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../voice/driver")>();
  return {
    ...actual,
    makeDriver: async (sdk: string, init: DriverInit) =>
      sdk === "fake-voice" ? new FakeVoiceDriver(init) : actual.makeDriver(sdk, init),
  };
});

describe("softphone coordinator: a WhatsApp cancel never ends another line's call", () => {
  it("drops a refused offer and ignores its cancel while a Plivo call is live", async () => {
    vi.stubGlobal("window", globalThis);
    vi.spyOn(console, "info").mockImplementation(() => {});
    const { softphone } = await import("../softphone");
    await softphone.boot({ account: "IN-LINE", sdk: "fake-voice", token: "t", options: {} });
    await softphone.boot({ account: "WA-LINE", sdk: "whatsapp-webrtc", token: "", options: {} });
    expect(softphone.isRegisteredOn("WA-LINE")).toBe(true);

    // a Plivo call rings and is answered
    FakeVoiceDriver.last!.init.emit({ type: "incoming", callId: "plivo-1", from: "+91 1" });
    softphone.answer();
    expect(softphone.getState().call).toMatchObject({ phase: "connected", account: "IN-LINE" });

    // a WhatsApp call arrives meanwhile, then is cancelled (answered by a colleague) and terminated
    softphone.signal("WA-LINE", { type: "offer", provider_call_id: "wacid.X", call: "CALL-X", sdp: SDP, from: "+91 2" });
    expect(softphone.getState().call.account).toBe("IN-LINE");
    softphone.signal("WA-LINE", { type: "cancel", provider_call_id: "wacid.X" });
    softphone.signal("WA-LINE", { type: "terminate", provider_call_id: "wacid.X" });
    expect(softphone.getState().call).toMatchObject({ phase: "connected", account: "IN-LINE", callUUID: "plivo-1" });

    // even a stray cancel or end from the WhatsApp line leaves the Plivo call alone
    const wa = (softphone as any).runtimes.get("WA-LINE").driver as WhatsAppDriver;
    (wa as any).emit({ type: "incomingCancelled", callId: "wacid.X" });
    (wa as any).emit({ type: "ended" });
    expect(softphone.getState().call).toMatchObject({ phase: "connected", account: "IN-LINE" });
    expect(FakeVoiceDriver.last!.hungUp).toBe(0);

    softphone.shutdown();
  });

  it("a WhatsApp cancel ends only the WhatsApp call that is ringing", async () => {
    vi.stubGlobal("window", globalThis);
    vi.spyOn(console, "info").mockImplementation(() => {});
    const { softphone } = await import("../softphone");
    await softphone.boot({ account: "WA-LINE", sdk: "whatsapp-webrtc", token: "", options: {} });
    softphone.signal("WA-LINE", { type: "offer", provider_call_id: "wacid.Y", call: "CALL-Y", sdp: SDP, from: "+91 3" });
    expect(softphone.getState().call).toMatchObject({ phase: "ringing", callUUID: "wacid.Y" });
    softphone.signal("WA-LINE", { type: "cancel", provider_call_id: "wacid.Y" });
    expect(softphone.getState().call.phase).toBe("none");
    softphone.shutdown();
  });

  it("redacts credentials before logging line options", async () => {
    const { redact } = await import("../softphone");
    expect(redact({ iceServers: [{ urls: ["turn:t"], username: "u", credential: "secret" }], account: "A" })).toEqual({
      iceServers: [{ urls: ["turn:t"], username: "u", credential: "…" }],
      account: "A",
    });
  });
});
