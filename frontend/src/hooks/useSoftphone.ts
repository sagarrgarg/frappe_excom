import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useFrappeEventListener, useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { toastError } from "../components/ErrorDialog";
import { useFrappeGetCall } from "@/lib/api";
import { ringtone } from "@/lib/ringtone";
import { softphone, type SoftphoneState } from "@/lib/softphone";

/**
 * React's view of the softphone.
 *
 * The SDK object lives in `lib/softphone.ts` and outlives this hook, so mounting and unmounting a
 * component never drops the agent's registration. This hook only:
 *   - fetches the config and the login token, and keeps the token fresh
 *   - heartbeats, so a closed laptop leaves the ring set on its own
 *   - listens for the server's own call events, which are the authority on who answered
 *   - exposes the actions a component needs
 */

/** One voice line this agent works. An agent may work several — India and the US, say. */
export interface SoftphoneLine {
  account: string;
  account_name: string;
  business_number?: string;
  country_code?: string;
  has_endpoint?: boolean;
  browser_calls?: boolean;
  phone_calls?: boolean;
  allows_international?: boolean;
  capabilities?: string[];
  presence?: { available: boolean; registered: boolean; busy: boolean };
}

export interface SoftphoneConfig {
  enabled: boolean;
  reason?: string;
  /** Every line the agent works. The top-level fields below describe the first of them. */
  lines?: SoftphoneLine[];
  account?: string;
  account_name?: string;
  business_number?: string;
  has_endpoint?: boolean;
  browser_calls?: boolean;
  phone_calls?: boolean;
  capabilities?: string[];
  presence?: { available: boolean; registered: boolean; busy: boolean };
  heartbeat_seconds?: number;
}

interface TokenResponse {
  token: string;
  account: string;
  options: Record<string, unknown>;
  refresh_after: number;
  capabilities: string[];
}

/** The screen pop. Published only to the agents Excom decided should ring. */
export interface RingingEvent {
  provider_call_id: string;
  from_number: string;
  business_number: string;
  account: string;
  omni_identity: string | null;
  display_name: string;
  direction: string;
  ring_seconds: number;
}

export interface AnsweredEvent {
  call: string;
  provider_call_id: string;
  answered_by: string | null;
  status: string;
  thread: string | null;
}

export interface EndedEvent {
  call: string;
  provider_call_id: string;
  status: string;
  duration: number;
  thread: string | null;
  missed: boolean;
  /** The provider's own cause name, e.g. "Destination Country Barred". */
  hangup_cause?: string;
  /** That cause turned into something the agent can act on. Empty when there is nothing to add. */
  reason?: string;
}

/** Which line the agent last chose to be signed in on. Survives a reload. */
const PREFERRED_LINE_KEY = "excom_voice_line";

function rememberLine(account: string) {
  try {
    localStorage.setItem(PREFERRED_LINE_KEY, account);
  } catch {
    /* the preference is a convenience; the first line is a fine default */
  }
}

function rememberedLine(): string {
  try {
    return localStorage.getItem(PREFERRED_LINE_KEY) || "";
  } catch {
    return "";
  }
}

const HEARTBEAT_FALLBACK = 60;
const QUALITY_INTERVAL_MS = 60_000;
/** The title alternates on this beat while the phone is ringing. */
const TITLE_FLASH_MS = 900;

