import { Fragment, useEffect, useRef, useState } from "react";
import { useFrappePostCall, useFrappeFileUpload } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { AlertTriangle, Ban, Copy, ImageUp, Info, Link2, MessageCircle, Plus, QrCode, RefreshCw, Save, ShieldCheck, ShoppingCart, Store, Trash2, Webhook, Zap } from "lucide-react";
import { toast } from "sonner";
import { toastError } from "../ErrorDialog";
import { Button, Chip, EmptyState, Field, Input, Select, SegmentedControl, Textarea } from "../primitives";
import { cn } from "../ui/utils";

/**
 * Admin → WhatsApp number: the settings Meta keeps per number (or per WhatsApp account), read
 * from Meta and written back to it. Nothing here is stored only locally, except the PIN (encrypted)
 * and the QR code mirror. The server checks every Meta limit again; the counters here only help.
 */

type Tab = "profile" | "blocked" | "qr" | "conversational" | "registration" | "webhook" | "commerce";

interface NumberRow { name: string; account_name?: string; status?: string; wa_display_phone?: string; wa_verified_name?: string; wa_phone_id?: string; wa_business_id?: string; wa_app_id?: string }
interface Overview { account: string; has_app_id: boolean; has_waba: boolean; verticals: string[]; regions: string[]; qr_count: number; qr_cap: number; default_webhook_url: string }

const TABS: { value: Tab; label: string }[] = [
  { value: "profile", label: "Profile" },
  { value: "blocked", label: "Blocked users" },
  { value: "qr", label: "QR codes" },
  { value: "conversational", label: "Ice breakers & commands" },
  { value: "registration", label: "Registration & PIN" },
  { value: "webhook", label: "Webhook" },
  { value: "commerce", label: "Commerce" },
];

const API = "excom.excom.api.whatsapp_account.";

/** Run a server call, toast the result, return the message (undefined on failure). */
async function attempt<T>(fn: () => Promise<{ message: T }>, ok?: string, fail = "WhatsApp did not accept this"): Promise<T | undefined> {
  try {
    const r = await fn();
    if (ok) toast.success(ok);
    return r.message;
  } catch (e) {
    toastError(e, fail);
    return undefined;
  }
}

export function WhatsAppNumberAdmin() {
  const [picked, setPicked] = useState("");
  const [tab, setTab] = useState<Tab>("profile");
  const { data: nums, isLoading } = useFrappeGetCall<{ message: NumberRow[] }>(API + "list_numbers", undefined, "wa-numbers", { revalidateOnFocus: false });
  const numbers = nums?.message ?? [];
  const account = picked || numbers.find((n) => n.status === "Active")?.name || numbers[0]?.name || "";
  const { data: ov } = useFrappeGetCall<{ message: Overview }>(account ? API + "get_overview" : null, account ? { account } : undefined, account ? `wa-overview:${account}` : null, { revalidateOnFocus: false });
  const overview = ov?.message;

  if (isLoading && !nums) return <p className="p-3 text-sm text-ink-3">Loading…</p>;
  if (!numbers.length) {
    return <div className="p-3"><EmptyState icon={<MessageCircle />} title="No WhatsApp number yet" hint="Connect one under Meta Business, or add an Excom Channel Account with channel 'whatsapp'." /></div>;
  }
  const current = numbers.find((n) => n.name === account);
  return (
    <div className="flex flex-col min-h-0 h-full">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-border flex-wrap">
        <Select value={account} onChange={(e) => setPicked(e.target.value)} className="max-w-xs" aria-label="WhatsApp number">
          {numbers.map((n) => <option key={n.name} value={n.name}>{n.account_name || n.name}{n.wa_display_phone ? ` · ${n.wa_display_phone}` : ""}</option>)}
        </Select>
        {current?.wa_verified_name && <span className="text-xs text-ink-2 truncate">{current.wa_verified_name}</span>}
        {current?.status && <Chip size="sm" accent={current.status === "Active" ? "green" : "neutral"} label={current.status} />}
        <span className="text-xs text-ink-3 truncate">phone id {current?.wa_phone_id || "—"} · WABA {current?.wa_business_id || "—"}</span>
      </div>
      <SegmentedControl value={tab} onChange={setTab} segments={TABS} className="px-3 border-b border-border overflow-x-auto" ariaLabel="WhatsApp number settings" />
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        <div className="max-w-3xl space-y-4" key={`${account}:${tab}`}>
          {tab === "profile" && <ProfileTab account={account} overview={overview} />}
          {tab === "blocked" && <BlockedTab account={account} />}
          {tab === "qr" && <QrTab account={account} />}
          {tab === "conversational" && <ConversationalTab account={account} />}
          {tab === "registration" && <RegistrationTab account={account} />}
          {tab === "webhook" && <WebhookTab account={account} overview={overview} />}
          {tab === "commerce" && <CommerceTab account={account} overview={overview} />}
        </div>
      </div>
    </div>
  );
}

