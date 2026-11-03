import { useCallback, useEffect, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { BookOpen, ChevronLeft, Loader2, Plus, Search } from "lucide-react";
import { toast } from "sonner";
import { Button, Chip, EmptyState, Field, Input, Select, Sheet } from "../primitives";
import { serverMessage } from "./util";

/**
 * Meta's template library: pre-written utility templates (orders, delivery, payments, account
 * updates). Created unchanged they are approved at once. Search → pick → fill the button inputs
 * → create. The server posts it to Meta once and stores it without posting again.
 */

interface LibButton { type: string; text?: string; url?: string; phone_number?: string }
interface LibInput { index: number; type: string; text: string; default?: string }
interface LibItem {
  id: string; name: string; language: string; category: string; topic?: string; usecase?: string; industry: string[];
  header: string; body: string; body_params: string[]; buttons: LibButton[]; button_inputs: LibInput[];
}

const TOPICS = ["ACCOUNT_UPDATE", "CUSTOMER_FEEDBACK", "ORDER_MANAGEMENT", "PAYMENTS"];
const INDUSTRIES = ["E_COMMERCE", "FINANCIAL_SERVICES"];
const USECASES = [
  "ACCOUNT_CREATION_CONFIRMATION", "AUTO_PAY_REMINDER", "DELIVERY_CONFIRMATION", "DELIVERY_FAILED", "DELIVERY_UPDATE",
  "FEEDBACK_SURVEY", "FRAUD_ALERT", "LOW_BALANCE_WARNING", "ORDER_ACTION_NEEDED", "ORDER_CONFIRMATION", "ORDER_DELAY",
  "ORDER_OR_TRANSACTION_CANCEL", "ORDER_PICK_UP", "PAYMENT_ACTION_REQUIRED", "PAYMENT_CONFIRMATION", "PAYMENT_DUE_REMINDER",
  "PAYMENT_OVERDUE", "PAYMENT_REJECT_FAIL", "PAYMENT_SCHEDULED", "RECEIPT_ATTACHMENT", "RETURN_CONFIRMATION",
  "SHIPMENT_CONFIRMATION", "STATEMENT_ATTACHMENT", "STATEMENT_AVAILABLE", "TRANSACTION_ALERT",
];
const nice = (s?: string) => (s || "").toLowerCase().replace(/_/g, " ");

/** Fill {{1}}, {{2}}… with Meta's sample values so the preview reads like a message. */
function filled(text: string, params: string[]): string {
  return (text || "").replace(/\{\{(\d+)\}\}/g, (m, n) => params[Number(n) - 1] ?? m);
}

export function TemplateLibraryButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><BookOpen />Meta library</Button>
      <Sheet open={open} onOpenChange={setOpen} title="Meta template library" width="w-[640px]">
        {open && <TemplateLibrary onCreated={() => setOpen(false)} />}
      </Sheet>
    </>
  );
}

