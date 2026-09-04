import { useCallback, useEffect, useState } from "react";
import {
  Bell, Copy, ExternalLink, KeyRound, Loader2,
  Mail, MessageSquare, Palette, RefreshCw, Reply, Shield,
  Volume2, VolumeX, Zap, RotateCcw, Users, Rows3, ArrowLeftRight, Settings as SettingsIcon,
} from "lucide-react";
import { useFrappePostCall, useFrappeGetDocList, useFrappeCreateDoc } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { toast } from "sonner";
import { Button, Select, Chip, Kbd, SegmentedControl } from "./primitives";
import { AdminPage } from "./shell/AdminPage";
import { useExcomBranding } from "../hooks/useBranding";
import { getDensity, applyDensity, type Density } from "../lib/ui-flag";
import { MOD } from "../lib/hotkeys";

declare global {
  interface Window {
    frappePushNotification?: {
      enableNotification: () => Promise<{ permission_granted: boolean; token?: string }>;
      disableNotification: () => Promise<void>;
      isNotificationEnabled: () => boolean;
    };
  }
}

interface MobileDiscovery {
  client_id: string | null;
  sitename: string;
  site_url: string;
  app_name: string;
  can_manage_mobile_oauth: boolean;
}

interface SettingsPageProps {
  onNavigateBack: () => void;
  embedded?: boolean;
}

const MOBILE_REDIRECT_SCHEME = "excom.app:";

type SectionId =
  | "general"
  | "appearance"
  | "signatures"
  | "notifications"
  | "branding"
  | "accounts"
  | "shortcuts"
  | "canned"
  | "auto-reply";

interface NavItem {
  id: SectionId;
  label: string;
  icon: React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  { id: "general", label: "General", icon: <Zap className="w-4 h-4" /> },
  { id: "appearance", label: "Appearance", icon: <Rows3 className="w-4 h-4" /> },
  { id: "signatures", label: "Email Signatures", icon: <Mail className="w-4 h-4" /> },
  { id: "notifications", label: "Notifications", icon: <Bell className="w-4 h-4" /> },
  { id: "branding", label: "Branding", icon: <Palette className="w-4 h-4" /> },
  { id: "accounts", label: "Accounts", icon: <Users className="w-4 h-4" /> },
  { id: "shortcuts", label: "Keyboard Shortcuts", icon: <KeyRound className="w-4 h-4" /> },
  { id: "canned", label: "Canned Responses", icon: <MessageSquare className="w-4 h-4" /> },
  { id: "auto-reply", label: "Auto-Reply", icon: <Reply className="w-4 h-4" /> },
];

const VALID: SectionId[] = ["general", "appearance", "signatures", "notifications", "branding", "accounts", "shortcuts", "canned", "auto-reply"];

/**
 * Settings — avatar menu page (T3). Left nav on laptop+, segmented strip below.
 * `?section=` deep-links a section (used by ⌘K and the rail).
 */