function Panel({ title, icon, actions, children }: { title: string; icon?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border min-w-0">
      <header className="flex items-center gap-2 px-3 h-9 border-b border-border bg-surface-sunken min-w-0 [&>svg]:size-4 [&>svg]:text-ink-3">
        {icon}
        <span className="text-sm text-ink-1 flex-1 truncate">{title}</span>
        {actions}
      </header>
      <div className="p-3 space-y-3">{children}</div>
    </section>
  );
}

function Note({ tone = "info", children }: { tone?: "info" | "warn"; children: React.ReactNode }) {
  return (
    <p className={cn("flex items-start gap-1.5 rounded-md border px-2.5 py-1.5 text-xs", tone === "warn" ? "border-crayon-amber-base/40 bg-crayon-amber-tint text-crayon-amber-text" : "border-border bg-surface-sunken text-ink-2")}>
      {tone === "warn" ? <AlertTriangle className="size-3.5 mt-0.5 shrink-0" /> : <Info className="size-3.5 mt-0.5 shrink-0" />}
      <span>{children}</span>
    </p>
  );
}

function Count({ value, max }: { value: string; max: number }) {
  return <span className={cn("tabular-nums", value.length > max && "text-crayon-rose-text")}>{value.length}/{max}</span>;
}

function CopyButton({ text }: { text: string }) {
  return <Button size="icon-sm" variant="ghost" aria-label="Copy" onClick={() => navigator.clipboard?.writeText(text).then(() => toast.success("Copied"), () => toast.error("Could not copy"))}><Copy /></Button>;
}

// ─── Profile ─────────────────────────────────────────────────────────────────

interface Profile { about?: string; address?: string; description?: string; email?: string; websites?: string[]; vertical?: string; profile_picture_url?: string }
const PROFILE_TEXT: { key: "about" | "address" | "description" | "email"; label: string; max: number; long?: boolean }[] = [
  { key: "about", label: "About", max: 139 },
  { key: "address", label: "Address", max: 256, long: true },
  { key: "description", label: "Description", max: 512, long: true },
  { key: "email", label: "Email", max: 128 },
];

function ProfileTab({ account, overview }: { account: string; overview?: Overview }) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: Profile }>(API + "get_profile", { account }, `wa-profile:${account}`, { revalidateOnFocus: false });
  const { call: save, loading: saving } = useFrappePostCall(API + "update_profile");
  const { call: setPhoto, loading: settingPhoto } = useFrappePostCall(API + "set_profile_photo");
  const { upload, loading: uploading } = useFrappeFileUpload();
  const fileRef = useRef<HTMLInputElement>(null);
  const loaded = data?.message;
  const [form, setForm] = useState<Profile>({});
  useEffect(() => { if (loaded) setForm({ ...loaded, websites: [...(loaded.websites ?? []), "", ""].slice(0, 2) }); }, [loaded]);

  const changed = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const f of PROFILE_TEXT) if ((form[f.key] ?? "") !== (loaded?.[f.key] ?? "")) out[f.key] = form[f.key] ?? "";
    const sites = (form.websites ?? []).map((w) => w.trim()).filter(Boolean);
    if (JSON.stringify(sites) !== JSON.stringify(loaded?.websites ?? [])) out.websites = sites;
    if ((form.vertical ?? "") !== (loaded?.vertical ?? "")) out.vertical = form.vertical ?? "";
    return out;
  };

  const onSave = async () => {
    const values = changed();
    if (!Object.keys(values).length) { toast.message("Nothing changed"); return; }
    if (await attempt(() => save({ account, values: JSON.stringify(values) }), "Profile updated on WhatsApp")) void mutate();
  };

  const onPhoto = async (file: File) => {
    if (!["image/jpeg", "image/png"].includes(file.type)) { toast.error("Use a JPEG or PNG image"); return; }
    try {
      const up = await upload(file, { isPrivate: true, doctype: "Excom Channel Account", docname: account });
      if (await attempt(() => setPhoto({ account, file_url: up.file_url }), "Profile photo updated")) void mutate();
    } catch (e) { toastError(e, "The photo could not be uploaded"); }
  };

  if (isLoading && !data) return <p className="text-sm text-ink-3">Reading the profile from WhatsApp…</p>;
  return (
    <>
      <Panel title="Profile photo" icon={<ImageUp />}>
        <div className="flex items-center gap-3">
          {loaded?.profile_picture_url ? <img src={loaded.profile_picture_url} alt="WhatsApp profile" className="size-16 rounded-full object-cover border border-border" /> : <div className="size-16 rounded-full bg-surface-sunken border border-border" />}
          <div className="space-y-1">
            <input ref={fileRef} type="file" accept="image/jpeg,image/png" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void onPhoto(f); e.target.value = ""; }} />
            <Button size="sm" variant="default" disabled={uploading || settingPhoto || !overview?.has_app_id} onClick={() => fileRef.current?.click()}><ImageUp className={uploading || settingPhoto ? "animate-pulse" : ""} />Upload new photo</Button>
            <p className="text-xs text-ink-3">JPEG or PNG, square works best. Sent through Meta's upload API.</p>
          </div>
        </div>
        {overview && !overview.has_app_id && <Note tone="warn">Set the App ID on this channel account first: Meta's upload API runs on the app.</Note>}
      </Panel>
      <Panel title="Business profile" icon={<Store />} actions={<Button size="sm" variant="primary" disabled={saving} onClick={onSave}><Save />Save to WhatsApp</Button>}>
        {PROFILE_TEXT.map((f) => (
          <Field key={f.key} label={<span className="flex justify-between gap-2"><span>{f.label}</span><Count value={form[f.key] ?? ""} max={f.max} /></span>}>
            {f.long ? <Textarea value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} /> : <Input value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />}
          </Field>
        ))}
        {[0, 1].map((i) => (
          <Field key={i} label={`Website ${i + 1}`} hint={i === 1 ? "At most two, each with http:// or https://." : undefined}>
            <Input value={form.websites?.[i] ?? ""} placeholder="https://" onChange={(e) => { const w = [...(form.websites ?? ["", ""])]; w[i] = e.target.value; setForm({ ...form, websites: w }); }} />
          </Field>
        ))}
        <Field label="Category">
          <Select value={form.vertical ?? ""} onChange={(e) => setForm({ ...form, vertical: e.target.value })}>
            <option value="">—</option>
            {(overview?.verticals ?? []).map((v) => <option key={v} value={v}>{v.replace(/_/g, " ").toLowerCase()}</option>)}
          </Select>
        </Field>
        <p className="text-xs text-ink-3">Only the fields you change are sent. About is plain text; links in it are not clickable.</p>
      </Panel>
    </>
  );
}