function TemplateLibrary({ onCreated }: { onCreated: () => void }) {
  const [q, setQ] = useState("");
  const [topic, setTopic] = useState("");
  const [usecase, setUsecase] = useState("");
  const [industry, setIndustry] = useState("");
  const [language, setLanguage] = useState("en_US");
  const [account, setAccount] = useState("");
  const [accounts, setAccounts] = useState<{ name: string; account_name: string }[]>([]);
  const [items, setItems] = useState<LibItem[]>([]);
  const [after, setAfter] = useState("");
  const [picked, setPicked] = useState<LibItem | null>(null);
  const { call: search, loading } = useFrappePostCall("excom.excom.api.chat.search_template_library");
  const { call: fetchAccounts } = useFrappePostCall("excom.excom.api.chat.get_channel_accounts");

  useEffect(() => { fetchAccounts({ channel: "whatsapp" }).then((r) => setAccounts((r as any)?.message || [])).catch(() => {}); }, [fetchAccounts]);

  const run = useCallback(async (more = false) => {
    try {
      const r = await search({ search: q, topic, usecase, industry, language, whatsapp_account: account, after: more ? after : "" });
      const m = (r as any)?.message || {};
      setItems((prev) => (more ? [...prev, ...(m.data || [])] : m.data || []));
      setAfter(m.after || "");
    } catch (e) { toast.error(serverMessage(e)); }
  }, [search, q, topic, usecase, industry, language, account, after]);

  useEffect(() => { const h = setTimeout(() => run(false), 350); return () => clearTimeout(h); }, [q, topic, usecase, industry, language, account]); // eslint-disable-line react-hooks/exhaustive-deps

  if (picked) return <CreateFromLibrary item={picked} account={account} onBack={() => setPicked(null)} onCreated={onCreated} />;

  return (
    <div className="p-3 space-y-3 min-w-0">
      <p className="text-xs text-ink-3">Utility templates written by Meta. Created unchanged, they are approved straight away.</p>
      <div className="relative"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-3" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: delivery, payment, order…" className="pl-8" autoFocus /></div>
      <div className="grid grid-cols-2 laptop:grid-cols-4 gap-2 min-w-0">
        <Select value={topic} onChange={(e) => setTopic(e.target.value)} aria-label="Topic"><option value="">Any topic</option>{TOPICS.map((t) => <option key={t} value={t}>{nice(t)}</option>)}</Select>
        <Select value={usecase} onChange={(e) => setUsecase(e.target.value)} aria-label="Use case"><option value="">Any use case</option>{USECASES.map((t) => <option key={t} value={t}>{nice(t)}</option>)}</Select>
        <Select value={industry} onChange={(e) => setIndustry(e.target.value)} aria-label="Industry"><option value="">Any industry</option>{INDUSTRIES.map((t) => <option key={t} value={t}>{nice(t)}</option>)}</Select>
        <Input value={language} onChange={(e) => setLanguage(e.target.value)} placeholder="Language, e.g. en_US" aria-label="Language" />
      </div>
      {accounts.length > 1 && (
        <Field label="WhatsApp account" hint="The template is created on this account's WhatsApp Business Account.">
          <Select value={account} onChange={(e) => setAccount(e.target.value)}><option value="">Default outgoing account</option>{accounts.map((a) => <option key={a.name} value={a.name}>{a.account_name || a.name}</option>)}</Select>
        </Field>
      )}
      {loading && items.length === 0 ? <div className="flex justify-center py-8 text-ink-3"><Loader2 className="size-5 animate-spin" /></div>
        : items.length === 0 ? <EmptyState icon={<BookOpen />} title="No library templates" hint="Try another word or clear the filters." compact />
        : (
          <div className="space-y-1.5">
            {items.map((t) => (
              <button key={`${t.id}-${t.language}`} type="button" onClick={() => setPicked(t)} className="w-full text-left rounded-md border border-border p-2.5 hover:bg-surface-hover min-w-0">
                <div className="flex items-center gap-2 min-w-0"><span className="text-sm font-medium text-ink-1 truncate flex-1">{t.name}</span><span className="text-xs text-ink-3 shrink-0">{t.language}</span></div>
                {t.header && <p className="text-xs text-ink-1 font-medium mt-0.5 truncate">{t.header}</p>}
                <p className="text-xs text-ink-3 line-clamp-2 mt-0.5 whitespace-pre-wrap">{filled(t.body, t.body_params)}</p>
                <div className="chip-row mt-1.5 h-5">
                  {t.topic && <Chip size="sm" accent="blue" label={nice(t.topic)} />}
                  {t.usecase && <Chip size="sm" label={nice(t.usecase)} />}
                  {t.buttons.length > 0 && <Chip size="sm" accent="amber" label={`${t.buttons.length} button${t.buttons.length > 1 ? "s" : ""}`} />}
                </div>
              </button>
            ))}
            {after && <Button size="sm" variant="ghost" disabled={loading} onClick={() => run(true)} className="w-full">{loading ? <Loader2 className="animate-spin" /> : null}More</Button>}
          </div>
        )}
    </div>
  );
}