export function useSoftphone() {
  const lastQualityAt = useRef(0);
  const state = useSyncExternalStore(softphone.subscribe.bind(softphone), softphone.getState, softphone.getState);

  const { data: configData, mutate: refreshConfig } = useFrappeGetCall<{ message: SoftphoneConfig }>(
    "excom.excom.api.voice.softphone_config",
  );
  const config = configData?.message;

  const { call: getToken } = useFrappePostCall("excom.excom.api.voice.softphone_token");
  const { call: postHeartbeat } = useFrappePostCall("excom.excom.api.voice.heartbeat");
  const { call: postAvailability } = useFrappePostCall("excom.excom.api.voice.set_availability");
  const { call: postDial } = useFrappePostCall("excom.excom.api.voice.dial");
  const { call: postBrowserCall } = useFrappePostCall("excom.excom.api.voice.browser_call_started");
  const { call: postQuality } = useFrappePostCall("excom.excom.api.voice.report_quality");
  const { call: postEndCall } = useFrappePostCall("excom.excom.api.voice.end_call");
  const { call: identify } = useFrappePostCall("excom.excom.api.voice.identify_caller");

  const [available, setAvailable] = useState(false);
  const [incoming, setIncoming] = useState<RingingEvent | null>(null);
  const [answeredElsewhere, setAnsweredElsewhere] = useState<string>("");

  const linesRef = useRef<SoftphoneLine[]>([]);
  linesRef.current = config?.lines ?? [];

  useEffect(() => {
    if (config?.presence) setAvailable(config.presence.available);
  }, [config?.presence?.available]);

  // ── boot ────────────────────────────────────────────────────────────────
  // One line at a time. The SDK keeps a single client on `window._PlivoInstance` and returns it to
  // every later construction, so a second line cannot sit alongside the first — it replaces it.
  // The agent therefore picks a line to be signed in on, and outbound switches as needed.

  const bootedFor = useRef<Set<string>>(new Set());

  const start = useCallback(async (account: string) => {
    if (!account) return;
    try {
      const res = (await getToken({ account })) as { message: TokenResponse };
      const payload = res?.message;
      if (!payload?.token) return;

      await softphone.boot({
        account,
        token: payload.token,
        options: payload.options ?? {},
        onIncoming: (_uuid, from) => {
          // The SDK ringing and the server's screen pop race. Whichever lands first shows a card;
          // the other fills in the detail. The SDK route is also the one that survives Frappe's
          // realtime being down, so it looks the caller up itself rather than showing a bare
          // number — a name is most of what a screen pop is for.
          setIncoming((prev) =>
            prev ?? {
              provider_call_id: "",
              from_number: from,
              business_number: "",
              account,
              omni_identity: null,
              display_name: from,
              direction: "Inbound",
              ring_seconds: 30,
            },
          );
          identify({ number: from })
            .then((res: any) => {
              const who = res?.message;
              if (!who?.found) return;
              setIncoming((prev) =>
                prev && !prev.omni_identity
                  ? { ...prev, display_name: who.display_name, omni_identity: who.omni_identity }
                  : prev,
              );
            })
            .catch(() => {
              /* a nameless pop still rings; this only adds the name */
            });
        },
        onOutgoing: (uuid) => {
          const { peerNumber, threadId } = softphone.getState().call;
          postBrowserCall({
            provider_call_id: uuid,
            to_number: peerNumber,
            account,
            thread: threadId ?? "",
          }).catch(() => {
            /* the answer-URL webhook creates the record too; this only removes the gap */
          });
        },
        onEnded: () => setIncoming(null),
        onQuality: (_uuid, metrics) => {
          const callName = softphone.getState().call.callName;
          if (!callName) return;
          // mediaMetrics fires continuously. One sample a minute is plenty to answer "was the line
          // bad"; posting every one would be a request per second per agent on a call.
          const now = Date.now();
          if (now - lastQualityAt.current < QUALITY_INTERVAL_MS) return;
          lastQualityAt.current = now;
          postQuality({ call: callName, metrics: JSON.stringify(metrics) }).catch(() => {
            /* a quality sample is never worth surfacing an error for */
          });
        },
      });

      bootedFor.current.add(account);
      // Refresh well before expiry, so a token never lapses mid-shift.
      const after = Math.max(60, payload.refresh_after || 3000);
      window.setTimeout(() => void start(account), after * 1000);
    } catch (e: unknown) {
      toastError(e, "The softphone could not connect");
    }
  }, [getToken, postQuality]);

  const bootable = (config?.lines ?? []).filter((l) => l.browser_calls && l.has_endpoint);
  const bootableKey = bootable.map((l) => l.account).join("|");

  /** The line to sign in on: whatever the agent last chose, else the first one they work. */
  const preferredLine =
    bootable.find((l) => l.account === rememberedLine())?.account || bootable[0]?.account || "";

  useEffect(() => {
    if (!config?.enabled || !preferredLine) return;
    if (bootedFor.current.has(preferredLine)) return;
    void start(preferredLine);
  }, [config?.enabled, bootableKey, preferredLine, start]);

  /** Sign the browser in on another line. Resolves true once that line is actually reachable. */
  const switchLine = useCallback(
    async (account: string): Promise<boolean> => {
      if (!account) return false;
      if (softphone.getState().activeAccount === account) {
        return softphone.isRegisteredOn(account);
      }
      try {
        await start(account);
        return await softphone.waitUntilRegistered(account);
      } catch (e: unknown) {
        toastError(e, "Could not switch line");
        return false;
      }
    },
    [start],
  );

  // ── heartbeat ───────────────────────────────────────────────────────────
  // A registered socket is not proof the agent is reachable — the tab could be gone and the socket
  // half-open. The heartbeat is what actually keeps them in a ring set, and stopping it is what
  // takes them out.

  // Only the line the browser is actually signed in to gets a heartbeat. The others must fall out
  // of their ring sets, or a caller is put through to a desk that cannot answer.
  const liveAccount =
    state.registration === "registered" ? state.activeAccount ?? "" : "";

  useEffect(() => {
    if (!liveAccount) return;

    const every = (config?.heartbeat_seconds || HEARTBEAT_FALLBACK) * 1000;
    const beat = () => {
      postHeartbeat({ account: liveAccount, registered: 1 }).catch(() => {
        /* one missed beat is tolerated by the TTL; noise here helps nobody */
      });
    };
    beat();
    const id = window.setInterval(beat, every);
    return () => window.clearInterval(id);
  }, [liveAccount, config?.heartbeat_seconds, postHeartbeat]);

  // Leaving a line must take the agent out of its ring set at once, rather than 150 seconds later.
  const previousLive = useRef<string>("");
  useEffect(() => {
    const left = previousLive.current;
    previousLive.current = liveAccount;
    if (left && left !== liveAccount) {
      postHeartbeat({ account: left, registered: 0 }).catch(() => {
        /* the TTL will retire it anyway */
      });
    }
  }, [liveAccount, postHeartbeat]);

  // Signing off should be instant, not "within 150 seconds".
  useEffect(() => {
    const accounts = (config?.lines ?? []).map((l) => l.account);
    if (!accounts.length) return;
    const onLeave = () => {
      try {
        for (const account of accounts) {
          navigator.sendBeacon?.(
            `/api/method/excom.excom.api.voice.heartbeat?account=${encodeURIComponent(account)}&registered=0`,
          );
        }
      } catch {
        /* best effort — the TTL is the real guarantee */
      }
    };
    window.addEventListener("pagehide", onLeave);
    return () => window.removeEventListener("pagehide", onLeave);
  }, [config?.account]);

  // ── server events ───────────────────────────────────────────────────────
  // The server is the authority on who answered: it computed the ring set, so it knows. The SDK
  // only knows about this browser's own leg.

  useFrappeEventListener("excom:call_ringing", (data: RingingEvent) => {
    setAnsweredElsewhere("");
    setIncoming(data);
  });

  useFrappeEventListener("excom:call_answered", (data: AnsweredEvent) => {
    softphone.attachRecord(data.call, data.thread);
    const me = (window as any).frappe?.boot?.user?.name;
    if (data.answered_by && me && data.answered_by !== me) {
      // Somebody else took it. Collapse the pop rather than leaving it ringing at a phone that
      // has already stopped.
      setAnsweredElsewhere(data.answered_by);
      setIncoming(null);
    }
  });

  useFrappeEventListener("excom:call_ended", (data: EndedEvent) => {
    setIncoming(null);
    setAnsweredElsewhere("");
    if (data.reason) {
      // A carrier refusal and an unanswered call look identical from here — the call simply does
      // not connect. Saying which is what stops an agent redialling a barred country all day.
      toast.error(data.hangup_cause || "The call did not connect", {
        description: data.reason,
        duration: 12_000,
      });
      return;
    }
    if (data.missed) toast.message("Missed call", { description: "It is in the missed-call queue." });
  });

  // ── the ring ────────────────────────────────────────────────────────────
  // Driven off `incoming` rather than off the SDK, deliberately. `incoming` is set by whichever of
  // the two arrives first — the SDK's own event or the server's screen pop — so the phone rings
  // even when one of those paths is down, and it rings exactly once when both work.

  const silentRingWarned = useRef(false);
  // The pop appears on the bare number and the name arrives a moment later, from `identify`.
  // Reading it through a ref lets the tab title catch up without the effect re-running — which
  // would restart the ring from the top every time a caller turned out to be someone we know.
  const incomingRef = useRef<RingingEvent | null>(null);
  incomingRef.current = incoming;

  useEffect(() => {
    if (!incoming) return;

    if (!ringtone.start() && !silentRingWarned.current) {
      // Only worth saying once a session, and only when it is actually true: an agent who muted
      // the ringer on purpose is never told anything.
      silentRingWarned.current = true;
      toast.warning("The ringtone is silent", {
        description: "Your browser blocks sound until you interact with the page. Click anywhere to switch it on.",
        duration: 10_000,
      });
    }

    const original = document.title;
    let alternate = false;
    const flash = window.setInterval(() => {
      alternate = !alternate;
      const who = incomingRef.current;
      document.title = alternate
        ? `📞 ${who?.display_name || who?.from_number || "Incoming call"}`
        : original;
    }, TITLE_FLASH_MS);

    // A buried tab is the case the sound alone does not cover — the agent may be in a spreadsheet
    // with the speakers on somebody else's desk.
    let note: Notification | null = null;
    try {
      if (document.hidden && "Notification" in window && Notification.permission === "granted") {
        note = new Notification("Incoming call", {
          body: incoming.display_name || incoming.from_number,
          tag: `excom-call-${incoming.provider_call_id || "unknown"}`,
          requireInteraction: true,
        });
        note.onclick = () => {
          window.focus();
          note?.close();
        };
      }
    } catch {
      /* notifications are a nicety; the tab title and the ring are the guarantee */
    }

    return () => {
      ringtone.stop();
      window.clearInterval(flash);
      document.title = original;
      try {
        note?.close();
      } catch {
        /* already dismissed */
      }
    };
  }, [incoming?.provider_call_id, incoming?.from_number, Boolean(incoming)]);

  // ── actions ─────────────────────────────────────────────────────────────

  const toggleAvailability = useCallback(
    async (next?: boolean) => {
      const wanted = next ?? !available;
      if (wanted) {
        // This runs inside the agent's click, which is the only context a browser will grant these
        // in — and it is the honest moment to ask, because going on the queue is a request to be
        // interrupted. Asked on page load instead, both prompts read as spam and get dismissed.
        ringtone.arm();
        try {
          if ("Notification" in window && Notification.permission === "default") {
            void Notification.requestPermission();
          }
        } catch {
          /* declined or unsupported — the ring and the tab title still do the job */
        }
      }

      setAvailable(wanted);
      try {
        await postAvailability({ available: wanted ? 1 : 0, account: config?.account ?? "" });
        // A mode switch with no confirmation is how an agent ends up believing they are on the
        // queue when the click did not land.
        if (wanted) toast.success("You are taking calls.");
        else toast.message("You are off the queue.", { description: "Calls will ring somebody else." });
      } catch (e: unknown) {
        setAvailable(!wanted);
        toastError(e, "Your availability could not be saved");
      }
    },
    [available, config?.account, postAvailability],
  );

  const dial = useCallback(
    async (
      toNumber: string,
      opts: { thread?: string; displayName?: string; transport?: string; account?: string } = {},
    ) => {
      try {
        const res = (await postDial({
          // Deliberately not naming a line. The server picks by destination — the Indian number on
          // the Indian line, the American one on the American line — and naming one here would
          // short-circuit that and bill an international call to whichever line happened to load
          // first. `opts.account` exists for the caller who really does mean a specific line.
          to_number: toNumber,
          account: opts.account ?? "",
          thread: opts.thread ?? "",
          transport: opts.transport ?? "",
        })) as { message: any };
        const plan = res?.message;
        if (!plan) {
          toast.error("The call could not be started.", {
            description: "The server accepted the request but returned no dialling plan.",
          });
          return null;
        }

        if (plan.mode === "phone") {
          toast.success(plan.message || "Your phone will ring.", {
            description: `Answer it and Excom connects you to ${opts.displayName || toNumber}.`,
          });
          return plan;
        }

        // `plan.account` is the line the server chose from the destination. The browser can only
        // be signed in to one line at a time, so if that is not the line we are on, sign in there
        // first — dialling an American number while signed in to the Indian account puts the call
        // out over the wrong provider, and it comes back as a bare "Busy".
        if (softphone.getState().activeAccount !== plan.account) {
          const name =
            linesRef.current.find((l) => l.account === plan.account)?.account_name || plan.account;
          const switching = toast.loading(`Switching to ${name}…`);
          const ready = await switchLine(plan.account);
          toast.dismiss(switching);
          if (!ready) {
            toast.error(`Could not sign in to ${name}`, {
              description: "The call was not placed. Try again in a moment.",
            });
            return null;
          }
        }

        // The leg is registered from the `onOutgoing` hook, not here: the SDK has no call uuid yet
        // at this point, so reading one back now would always be null.
        softphone.call(plan.dial_string, plan.extra_headers ?? {}, {
          displayName: opts.displayName || toNumber,
          threadId: opts.thread ?? null,
          account: plan.account,
        });
        return plan;
      } catch (e: unknown) {
        toastError(e, `Could not call ${opts.displayName || toNumber}`);
        return null;
      }
    },
    [postDial, postBrowserCall, switchLine],
  );

  // Both stop the ring by hand rather than leaving it to the effect's cleanup. The cleanup does run
  // — but a render later, and a ringtone that carries on past the click on Answer is the first
  // thing anyone notices.

  // After a call placed on another line, come home. The line the browser is signed in to is the
  // only line it can receive on, so staying on the American desk after one American call would
  // quietly stop Indian calls from ringing at all.
  useEffect(() => {
    if (state.call.phase !== "none") return;
    if (!preferredLine || !state.activeAccount) return;
    if (state.activeAccount === preferredLine) return;
    const id = window.setTimeout(() => void switchLine(preferredLine), 1500);
    return () => window.clearTimeout(id);
  }, [state.call.phase, state.activeAccount, preferredLine, switchLine]);

  const answer = useCallback(() => {
    ringtone.stop();
    softphone.answer();
    setIncoming(null);
  }, []);

  const reject = useCallback(() => {
    ringtone.stop();
    softphone.reject();
    setIncoming(null);
  }, []);

  const hangup = useCallback(async () => {
    const callName = softphone.getState().call.callName;
    softphone.hangup();
    if (callName) {
      postEndCall({ call: callName }).catch(() => {
        /* the browser leg is already down; the provider will report the hangup either way */
      });
    }
  }, [postEndCall]);

  return {
    config,
    refreshConfig,
    state: state as SoftphoneState,
    available,
    incoming,
    answeredElsewhere,
    dismissIncoming: () => {
      ringtone.stop();
      setIncoming(null);
    },
    toggleAvailability,
    dial,
    answer,
    reject,
    hangup,
    toggleMute: () => softphone.toggleMute(),
    sendDigit: (tone: string) => softphone.sendDigit(tone),
    lines: config?.lines ?? [],
    activeLine: state.activeAccount,
    /** Sign in on another line. The agent's choice is remembered across reloads. */
    chooseLine: async (account: string) => {
      rememberLine(account);
      return switchLine(account);
    },
    // Asked across every line, not just the first: an agent whose Indian desk is phone-only can
    // still call from the browser if their American desk has a softphone.
    canCallInBrowser: (config?.lines ?? []).some((l) => l.browser_calls && l.has_endpoint),
    canCallOnPhone: (config?.lines ?? []).some((l) => l.phone_calls),
  };
}

export type SoftphoneApi = ReturnType<typeof useSoftphone>;