// ─── Blocked users ───────────────────────────────────────────────────────────

interface BlockedRow { wa_id: string; user_id: string; identity: string; name: string }

function BlockedTab({ account }: { account: string }) {
  const [after, setAfter] = useState("");
  const [rows, setRows] = useState<BlockedRow[]>([]);
  const [user, setUser] = useState("");
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: { data: BlockedRow[]; after: string } }>(API + "list_blocked", { account, after }, `wa-blocked:${account}:${after}`, { revalidateOnFocus: false });
  useEffect(() => { if (data?.message) setRows((prev) => (after ? [...prev, ...data.message.data] : data.message.data)); }, [data, after]);
  const { call: blockCall, loading: blocking } = useFrappePostCall(API + "block");
  const { call: unblockCall } = useFrappePostCall(API + "unblock");
  const reload = () => { setAfter(""); void mutate(); };
  return (
    <>
      <Note>WhatsApp only lets you block someone who messaged this number in the last 24 hours. A blocked person cannot message or call this number, and you cannot message them. Agents can also block from a conversation's ⋯ menu.</Note>
      <Panel title="Block someone" icon={<Ban />}>
        <div className="flex gap-2 flex-wrap">
          <Input value={user} onChange={(e) => setUser(e.target.value)} placeholder="WhatsApp number (+91…) or user id (IN.…)" className="flex-1 min-w-[240px]" />
          <Button size="md" variant="danger" disabled={!user.trim() || blocking} onClick={async () => { if (await attempt(() => blockCall({ account, user: user.trim() }), "Blocked on WhatsApp", "Could not block")) { setUser(""); reload(); } }}>Block</Button>
          <Button size="md" variant="default" disabled={!user.trim()} onClick={async () => { if (await attempt(() => unblockCall({ account, user: user.trim() }), "Unblocked", "Could not unblock")) { setUser(""); reload(); } }}>Unblock</Button>
        </div>
      </Panel>
      <Panel title="Blocked on this number" icon={<ShieldCheck />} actions={<Button size="sm" variant="ghost" onClick={reload}><RefreshCw className={isLoading ? "animate-spin" : ""} />Refresh</Button>}>
        {rows.length === 0 ? <p className="text-sm text-ink-3">{isLoading ? "Reading the block list…" : "Nobody is blocked."}</p> : (
          <ul className="divide-y divide-border -mx-3">
            {rows.map((r) => (
              <li key={r.user_id || r.wa_id} className="flex items-center gap-2 px-3 py-1.5 min-w-0">
                <div className="flex-1 min-w-0"><div className="text-sm text-ink-1 truncate">{r.name || r.wa_id || r.user_id}</div><div className="text-xs text-ink-3 truncate">{[r.wa_id && `+${r.wa_id}`, r.user_id].filter(Boolean).join(" · ")}</div></div>
                <Button size="sm" variant="ghost" onClick={async () => { if (await attempt(() => unblockCall({ account, user: r.user_id || r.wa_id }), "Unblocked", "Could not unblock")) reload(); }}>Unblock</Button>
              </li>
            ))}
          </ul>
        )}
        {data?.message?.after && <Button size="sm" variant="ghost" onClick={() => setAfter(data.message.after)}>Load more</Button>}
      </Panel>
    </>
  );
}