function CreateFromLibrary({ item, account, onBack, onCreated }: { item: LibItem; account: string; onBack: () => void; onCreated: () => void }) {
  const [name, setName] = useState(item.name.replace(/_\d+$/, ""));
  const [values, setValues] = useState<Record<number, { main: string; example: string }>>(() =>
    Object.fromEntries(item.button_inputs.map((b) => [b.index, { main: b.default || "", example: "" }])));
  const { call: create, loading } = useFrappePostCall("excom.excom.api.chat.create_template_from_library");
  const nameOk = /^[a-z0-9_]{1,512}$/.test(name);

  const inputs = item.button_inputs.map((b) => {
    const v = values[b.index] || { main: "", example: "" };
    if (b.type === "URL") return { type: "URL", url: { base_url: v.main.trim(), ...(v.main.includes("{{") ? { url_suffix_example: v.example.trim() } : {}) } };
    if (b.type === "PHONE_NUMBER") return { type: "PHONE_NUMBER", phone_number: v.main.trim() };
    return { type: b.type };
  });
  const problem = !nameOk ? "Name: lowercase letters, digits and _ only"
    : item.button_inputs.some((b) => (b.type === "URL" || b.type === "PHONE_NUMBER") && !(values[b.index]?.main || "").trim()) ? "Fill every button"
    : item.button_inputs.some((b) => b.type === "URL" && (values[b.index]?.main || "").includes("{{") && !(values[b.index]?.example || "").trim()) ? "Give an example link for each {{1}}" : "";

  const submit = async () => {
    try {
      const r = await create({ library_template_name: item.name, template_name: name, language: item.language, whatsapp_account: account, button_inputs: JSON.stringify(inputs) });
      const m = (r as any)?.message || {};
      toast.success(`Created ${name} — ${m.status || "submitted"}`);
      window.dispatchEvent(new Event("excom:templates-synced"));
      onCreated();
    } catch (e) { toast.error(serverMessage(e)); }
  };

  return (
    <div className="p-3 space-y-3 min-w-0">
      <button type="button" onClick={onBack} className="text-xs text-ink-3 hover:text-ink-1 inline-flex items-center gap-1"><ChevronLeft className="size-3" />Back to the library</button>
      <div className="rounded-md border border-border bg-surface-sunken p-3 min-w-0">
        <p className="text-xs text-ink-3 mb-1">{item.name} · {item.language} · {nice(item.category)}</p>
        {item.header && <p className="text-sm text-ink-1 font-semibold mb-1">{item.header}</p>}
        <p className="text-sm text-ink-2 whitespace-pre-wrap break-words">{filled(item.body, item.body_params)}</p>
        {item.buttons.length > 0 && <div className="chip-row mt-2 h-6">{item.buttons.map((b, i) => <Chip key={i} size="sm" accent="blue" label={b.text || nice(b.type)} />)}</div>}
      </div>
      <Field label="Template name" required hint="Your own name for it on WhatsApp: lowercase letters, digits and _.">
        <Input value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[\s-]+/g, "_"))} />
      </Field>
      {item.button_inputs.filter((b) => b.type === "URL" || b.type === "PHONE_NUMBER").map((b) => {
        const v = values[b.index] || { main: "", example: "" };
        const set = (patch: Partial<typeof v>) => setValues((prev) => ({ ...prev, [b.index]: { ...v, ...patch } }));
        return b.type === "URL" ? (
          <Field key={b.index} label={`“${b.text}” link`} required hint="Your website address. End it with {{1}} to add a different ending per message.">
            <Input value={v.main} onChange={(e) => set({ main: e.target.value })} placeholder="https://www.example.com/orders/{{1}}" />
            {v.main.includes("{{") && <Input className="mt-1.5" value={v.example} onChange={(e) => set({ example: e.target.value })} placeholder="Example full link, e.g. https://www.example.com/orders/123" />}
          </Field>
        ) : (
          <Field key={b.index} label={`“${b.text}” phone number`} required hint="With country code.">
            <Input value={v.main} onChange={(e) => set({ main: e.target.value })} placeholder="+919812345678" />
          </Field>
        );
      })}
      {problem && <p className="text-xs text-crayon-amber-text">{problem}</p>}
      <div className="flex justify-end"><Button variant="primary" disabled={!!problem || loading} onClick={submit}>{loading ? <Loader2 className="animate-spin" /> : <Plus />}Create template</Button></div>
    </div>
  );
}