export function SettingsPage({ onNavigateBack, embedded }: SettingsPageProps) {
  const initial = (() => { const q = new URLSearchParams(window.location.search).get("section") as SectionId | null; return q && VALID.includes(q) ? q : "general"; })();
  const [activeSection, setActiveSection] = useState<SectionId>(initial);
  const { branding } = useExcomBranding();

  useEffect(() => {
    const onPop = () => { const q = new URLSearchParams(window.location.search).get("section") as SectionId | null; if (q && VALID.includes(q)) setActiveSection(q); };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const select = (id: SectionId) => {
    setActiveSection(id);
    try { const u = new URL(window.location.href); u.searchParams.set("section", id); window.history.replaceState(window.history.state, "", u.toString()); } catch { /* ignore */ }
  };

  const content = (
    <>
      {activeSection === "general" && <GeneralSection siteUrl={""} />}
      {activeSection === "appearance" && <AppearanceSection />}
      {activeSection === "signatures" && <SignaturesSection />}
      {activeSection === "notifications" && <NotificationsSection />}
      {activeSection === "branding" && <BrandingSection branding={branding} />}
      {activeSection === "accounts" && <AccountsSection />}
      {activeSection === "shortcuts" && <ShortcutsSection />}
      {activeSection === "canned" && <CannedSection />}
      {activeSection === "auto-reply" && <AutoReplySection />}
    </>
  );

  return (
    <AdminPage title="Settings" icon={<SettingsIcon />} onBack={onNavigateBack} embedded={embedded} bleed
      toolbar={<div className="laptop:hidden"><SegmentedControl variant="segmented" value={activeSection} onChange={select} segments={NAV_ITEMS.map((n) => ({ value: n.id, label: n.label, icon: n.icon }))} /></div>}>
      <div className="flex min-h-full">
        <nav className="hidden laptop:flex w-52 shrink-0 border-r border-border bg-surface-sunken flex-col py-2 sticky top-0 self-start" aria-label="Settings sections">
          {NAV_ITEMS.map((item) => (
            <button key={item.id} type="button" onClick={() => select(item.id)} className={`flex items-center gap-2.5 px-3 h-9 text-sm text-left min-w-0 ${activeSection === item.id ? "bg-surface-active text-ink-1 font-medium" : "text-ink-2 hover:bg-surface-hover hover:text-ink-1"}`}>
              <span className="text-ink-3 shrink-0">{item.icon}</span><span className="truncate">{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="flex-1 min-w-0 p-3">{content}</div>
      </div>
    </AdminPage>
  );
}

/* ─── Appearance ───────────────────────────────────────────────────────────── */

function AppearanceSection() {
  const [density, setDensity] = useState<Density>(getDensity);
  const apply = (d: Density) => { setDensity(d); applyDensity(d); toast.success(d === "compact" ? "Compact rows" : "Comfortable rows"); };
  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Rows3 className="w-4 h-4 text-crayon-blue-text" />} title="Appearance" />
      <Card>
        <p className="font-medium text-ink-1 text-sm mb-1">Density</p>
        <p className="text-xs text-ink-3 mb-3">Changes row heights only — never type size, so it stays legible on low-DPI screens.</p>
        <div className="inline-flex rounded-md bg-surface-sunken p-0.5">
          {(["comfortable", "compact"] as Density[]).map((d) => <button key={d} type="button" onClick={() => apply(d)} className={`h-8 px-3 rounded text-sm ${density === d ? "bg-surface text-ink-1 shadow-ex" : "text-ink-3"}`}>{d === "compact" ? "Compact" : "Comfortable"}</button>)}
        </div>
      </Card>
    </div>
  );
}

/* ─── General ─────────────────────────────────────────────────────────────── */

function GeneralSection({ siteUrl: _siteUrl }: { siteUrl: string }) {
  const { data: mobileRaw, mutate: refreshMobile } = useFrappeGetCall<{
    message: MobileDiscovery;
  }>("excom.excom.api.mobile.get_client_id");
  const mobile = mobileRaw?.message;

  const { call: createOAuthClient, loading: creatingOAuth } = useFrappePostCall(
    "excom.excom.api.mobile.create_oauth_client"
  );

  const copyText = (label: string, text: string) => {
    void navigator.clipboard.writeText(text);
    toast.success(`${label} copied`);
  };

  const handleCreateOAuth = async () => {
    try {
      await createOAuthClient({});
      toast.success("OAuth client ready.");
      await refreshMobile();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Could not create OAuth client");
    }
  };

  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Zap className="w-4 h-4 text-crayon-blue-text" />} title="General" />

      <Card>
        <div className="flex items-center gap-2 text-ink-1 font-medium mb-1">
          <KeyRound className="w-4 h-4 text-crayon-violet-text" />
          Mobile app OAuth (PKCE)
        </div>
        <p className="text-sm text-ink-3 mb-3">
          The native / PWA companion apps use an OAuth 2 client defined in Excom Settings.
        </p>
        <dl className="space-y-3 text-sm">
          <InfoRow label="Site URL" value={mobile?.site_url} onCopy={(v) => copyText("Site URL", v)} />
          <InfoRow label="OAuth client ID" value={mobile?.client_id || undefined} onCopy={(v) => copyText("Client ID", v)} />
          <InfoRow label="Redirect URI" value={MOBILE_REDIRECT_SCHEME} onCopy={(v) => copyText("Redirect URI", v)} />
          <InfoRow label="Site name" value={mobile?.sitename} />
        </dl>
        {mobile?.can_manage_mobile_oauth ? (
          <div className="mt-5 flex flex-wrap gap-2 items-center">
            <Button
              onClick={() => void handleCreateOAuth()}
              disabled={creatingOAuth}
              className="border border-crayon-violet-base/40 bg-crayon-violet-tint text-crayon-violet-text hover:bg-crayon-violet-tint"
            >
              {creatingOAuth ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <RefreshCw className="w-4 h-4 mr-2" />}
              Create or refresh OAuth client
            </Button>
            <a href="/app/excom-settings/Excom%20Settings" className="inline-flex items-center gap-1 text-xs text-crayon-blue-text hover:text-crayon-blue-text">
              Excom Settings <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        ) : (
          <p className="mt-4 text-xs text-ink-3 flex items-start gap-2">
            <Shield className="w-4 h-4 shrink-0 mt-0.5 text-ink-3" />
            Only System Manager or Excom Manager can create the OAuth client.
          </p>
        )}
      </Card>
    </div>
  );
}

/* ─── Email Signatures ─────────────────────────────────────────────────────── */

function SignaturesSection() {
  const { data: sigRaw, mutate: refreshSig } = useFrappeGetCall<{
    message: { exists: boolean; signature_html: string; position: string };
  }>("excom.excom.api.email.get_my_signature");

  const { call: saveSig, loading: saving } = useFrappePostCall(
    "excom.excom.api.email.save_my_signature"
  );

  const [html, setHtml] = useState("");
  const [position, setPosition] = useState("Below Reply");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (sigRaw?.message && !loaded) {
      setHtml(sigRaw.message.signature_html || "");
      setPosition(sigRaw.message.position || "Below Reply");
      setLoaded(true);
    }
  }, [sigRaw, loaded]);

  const handleSave = async () => {
    try {
      await saveSig({ signature_html: html, position });
      toast.success("Signature saved");
      await refreshSig();
    } catch {
      toast.error("Failed to save signature");
    }
  };

  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Mail className="w-4 h-4 text-crayon-green-text" />} title="Email Signatures" />
      <Card>
        <p className="text-sm text-ink-3 mb-3">
          Your signature is appended to outgoing emails. Supports HTML formatting.
        </p>
        <div className="mb-3">
          <label className="block text-xs text-ink-3 mb-1">Position</label>
          <div className="flex gap-2">
            {["Below Reply", "Below All"].map((opt) => (
              <button
                key={opt}
                onClick={() => setPosition(opt)}
                className={`px-3 py-1.5 rounded-lg text-sm border transition-colors ${
                  position === opt
                    ? "bg-crayon-blue-tint border-crayon-blue-base/40 text-crayon-blue-text"
                    : "border-border-strong text-ink-3 hover:border-border-strong hover:text-ink-1"
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
        </div>
        <div className="mb-3">
          <label className="block text-xs text-ink-3 mb-1">Signature (HTML)</label>
          <textarea
            value={html}
            onChange={(e) => setHtml(e.target.value)}
            rows={8}
            className="w-full bg-surface-sunken border border-border-strong rounded-lg px-3 py-2 text-sm text-ink-1 font-mono resize-y focus:outline-none focus:border-crayon-blue-base/40 placeholder:text-ink-3"
            placeholder="<p>Best regards,<br><strong>Your Name</strong></p>"
          />
        </div>
        {html && (
          <div className="mb-3">
            <label className="block text-xs text-ink-3 mb-1">Preview</label>
            <div
              className="border border-border-strong rounded-lg p-3 text-sm text-ink-2 bg-surface-sunken"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          </div>
        )}
        <Button
          onClick={() => void handleSave()}
          disabled={saving}
          className="bg-crayon-blue-base hover:bg-crayon-blue-text text-white"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
          Save Signature
        </Button>
      </Card>
    </div>
  );
}

/* ─── Notifications ─────────────────────────────────────────────────────────── */

function NotificationsSection() {
  const { data: pushEnabledRaw, mutate: refreshPushFlag } = useFrappeGetCall<{
    message: boolean;
  }>("excom.excom.api.notification.are_push_notifications_enabled");
  const serverPushEnabled = Boolean(pushEnabledRaw?.message);

  const [notifPerm, setNotifPerm] = useState<NotificationPermission>(
    typeof Notification !== "undefined" ? Notification.permission : "denied"
  );
  const [pushBusy, setPushBusy] = useState(false);
  const [fcmRegistered, setFcmRegistered] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState<boolean>(
    () => localStorage.getItem("excom_sound_enabled") !== "false"
  );

  const checkRelayTokenLocal = useCallback(() => {
    try {
      setFcmRegistered(typeof localStorage !== "undefined" && localStorage.getItem("firebase_token_excom") != null);
    } catch {
      setFcmRegistered(false);
    }
  }, []);

  useEffect(() => {
    setNotifPerm(typeof Notification !== "undefined" ? Notification.permission : "denied");
    checkRelayTokenLocal();
  }, [checkRelayTokenLocal]);

  const handleSoundToggle = (val: boolean) => {
    setSoundEnabled(val);
    localStorage.setItem("excom_sound_enabled", String(val));
    toast.success(val ? "Notification sound on" : "Notification sound off");
  };

  const handleEnablePush = async () => {
    setPushBusy(true);
    try {
      const helper = window.frappePushNotification;
      if (!helper) { toast.error("Push helper not loaded — refresh and try again."); return; }
      const result = await helper.enableNotification();
      setNotifPerm(Notification.permission);
      checkRelayTokenLocal();
      if (result.permission_granted) {
        toast.success("Push notifications enabled.");
        await refreshPushFlag();
      } else {
        toast.message("Notifications blocked", { description: "Allow in browser site settings." });
      }
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Could not enable push notifications");
    } finally {
      setPushBusy(false);
    }
  };

  const handleDisablePush = async () => {
    setPushBusy(true);
    try {
      await window.frappePushNotification?.disableNotification();
      checkRelayTokenLocal();
      toast.success("Unsubscribed from push on this device.");
    } catch {
      toast.error("Could not disable push on this device.");
    } finally {
      setPushBusy(false);
    }
  };

  const permLabel = notifPerm === "granted" ? "Allowed" : notifPerm === "denied" ? "Blocked" : "Not asked yet";

  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Bell className="w-4 h-4 text-crayon-amber-text" />} title="Notifications" />

      <Card>
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="font-medium text-ink-1 text-sm">Notification sound</p>
            <p className="text-xs text-ink-3 mt-0.5">Play a tone when a new message arrives, even when this tab is focused.</p>
          </div>
          <button
            onClick={() => handleSoundToggle(!soundEnabled)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm transition-colors ${
              soundEnabled
                ? "bg-crayon-green-tint border-crayon-green-base/40 text-crayon-green-text"
                : "border-border-strong text-ink-3 hover:border-border-strong"
            }`}
          >
            {soundEnabled ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
            {soundEnabled ? "On" : "Off"}
          </button>
        </div>
      </Card>

      <Card>
        <div className="flex items-center gap-2 text-ink-1 font-medium mb-1">
          <Bell className="w-4 h-4 text-crayon-amber-text" />
          Browser push notifications
        </div>
        <p className="text-sm text-ink-3 mb-3">
          Uses the Frappe Push Notification relay. Subscribe this browser to receive alerts in the background.
        </p>
        <div className="flex flex-wrap items-center gap-3 mb-3 text-sm">
          <span className="text-ink-3">Browser:</span>
          <span className={notifPerm === "granted" ? "text-crayon-green-text" : notifPerm === "denied" ? "text-crayon-rose-text" : "text-crayon-amber-text"}>
            {permLabel}
          </span>
          <span className="text-ink-3">·</span>
          <span className="text-ink-3">Server:</span>
          <span className={serverPushEnabled ? "text-crayon-green-text" : "text-ink-3"}>
            {serverPushEnabled ? "On" : "Off"}
          </span>
          <span className="text-ink-3">·</span>
          <span className="text-ink-3">This browser:</span>
          <span className={fcmRegistered ? "text-crayon-green-text" : "text-ink-3"}>
            {fcmRegistered ? "Subscribed" : "Not subscribed"}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => void handleEnablePush()}
            disabled={pushBusy || notifPerm === "denied"}
            className="bg-crayon-blue-base hover:bg-crayon-blue-text text-white"
          >
            {pushBusy ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
            Allow push notifications
          </Button>
          {fcmRegistered && (
            <Button onClick={() => void handleDisablePush()} disabled={pushBusy} className="border-border-strong text-ink-2">
              Unsubscribe this browser
            </Button>
          )}
        </div>
        {notifPerm === "denied" && (
          <p className="text-xs text-crayon-rose-text/90 mt-3">
            This site is blocked in your browser. Open site settings, allow notifications, then reload.
          </p>
        )}
      </Card>
    </div>
  );
}

/* ─── Branding ─────────────────────────────────────────────────────────────── */

function BrandingSection({ branding }: { branding: ReturnType<typeof useExcomBranding>["branding"] }) {
  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Palette className="w-4 h-4 text-crayon-plum-text" />} title="Branding" />
      <Card>
        <p className="text-sm text-ink-3 mb-3">
          Logo gradient, app name, and colours are managed in{" "}
          <a href="/app/excom-settings/Excom%20Settings" className="text-crayon-blue-text hover:text-crayon-blue-text inline-flex items-center gap-1">
            Excom Settings <ExternalLink className="w-3 h-3" />
          </a>{" "}
          on the desk.
        </p>
        <div className="flex items-center gap-3">
          <div
            className="w-14 h-14 rounded-xl shrink-0 flex items-center justify-center shadow-ex"
            style={{ background: branding?.logo_gradient_from ? `linear-gradient(to bottom right, ${branding.logo_gradient_from}, ${branding.logo_gradient_to || branding.logo_gradient_from})` : "var(--ex-blue-base)" }}
          />
          <div className="min-w-0">
            {branding?.show_app_name ? (
              <p className="text-sm text-ink-2 font-medium truncate">Header title: {branding.app_name || "Excom"}</p>
            ) : (
              <p className="text-sm text-ink-3">App name in header is hidden. Enable "Show App Name in Header" in Excom Settings.</p>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}

/* ─── Accounts ─────────────────────────────────────────────────────────────── */

interface ChannelAccountItem {
  name: string;
  channel: string;
  account_name: string;
  email_address?: string;
  wa_phone_id?: string;
}

function AccountsSection() {
  const { data: raw } = useFrappeGetCall<{ message: ChannelAccountItem[] }>(
    "excom.excom.api.chat.get_channel_accounts"
  );
  const accounts: ChannelAccountItem[] = Array.isArray(raw?.message) ? raw!.message : [];

  const CHANNEL_ACCENT: Record<string, "green" | "blue" | "plum"> = { WhatsApp: "green", Email: "blue", Instagram: "plum" };

  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<Users className="w-4 h-4 text-crayon-teal-text" />} title="Accounts" />
      <Card>
        <p className="text-sm text-ink-3 mb-3">Channel accounts you have access to.</p>
        {accounts.length === 0 ? (
          <p className="text-sm text-ink-3">No accounts available.</p>
        ) : (
          <ul className="divide-y divide-border">
            {accounts.map((acc) => (
              <li key={acc.name} className="py-2 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-ink-1">{acc.account_name}</p>
                  <p className="text-xs text-ink-3 mt-0.5">{acc.email_address || acc.wa_phone_id || acc.name}</p>
                </div>
                <Chip size="sm" accent={CHANNEL_ACCENT[acc.channel] || "neutral"} label={acc.channel} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/* ─── Keyboard Shortcuts ───────────────────────────────────────────────────── */

const SHORTCUTS = [
  { keys: [MOD, "K"], desc: "Command palette — search everything" },
  { keys: [MOD, "N"], desc: "New conversation" },
  { keys: ["/"], desc: "Focus search" },
  { keys: ["J"], desc: "Next conversation" },
  { keys: ["K"], desc: "Previous conversation" },
  { keys: ["↵"], desc: "Open highlighted conversation" },
  { keys: ["E"], desc: "Archive highlighted conversation" },
  { keys: ["A"], desc: "Assign highlighted conversation to me" },
  { keys: [MOD, "↵"], desc: "Send message" },
  { keys: [MOD, "."], desc: "Toggle details pane" },
  { keys: ["G", "I"], desc: "Go to Inbox" },
  { keys: ["G", "T"], desc: "Go to Today's actions" },
  { keys: ["G", "P"], desc: "Go to Pipeline" },
  { keys: ["G", "B"], desc: "Go to Broadcasts" },
  { keys: ["G", "A"], desc: "Go to Analytics" },
  { keys: ["G", "S"], desc: "Go to Settings" },
  { keys: ["Esc"], desc: "Close dialogs / back to list on phone" },
];

function ShortcutsSection() {
  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<KeyRound className="w-4 h-4 text-crayon-violet-text" />} title="Keyboard Shortcuts" />
      <Card>
        <p className="text-sm text-ink-3 mb-3">Available keyboard shortcuts for power users.</p>
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border">
            {SHORTCUTS.map(({ keys, desc }, i) => (
              <tr key={i} className="py-2">
                <td className="py-2.5 pr-4 w-40">
                  <div className="flex items-center gap-1">
                    {keys.map((k) => (
                      <Kbd key={k}>{k}</Kbd>
                    ))}
                  </div>
                </td>
                <td className="py-2.5 text-ink-2">{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-ink-3 mt-4">Single-key shortcuts are ignored while typing. “G then …” is a two-key chord.</p>
      </Card>
    </div>
  );
}

/* ─── Canned Responses ─────────────────────────────────────────────────────── */

interface CannedResponse {
  name: string;
  title: string;
  shortcode: string;
  content: string;
}

function CannedSection() {
  const { data: items, mutate: refresh } = useFrappeGetDocList<CannedResponse>(
    "Excom Canned Response",
    {
      fields: ["name", "title", "shortcode", "content"],
      limit: 50,
      orderBy: { field: "title", order: "asc" },
    }
  );

  const { createDoc, loading: saving } = useFrappeCreateDoc();

  const [newTitle, setNewTitle] = useState("");
  const [newShortcode, setNewShortcode] = useState("");
  const [newContent, setNewContent] = useState("");

  const handleAdd = async () => {
    if (!newTitle.trim() || !newContent.trim()) { toast.error("Title and content are required."); return; }
    const shortcode = (newShortcode.trim() || newTitle.trim()).toLowerCase().replace(/[^a-z0-9_]/g, "_");
    try {
      await createDoc("Excom Canned Response", {
        title: newTitle.trim(),
        shortcode,
        content: newContent.trim(),
      });
      setNewTitle(""); setNewShortcode(""); setNewContent("");
      toast.success("Canned response added.");
      await refresh();
    } catch {
      toast.error("Failed to save. Check if the shortcode is already taken.");
    }
  };

  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<MessageSquare className="w-4 h-4 text-crayon-violet-text" />} title="Canned Responses" />
      <Card>
        <p className="text-sm text-ink-3 mb-3">Quick-reply templates triggered by typing <code className="text-ink-2">/shortcode</code> in chat.</p>
        {(items?.length ?? 0) > 0 && (
          <ul className="divide-y divide-border mb-3">
            {items!.map((item) => (
              <li key={item.name} className="py-2.5">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-ink-1">{item.title}</p>
                  <span className="text-xs text-ink-3 font-mono">/{item.shortcode}</span>
                </div>
                <p className="text-xs text-ink-3 mt-0.5 line-clamp-2">{item.content}</p>
              </li>
            ))}
          </ul>
        )}
        <div className="border-t border-border pt-4 space-y-2">
          <p className="text-xs text-ink-3 mb-2">Add new</p>
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Title (e.g. Greeting)"
            className="w-full bg-surface-sunken border border-border-strong rounded-lg px-3 py-2 text-sm text-ink-1 focus:outline-none focus:border-crayon-blue-base/40"
          />
          <input
            value={newShortcode}
            onChange={(e) => setNewShortcode(e.target.value)}
            placeholder="Shortcode (e.g. greeting) — auto-generated from title if empty"
            className="w-full bg-surface-sunken border border-border-strong rounded-lg px-3 py-2 text-sm text-ink-1 font-mono focus:outline-none focus:border-crayon-blue-base/40"
          />
          <textarea
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            placeholder="Response content…"
            rows={3}
            className="w-full bg-surface-sunken border border-border-strong rounded-lg px-3 py-2 text-sm text-ink-1 resize-y focus:outline-none focus:border-crayon-blue-base/40"
          />
          <Button onClick={() => void handleAdd()} disabled={saving} size="sm" className="bg-crayon-blue-tint text-crayon-blue-text border border-crayon-blue-base/40 hover:bg-crayon-blue-tint">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : null}Add
          </Button>
        </div>
      </Card>
    </div>
  );
}

/* ─── Auto-Reply ───────────────────────────────────────────────────────────── */

function AutoReplySection() {
  return (
    <div className="max-w-2xl space-y-4">
      <SectionHeader icon={<RotateCcw className="w-4 h-4 text-crayon-teal-text" />} title="Auto-Reply" />
      <Card>
        <p className="text-sm text-ink-3 mb-2">
          Configure automatic replies per channel account — e.g., out-of-office messages, welcome messages.
        </p>
        <p className="text-xs text-ink-3 p-3 rounded-lg bg-surface-sunken border border-border-strong">
          Auto-reply rule management coming soon. For now, configure rules via the Frappe desk under{" "}
          <a href="/app" className="text-crayon-blue-text hover:text-crayon-blue-text">Excom modules</a>.
        </p>
      </Card>
    </div>
  );
}

/* ─── Shared helpers ───────────────────────────────────────────────────────── */

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      {children}
    </div>
  );
}

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      {icon}
      <h2 className="text-base font-semibold text-ink-1">{title}</h2>
    </div>
  );
}

function InfoRow({
  label,
  value,
  onCopy,
}: {
  label: string;
  value?: string | null;
  onCopy?: (v: string) => void;
}) {
  return (
    <div>
      <dt className="text-ink-3 text-xs mb-1">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">
        <code className="text-ink-1 bg-surface-sunken px-2 py-1 rounded break-all text-xs">
          {value || "—"}
        </code>
        {value && onCopy && (
          <Button type="button" variant="ghost" size="icon" className="h-7 w-7 text-ink-3" onClick={() => onCopy(value)}>
            <Copy className="w-3.5 h-3.5" />
          </Button>
        )}
      </dd>
    </div>
  );
}