// ─── QR codes ────────────────────────────────────────────────────────────────

interface QrRow { name: string; code: string; prefilled_message: string; deep_link_url: string; image_format: string; qr_image?: string; qr_image_url?: string; last_synced_on?: string }

function QrTab({ account }: { account: string }) {
  const { data, mutate } = useFrappeGetCall<{ message: { data: QrRow[]; count: number; cap: number } }>(API + "list_qr_codes", { account }, `wa-qr:${account}`, { revalidateOnFocus: false });
  const { call: create, loading: creating } = useFrappePostCall(API + "create_qr_code");
  const { call: sync, loading: syncing } = useFrappePostCall(API + "sync_qr_codes");
  const [message, setMessage] = useState("");
  const [format, setFormat] = useState("PNG");
  const rows = data?.message?.data ?? [];
  const cap = data?.message?.cap ?? 2000;
  return (
    <>
      <Panel title="New QR code / short link" icon={<QrCode />} actions={<span className="text-xs text-ink-3 tabular-nums">{rows.length} of {cap}</span>}>
        <Field label={<span className="flex justify-between gap-2"><span>Prefilled message</span><Count value={message} max={140} /></span>} hint="What the person's WhatsApp types for them when they scan the code or open the link.">
          <Textarea value={message} onChange={(e) => setMessage(e.target.value)} />
        </Field>
        <div className="flex gap-2 items-end">
          <Field label="Image" className="w-32"><Select value={format} onChange={(e) => setFormat(e.target.value)}><option>PNG</option><option>SVG</option></Select></Field>
          <Button size="md" variant="primary" disabled={!message.trim() || message.length > 140 || creating || rows.length >= cap} onClick={async () => { if (await attempt(() => create({ account, prefilled_message: message.trim(), image_format: format }), "QR code created")) { setMessage(""); void mutate(); } }}><Plus />Create</Button>
        </div>
      </Panel>
      <Panel title="QR codes on this number" icon={<Link2 />} actions={<Button size="sm" variant="default" disabled={syncing} onClick={async () => { const r = await attempt<{ created: number; updated: number; removed: number }>(() => sync({ account })); if (r) { toast.success(`Synced: ${r.created} new, ${r.updated} changed, ${r.removed} removed`); void mutate(); } }}><RefreshCw className={syncing ? "animate-spin" : ""} />Sync from Meta</Button>}>
        {rows.length === 0 ? <p className="text-sm text-ink-3">No QR codes yet. Codes made in WhatsApp Manager appear after a sync.</p> : (
          <ul className="divide-y divide-border -mx-3">{rows.map((r) => <QrItem key={r.name} row={r} onChange={() => void mutate()} />)}</ul>
        )}
        <p className="text-xs text-ink-3">Codes never expire. A deleted code shows "This QR code has expired" to whoever scans it.</p>
      </Panel>
    </>
  );
}

function QrItem({ row, onChange }: { row: QrRow; onChange: () => void }) {
  const [text, setText] = useState(row.prefilled_message);
  const { call: update, loading: saving } = useFrappePostCall(API + "update_qr_code");
  const { call: remove } = useFrappePostCall(API + "delete_qr_code");
  const { call: refresh, loading: refreshing } = useFrappePostCall(API + "refresh_qr_image");
  const image = row.qr_image || "";
  return (
    <li className="flex gap-3 px-3 py-2 min-w-0">
      {image ? <a href={image} target="_blank" rel="noreferrer" className="shrink-0"><img src={image} alt={`QR ${row.code}`} className="size-20 border border-border rounded bg-white" /></a> : <div className="size-20 shrink-0 rounded border border-dashed border-border flex items-center justify-center text-ink-3"><QrCode className="size-6" /></div>}
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-1 min-w-0">
          <code className="text-xs text-ink-1 truncate">{row.deep_link_url || row.code}</code>
          {row.deep_link_url && <CopyButton text={row.deep_link_url} />}
          <span className="flex-1" />
          <Button size="sm" variant="ghost" disabled={refreshing} onClick={async () => { if (await attempt(() => refresh({ name: row.name }), "Image refreshed")) onChange(); }}><RefreshCw className={refreshing ? "animate-spin" : ""} />Image</Button>
          <Button size="icon-sm" variant="ghost" aria-label="Delete" onClick={async () => { if (!window.confirm("Delete this QR code on WhatsApp? Printed copies will stop working.")) return; if (await attempt(() => remove({ name: row.name }), "Deleted")) onChange(); }}><Trash2 /></Button>
        </div>
        <div className="flex gap-2 items-start">
          <Input value={text} onChange={(e) => setText(e.target.value)} className="flex-1" />
          {text !== row.prefilled_message && <Button size="md" variant="primary" disabled={saving || text.length > 140 || !text.trim()} onClick={async () => { if (await attempt(() => update({ name: row.name, prefilled_message: text.trim() }), "Message updated")) onChange(); }}><Save />Save</Button>}
        </div>
        <p className="text-xs text-ink-3"><Count value={text} max={140} /> · {row.code}{row.image_format ? ` · ${row.image_format}` : ""}</p>
      </div>
    </li>
  );
}

