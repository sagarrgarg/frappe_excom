import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useFrappeEventListener, useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
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

export interface SoftphoneConfig {
  enabled: boolean;
  reason?: string;
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
}

const HEARTBEAT_FALLBACK = 60;
const QUALITY_INTERVAL_MS = 60_000;

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

  const accountRef = useRef<string>("");
  accountRef.current = config?.account ?? "";

  useEffect(() => {
    if (config?.presence) setAvailable(config.presence.available);
  }, [config?.presence?.available]);

  // ── boot ────────────────────────────────────────────────────────────────
  // Runs once the config says this agent has a softphone. Re-running is safe: boot() is idempotent
  // and an existing client just gets the fresh token.

  const bootedFor = useRef<string>("");

  const start = useCallback(async () => {
    const account = accountRef.current;
    if (!account) return;
    try {
      const res = (await getToken({ account })) as { message: TokenResponse };
      const payload = res?.message;
      if (!payload?.token) return;

      await softphone.boot({
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

      bootedFor.current = account;
      // Refresh well before expiry, so a token never lapses mid-shift.
      const after = Math.max(60, payload.refresh_after || 3000);
      window.setTimeout(() => void start(), after * 1000);
    } catch (e: any) {
      toast.error(e?.message || "The softphone could not connect.");
    }
  }, [getToken, postQuality]);

  useEffect(() => {
    if (!config?.enabled || !config.has_endpoint || !config.browser_calls) return;
    if (bootedFor.current === config.account) return;
    void start();
  }, [config?.enabled, config?.has_endpoint, config?.browser_calls, config?.account, start]);

  // ── heartbeat ───────────────────────────────────────────────────────────
  // A registered socket is not proof the agent is reachable — the tab could be gone and the socket
  // half-open. The heartbeat is what actually keeps them in a ring set, and stopping it is what
  // takes them out.

  useEffect(() => {
    const account = config?.account;
    if (!account || state.registration !== "registered") return;

    const every = (config?.heartbeat_seconds || HEARTBEAT_FALLBACK) * 1000;
    const beat = () => {
      postHeartbeat({ account, registered: 1 }).catch(() => {
        /* one missed beat is tolerated by the TTL; noise here helps nobody */
      });
    };
    beat();
    const id = window.setInterval(beat, every);
    return () => window.clearInterval(id);
  }, [config?.account, config?.heartbeat_seconds, state.registration, postHeartbeat]);

  // Signing off should be instant, not "within 150 seconds".
  useEffect(() => {
    const account = config?.account;
    if (!account) return;
    const onLeave = () => {
      try {
        navigator.sendBeacon?.(
          `/api/method/excom.excom.api.voice.heartbeat?account=${encodeURIComponent(account)}&registered=0`,
        );
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
    if (data.missed) toast.message("Missed call", { description: "It is in the missed-call queue." });
  });

  // ── actions ─────────────────────────────────────────────────────────────

  const toggleAvailability = useCallback(
    async (next?: boolean) => {
      const wanted = next ?? !available;
      setAvailable(wanted);
      try {
        await postAvailability({ available: wanted ? 1 : 0, account: config?.account ?? "" });
      } catch (e: any) {
        setAvailable(!wanted);
        toast.error(e?.message || "That could not be saved.");
      }
    },
    [available, config?.account, postAvailability],
  );

  const dial = useCallback(
    async (toNumber: string, opts: { thread?: string; displayName?: string; transport?: string } = {}) => {
      try {
        const res = (await postDial({
          to_number: toNumber,
          account: config?.account ?? "",
          thread: opts.thread ?? "",
          transport: opts.transport ?? "",
        })) as { message: any };
        const plan = res?.message;
        if (!plan) return null;

        if (plan.mode === "phone") {
          toast.success(plan.message || "Your phone will ring.");
          return plan;
        }

        // The leg is registered from the `onOutgoing` hook, not here: the SDK has no call uuid yet
        // at this point, so reading one back now would always be null.
        softphone.call(plan.dial_string, plan.extra_headers ?? {}, {
          displayName: opts.displayName || toNumber,
          threadId: opts.thread ?? null,
        });
        return plan;
      } catch (e: any) {
        toast.error(e?.message || "The call could not be started.");
        return null;
      }
    },
    [config?.account, postDial, postBrowserCall],
  );

  const answer = useCallback(() => {
    softphone.answer();
    setIncoming(null);
  }, []);

  const reject = useCallback(() => {
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
    dismissIncoming: () => setIncoming(null),
    toggleAvailability,
    dial,
    answer,
    reject,
    hangup,
    toggleMute: () => softphone.toggleMute(),
    sendDigit: (tone: string) => softphone.sendDigit(tone),
    canCallInBrowser: Boolean(config?.browser_calls && config?.has_endpoint),
    canCallOnPhone: Boolean(config?.phone_calls),
  };
}

export type SoftphoneApi = ReturnType<typeof useSoftphone>;