// ─── Ice breakers, commands, welcome ─────────────────────────────────────────

interface Command { command_name: string; command_description: string }

function ConversationalTab({ account }: { account: string }) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: { prompts: string[]; commands: Command[]; welcome_message: string; enable_welcome_message?: boolean } }>(API + "get_conversational", { account }, `wa-conv:${account}`, { revalidateOnFocus: false });
  const { call: save, loading: saving } = useFrappePostCall(API + "set_conversational");
  const [prompts, setPrompts] = useState<string[]>(["", "", "", ""]);
  const [commands, setCommands] = useState<Command[]>([]);
  const [welcome, setWelcome] = useState("");
  useEffect(() => {
    const m = data?.message;
    if (!m) return;
    setPrompts([...(m.prompts ?? []), "", "", "", ""].slice(0, 4));
    setCommands(m.commands ?? []);
    setWelcome(m.welcome_message ?? "");
  }, [data]);
  const onSave = async () => {
    const r = await attempt<{ warning?: string }>(() => save({ account, prompts: JSON.stringify(prompts.map((p) => p.trim()).filter(Boolean)), commands: JSON.stringify(commands), welcome_message: welcome }));
    if (r) { if (r.warning) toast.warning(r.warning); else toast.success("Saved on WhatsApp"); void mutate(); }
  };
  if (isLoading && !data) return <p className="text-sm text-ink-3">Reading from WhatsApp…</p>;
  return (
    <>
      <Panel title="Ice breakers" icon={<Zap />} actions={<Button size="sm" variant="primary" disabled={saving} onClick={onSave}><Save />Save all</Button>}>
        <p className="text-xs text-ink-3">Up to 4 tappable openers shown when someone opens a chat with you for the first time. At most 80 characters each, no emoji. A tap arrives as an ordinary text message.</p>
        {prompts.map((p, i) => (
          <Field key={i} label={<span className="flex justify-between gap-2"><span>Ice breaker {i + 1}</span><Count value={p} max={80} /></span>}>
            <Input value={p} onChange={(e) => setPrompts(prompts.map((x, j) => (j === i ? e.target.value : x)))} />
          </Field>
        ))}
      </Panel>
      <Panel title="Commands" icon={<MessageCircle />} actions={<Button size="sm" variant="ghost" disabled={commands.length >= 30} onClick={() => setCommands([...commands, { command_name: "", command_description: "" }])}><Plus />Add</Button>}>
        <p className="text-xs text-ink-3">Up to 30 commands people can type after "/". Name at most 32 characters (no spaces, no "/"), description at most 256. Other apps receive them as inbound events of kind "command".</p>
        {commands.length === 0 && <p className="text-sm text-ink-3">No commands.</p>}
        {commands.map((c, i) => (
          <div key={i} className="flex gap-2 items-start">
            <Input value={c.command_name} placeholder="name" className="w-40" onChange={(e) => setCommands(commands.map((x, j) => (j === i ? { ...x, command_name: e.target.value.replace(/^\//, "") } : x)))} />
            <Input value={c.command_description} placeholder="What it does" className="flex-1" onChange={(e) => setCommands(commands.map((x, j) => (j === i ? { ...x, command_description: e.target.value } : x)))} />
            <Button size="icon" variant="ghost" aria-label="Remove command" onClick={() => setCommands(commands.filter((_, j) => j !== i))}><Trash2 /></Button>
          </div>
        ))}
      </Panel>
      <Panel title="Welcome message events" icon={<Info />}>
        <Field label="enable_welcome_message" hint="Sent to Meta only when On or Off. When on, Meta may send a 'request_welcome' event when someone opens a chat for the first time; excom stores it in the inbox and passes it to other apps as kind 'welcome'.">
          <Select value={welcome} onChange={(e) => setWelcome(e.target.value)} className="max-w-[200px]"><option value="">Not sent</option><option>On</option><option>Off</option></Select>
        </Field>
        <Note tone="warn">This setting is no longer in Meta's documentation. Meta may ignore it; if Meta refuses it, the ice breakers and commands are saved without it.</Note>
      </Panel>
    </>
  );
}

// ─── Registration and PIN ────────────────────────────────────────────────────

interface Attempt { at: string; action: string; ok: boolean; code?: number }
interface Registration { status?: string; code_verification_status?: string; platform_type?: string; name_status?: string; verified_name?: string; display_phone_number?: string; has_pin: boolean; data_localization_region: string; regions: string[]; budget: { used: number; left: number; limit: number; free_at: string; attempts: Attempt[] } }

function RegistrationTab({ account }: { account: string }) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: Registration }>(API + "get_registration", { account }, `wa-reg:${account}`, { revalidateOnFocus: false });
  const { call: setPin, loading: settingPin } = useFrappePostCall(API + "set_pin");
  const { call: register, loading: registering } = useFrappePostCall(API + "register");
  const { call: deregister, loading: deregistering } = useFrappePostCall(API + "deregister");
  const { call: requestCode, loading: requesting } = useFrappePostCall(API + "request_code");
  const { call: verify, loading: verifying } = useFrappePostCall(API + "verify_code");
  const [pin, setPinValue] = useState("");
  const [region, setRegion] = useState<string | null>(null);
  const [method, setMethod] = useState("SMS");
  const [language, setLanguage] = useState("en_US");
  const [code, setCode] = useState("");
  const r = data?.message;
  const regionValue = region ?? r?.data_localization_region ?? "";
  const budget = r?.budget;
  if (isLoading && !data) return <p className="text-sm text-ink-3">Reading the number's status from WhatsApp…</p>;
  return (
    <>
      <Panel title="Number status" icon={<ShieldCheck />} actions={<Button size="sm" variant="ghost" onClick={() => void mutate()}><RefreshCw />Refresh</Button>}>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          <dt className="text-ink-3">Status</dt><dd className="text-ink-1">{r?.status || "—"}</dd>
          <dt className="text-ink-3">Code verification</dt><dd className="text-ink-1">{r?.code_verification_status || "—"}</dd>
          <dt className="text-ink-3">Platform</dt><dd className="text-ink-1">{r?.platform_type || "—"}</dd>
          <dt className="text-ink-3">Display name</dt><dd className="text-ink-1">{r?.verified_name || "—"}{r?.name_status ? ` (${r.name_status.toLowerCase().replace(/_/g, " ")})` : ""}</dd>
          <dt className="text-ink-3">Register / deregister (72 h)</dt><dd className={cn("text-ink-1 tabular-nums", budget && budget.left <= 2 && "text-crayon-rose-text")}>{budget ? `${budget.used} of ${budget.limit} used` : "—"}{budget?.free_at ? ` · next allowed after ${budget.free_at.slice(0, 16)}` : ""}</dd>
        </dl>
        {budget && budget.left <= 3 && <Note tone="warn">Meta allows {budget.limit} register / deregister requests per number in 72 hours. Going over blocks the number for 72 hours (error 133016). {budget.left} left.</Note>}
      </Panel>
      <Panel title="Two-step verification PIN" icon={<ShieldCheck />}>
        <Note>Meta is phasing out the PIN check at registration, gradually, from 2026-09-23 (eligible numbers no longer validate it, and the two-step tab disappears in WhatsApp Manager). Meta still asks for a valid 6-digit PIN with Register until it announces the API change, so keep one here. There is no API to turn the PIN off.</Note>
        <div className="flex gap-2 items-end flex-wrap">
          <Field label="New 6-digit PIN" hint={r?.has_pin ? "A PIN is stored (encrypted) for registering." : "No PIN stored yet."}><Input type="password" inputMode="numeric" autoComplete="new-password" maxLength={6} value={pin} onChange={(e) => setPinValue(e.target.value.replace(/\D/g, ""))} className="w-40" /></Field>
          <Button size="md" variant="primary" disabled={pin.length !== 6 || settingPin} onClick={async () => { if (await attempt(() => setPin({ account, pin }), "PIN set on WhatsApp")) { setPinValue(""); void mutate(); } }}>Set PIN</Button>
        </div>
      </Panel>
      <Panel title="Register / deregister" icon={<Zap />}>
        <div className="flex gap-2 items-end flex-wrap">
          <Field label="Data localisation region (optional)">
            <Select value={regionValue} onChange={(e) => setRegion(e.target.value)} className="w-44"><option value="">None</option>{(r?.regions ?? []).map((x) => <option key={x}>{x}</option>)}</Select>
          </Field>
          <Button size="md" variant="primary" disabled={registering || !budget?.left} onClick={async () => { if (await attempt(() => register({ account, region: regionValue }), "Number registered")) void mutate(); }}>Register</Button>
          <Button size="md" variant="danger" disabled={deregistering || !budget?.left} onClick={async () => { if (!window.confirm("Deregister this number? It stops sending and receiving on the Cloud API until registered again.")) return; if (await attempt(() => deregister({ account }), "Number deregistered")) void mutate(); }}>Deregister</Button>
        </div>
        <p className="text-xs text-ink-3">Register uses the stored PIN. Register again after a display-name change is approved. To change the region: deregister, then register with the new one. A number shared with the WhatsApp Business app (coexistence) cannot be deregistered.</p>
        {(budget?.attempts ?? []).length > 0 && (
          <ul className="text-xs text-ink-2 space-y-0.5">{budget!.attempts.slice().reverse().map((a, i) => <li key={i} className="tabular-nums">{a.at.slice(0, 16)} · {a.action} · {a.ok ? "ok" : `refused${a.code ? ` (${a.code})` : ""}`}</li>)}</ul>
        )}
      </Panel>
      <Panel title="Verify the number" icon={<MessageCircle />}>
        <div className="flex gap-2 items-end flex-wrap">
          <Field label="Send code by"><Select value={method} onChange={(e) => setMethod(e.target.value)} className="w-28"><option>SMS</option><option>VOICE</option></Select></Field>
          <Field label="Language"><Input value={language} onChange={(e) => setLanguage(e.target.value)} className="w-28" /></Field>
          <Button size="md" variant="default" disabled={requesting} onClick={() => void attempt(() => requestCode({ account, code_method: method, language }), "Code sent")}>Request code</Button>
        </div>
        <div className="flex gap-2 items-end flex-wrap">
          <Field label="6-digit code"><Input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} className="w-32" /></Field>
          <Button size="md" variant="primary" disabled={code.length !== 6 || verifying} onClick={async () => { if (await attempt(() => verify({ account, code }), "Number verified")) { setCode(""); void mutate(); } }}>Verify</Button>
        </div>
        <p className="text-xs text-ink-3">Codes are single use and last about 10 minutes. An already verified number needs no code.</p>
      </Panel>
    </>
  );
}

// ─── Webhook ─────────────────────────────────────────────────────────────────

interface WebhookCfg { phone_number: string; whatsapp_business_account: string; application: string; subscribed_apps: { id: string; name: string; override_callback_uri: string }[]; waba_error: string; default_url: string }

function WebhookTab({ account, overview }: { account: string; overview?: Overview }) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: WebhookCfg }>(API + "get_webhook", { account }, `wa-hook:${account}`, { revalidateOnFocus: false });
  const cfg = data?.message;
  if (isLoading && !data) return <p className="text-sm text-ink-3">Reading the webhook configuration from WhatsApp…</p>;
  return (
    <>
      <Note>Meta sends a number's messages, statuses and calls to the first URL set: the number's override, then the WhatsApp account's, then the app's callback URL. Template, account and review events always go to the app's URL. Meta checks a new URL with its verify token before saving it, so the URL must answer that check.</Note>
      <Panel title="Where events go now" icon={<Webhook />} actions={<Button size="sm" variant="ghost" onClick={() => void mutate()}><RefreshCw />Refresh</Button>}>
        <dl className="grid gap-x-4 gap-y-1 text-sm [grid-template-columns:max-content_1fr]">
          {([["This number", cfg?.phone_number], ["WhatsApp account", cfg?.whatsapp_business_account], ["App", cfg?.application]] as [string, string | undefined][]).map(([k, v]) => (
            <Fragment key={k}><dt className="text-ink-3">{k}</dt><dd className="min-w-0 text-ink-1 truncate"><code className="text-xs">{v || "—"}</code></dd></Fragment>
          ))}
        </dl>
        {cfg?.waba_error && <Note tone="warn">WhatsApp account subscription could not be read: {cfg.waba_error}</Note>}
      </Panel>
      <OverrideForm account={account} level="number" title="Override for this number" current={cfg?.phone_number || ""} defaultUrl={cfg?.default_url || overview?.default_webhook_url || ""} onDone={() => void mutate()} />
      <OverrideForm account={account} level="waba" title="Override for the whole WhatsApp account" current={cfg?.whatsapp_business_account || ""} defaultUrl={cfg?.default_url || overview?.default_webhook_url || ""} onDone={() => void mutate()} disabled={!overview?.has_waba}
        help="Applies to every number of this WhatsApp Business Account that has no override of its own. The app must already be subscribed to the account. Clearing re-subscribes the app with no override." />
    </>
  );
}

function OverrideForm({ account, level, title, current, defaultUrl, onDone, disabled, help }: { account: string; level: "number" | "waba"; title: string; current: string; defaultUrl: string; onDone: () => void; disabled?: boolean; help?: string }) {
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const { call: setHook, loading: setting } = useFrappePostCall(API + "set_webhook");
  const { call: clearHook, loading: clearing } = useFrappePostCall(API + "clear_webhook");
  const target = url.trim() || defaultUrl;
  const foreign = Boolean(target) && target !== defaultUrl;
  return (
    <Panel title={title} icon={<Webhook />}>
      {help && <p className="text-xs text-ink-3">{help}</p>}
      <Field label="Callback URL" hint={`Leave empty for this site: ${defaultUrl}. At most 200 characters, https only.`}>
        <Input value={url} placeholder={defaultUrl} onChange={(e) => setUrl(e.target.value)} />
      </Field>
      {foreign && <Field label="Verify token" hint="The token that URL expects; Meta checks it before saving."><Input value={token} onChange={(e) => setToken(e.target.value)} /></Field>}
      <div className="flex gap-2">
        <Button size="md" variant="primary" disabled={disabled || setting || (foreign && !token.trim())} onClick={async () => { if (await attempt(() => setHook({ account, level, url: url.trim(), verify_token: token.trim() }), "Webhook override set")) { setUrl(""); setToken(""); onDone(); } }}>Set override</Button>
        <Button size="md" variant="default" disabled={disabled || clearing || (level === "number" && !current)} onClick={async () => { if (!window.confirm("Remove this override? Events fall back to the next level.")) return; if (await attempt(() => clearHook({ account, level }), "Override removed")) onDone(); }}>Clear</Button>
      </div>
    </Panel>
  );
}

// ─── Commerce ────────────────────────────────────────────────────────────────

function CommerceTab({ account, overview }: { account: string; overview?: Overview }) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: { is_cart_enabled: boolean; is_catalog_visible: boolean; catalog_id: string } }>(API + "get_commerce", { account }, `wa-commerce:${account}`, { revalidateOnFocus: false });
  const { data: cats, mutate: mutateCats } = useFrappeGetCall<{ message: { id: string; name: string }[] }>(overview?.has_waba ? API + "list_catalogs" : null, overview?.has_waba ? { account } : undefined, overview?.has_waba ? `wa-catalogs:${account}` : null, { revalidateOnFocus: false });
  const { call: save, loading: saving } = useFrappePostCall(API + "set_commerce");
  const { call: link, loading: linking } = useFrappePostCall(API + "link_catalog");
  const [catalogId, setCatalogId] = useState("");
  const s = data?.message;
  const toggle = async (key: "is_cart_enabled" | "is_catalog_visible", value: boolean) => {
    if (await attempt(() => save({ account, [key]: value ? 1 : 0 }), "Saved on WhatsApp")) void mutate();
  };
  if (isLoading && !data) return <p className="text-sm text-ink-3">Reading commerce settings from WhatsApp…</p>;
  return (
    <>
      <Panel title="Commerce settings" icon={<ShoppingCart />}>
        <label className="flex items-center gap-2 text-sm text-ink-1"><input type="checkbox" className="size-4" disabled={saving} checked={Boolean(s?.is_cart_enabled)} onChange={(e) => void toggle("is_cart_enabled", e.target.checked)} />Cart enabled</label>
        <label className="flex items-center gap-2 text-sm text-ink-1"><input type="checkbox" className="size-4" disabled={saving} checked={Boolean(s?.is_catalog_visible)} onChange={(e) => void toggle("is_catalog_visible", e.target.checked)} />Catalogue visible (shop icon and "View catalog")</label>
        <p className="text-xs text-ink-3">Meta's defaults: cart on, catalogue hidden. While hidden, wa.me catalogue links and the View catalog button show "Invalid catalog link".</p>
      </Panel>
      <Panel title="Linked catalogue" icon={<Store />} actions={overview?.has_waba ? <Button size="sm" variant="ghost" onClick={() => void mutateCats()}><RefreshCw />Refresh</Button> : undefined}>
        {!overview?.has_waba ? <Note tone="warn">Set the WhatsApp Business Account ID on this channel account first.</Note> : (
          <>
            {(cats?.message ?? []).length === 0 ? <p className="text-sm text-ink-3">No catalogue linked to this WhatsApp account.</p> : (
              <ul className="text-sm space-y-1">{cats!.message.map((c) => <li key={c.id} className="flex items-center gap-2"><span className="text-ink-1">{c.name || c.id}</span><code className="text-xs text-ink-3">{c.id}</code>{c.id === s?.catalog_id && <Chip size="sm" accent="green" label="Used for catalogue sync" />}</li>)}</ul>
            )}
            <div className="flex gap-2 items-end flex-wrap">
              <Field label="Catalogue ID (Commerce Manager)"><Input value={catalogId} onChange={(e) => setCatalogId(e.target.value.replace(/\D/g, ""))} className="w-56" /></Field>
              <Button size="md" variant="primary" disabled={!catalogId || linking} onClick={async () => { if (await attempt(() => link({ account, catalog_id: catalogId }), "Catalogue linked")) { setCatalogId(""); void mutateCats(); void mutate(); } }}>Link</Button>
            </div>
            <Note>One catalogue per WhatsApp account, shared by all its numbers. Meta's API cannot unlink a catalogue: do that in WhatsApp Manager or Commerce Manager. Catalogue sync settings (price list, product URL, brand) are on the channel account.</Note>
          </>
        )}
      </Panel>
    </>
  );
}
