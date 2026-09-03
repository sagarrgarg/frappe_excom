import { useCallback, useEffect, useRef, useState } from "react";
import { useFrappePostCall, useFrappeFileUpload } from "frappe-react-sdk";
import { toast } from "sonner";
import { Search, Send, Loader2, ChevronRight, ChevronLeft, AlertTriangle, Mail, MessageSquare, FileText, Users, Image as ImageIcon, Paperclip, Upload, Trash2, Timer, Check } from "lucide-react";
import { Sheet, Button, Input, Field, Select, Textarea, Chip, EmptyState } from "./primitives";
import { DateTimePicker } from "./ui/date-time-picker";
import { cn } from "./ui/utils";

interface SubscriberListOption { name: string; list_name: string; active_subscribers: number }
interface TemplateButton { button_type: string; button_label: string; website_url?: string; url_type?: string; example_url?: string; phone_number?: string }
interface TemplateItem {
  name: string; template_name: string; actual_name: string; template: string; language_code: string; category: string;
  header_type: string; header: string; footer: string; sample_values: string; field_names: string; variable_count: number;
  sample_variables: string[]; buttons: TemplateButton[]; has_dynamic_url: boolean; available_languages: string[];
}
interface SubscriberField { value: string; label: string }
type WaVariableSlotKind = "literal" | "subscriber_field";
interface WaVariableSlotState { kind: WaVariableSlotKind; value: string; field: string }
const emptyVariableSlots = (n: number): WaVariableSlotState[] => Array.from({ length: n }, () => ({ kind: "literal", value: "", field: "" }));

const HEADER_ACCEPT: Record<string, string> = {
  IMAGE: "image/jpeg,image/png,image/webp",
  DOCUMENT: "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

function localDatetimeInputMin(): string {
  const n = new Date(); const p = (x: number) => String(x).padStart(2, "0");
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}T${p(n.getHours())}:${p(n.getMinutes())}`;
}
function frappeDatetimeFromLocalInput(dt: string): string {
  if (!dt.trim()) return ""; const [d, t] = dt.split("T"); if (!d || !t) return ""; return `${d} ${t.slice(0, 5)}:00`;
}

type Step = 0 | 1 | 2 | 3;
const STEPS = ["Audience", "Content", "Schedule", "Review"];

/**
 * Broadcast wizard (W11): Audience → Content → Schedule → Review. Each step fits a 768px-tall screen;
 * lists scroll inside the step. Renders as a right Sheet (tablet+) / bottom sheet (phone).
 */
export function BroadcastWizard({ open, onOpenChange, onCreated, presetList }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (name: string) => void; presetList?: string }) {
  const [step, setStep] = useState<Step>(0);
  const [name, setName] = useState("");
  const [subscriberList, setSubscriberList] = useState(presetList || "");
  const [channel, setChannel] = useState<"WhatsApp" | "Email">("WhatsApp");
  const [lists, setLists] = useState<SubscriberListOption[]>([]);
  const [loadingLists, setLoadingLists] = useState(true);
  const [listSearch, setListSearch] = useState("");

  const [waAccounts, setWaAccounts] = useState<{ name: string; account_name: string; wa_phone_id?: string }[]>([]);
  const [waChannelAccount, setWaChannelAccount] = useState("");
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateItem | null>(null);
  const [templateSearch, setTemplateSearch] = useState("");
  const [selectedLanguage, setSelectedLanguage] = useState("");
  const [variableSlots, setVariableSlots] = useState<WaVariableSlotState[]>([]);
  const [buttonUrls, setButtonUrls] = useState<string[]>([]);
  const [subscriberFields, setSubscriberFields] = useState<SubscriberField[]>([]);
  const [headerMediaUrl, setHeaderMediaUrl] = useState("");
  const [headerFileName, setHeaderFileName] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [scheduleAt, setScheduleAt] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const { call: fetchLists } = useFrappePostCall("excom.excom.api.broadcast.get_subscriber_lists_for_broadcast");
  const { call: fetchWaAccounts } = useFrappePostCall("excom.excom.api.chat.get_channel_accounts");
  const { call: fetchTemplates } = useFrappePostCall("excom.excom.api.chat.get_whatsapp_templates");
  const { call: fetchSubFields } = useFrappePostCall("excom.excom.api.chat.get_subscriber_variable_fields");
  const { call: createBroadcast } = useFrappePostCall("excom.excom.api.broadcast.create_broadcast");
  const { call: submitBroadcast } = useFrappePostCall("excom.excom.api.broadcast.submit_broadcast");
  const { upload } = useFrappeFileUpload();

  useEffect(() => {
    if (!open) return;
    setLoadingLists(true);
    fetchLists({}).then((res) => setLists((res as any)?.message || [])).catch(() => toast.error("Failed to load subscriber lists")).finally(() => setLoadingLists(false));
  }, [open, fetchLists]);

  useEffect(() => {
    if (step !== 1 || channel !== "WhatsApp") return;
    if (waAccounts.length === 0) fetchWaAccounts({ channel: "whatsapp" }).then((res) => { const accs = (res as any)?.message || []; setWaAccounts(accs); if (accs.length === 1) setWaChannelAccount(accs[0].name); }).catch(() => {});
    if (subscriberFields.length === 0) fetchSubFields({}).then((res) => setSubscriberFields((res as any)?.message || [])).catch(() => {});
  }, [step, channel, waAccounts.length, subscriberFields.length, fetchWaAccounts, fetchSubFields]);

  useEffect(() => { if (waChannelAccount) { setSelectedTemplate(null); setVariableSlots([]); setHeaderMediaUrl(""); setHeaderFileName(""); setButtonUrls([]); } }, [waChannelAccount]);

  const loadTemplates = useCallback(async (search = "") => {
    if (!waChannelAccount) { setTemplates([]); return; }
    try { const res = await fetchTemplates({ search, whatsapp_account: waChannelAccount }); setTemplates((res as any)?.message || []); } catch { toast.error("Failed to load templates"); }
  }, [fetchTemplates, waChannelAccount]);
  useEffect(() => { if (step === 1 && channel === "WhatsApp" && waChannelAccount) loadTemplates(templateSearch); }, [step, channel, templateSearch, waChannelAccount, loadTemplates]);

  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    setUploading(true);
    try { const r = await upload(file, { isPrivate: false }); setHeaderMediaUrl(r.file_url); setHeaderFileName(file.name); toast.success("File uploaded"); }
    catch { toast.error("Upload failed"); } finally { setUploading(false); if (fileInputRef.current) fileInputRef.current.value = ""; }
  }, [upload]);

  const selectedListData = lists.find((l) => l.name === subscriberList);
  const needsMedia = ["IMAGE", "VIDEO", "DOCUMENT"].includes(selectedTemplate?.header_type || "");
  const variableSlotsComplete = !selectedTemplate || selectedTemplate.variable_count === 0 || (variableSlots.length === selectedTemplate.variable_count && variableSlots.every((s) => (s.kind === "literal" ? s.value.trim() : s.field.trim())));
  const canAudience = Boolean(name.trim() && subscriberList);
  const canContent = channel === "WhatsApp" ? Boolean(selectedTemplate && (!needsMedia || headerMediaUrl) && waChannelAccount && variableSlotsComplete) : Boolean(emailSubject.trim() && emailBody.trim());
  const canNext = [canAudience, canContent, true, true][step];

  const getPreview = (): string => {
    if (!selectedTemplate) return "";
    let text = selectedTemplate.template || "";
    variableSlots.forEach((slot, i) => {
      if (slot.kind === "subscriber_field") { const sf = subscriberFields.find((f) => f.value === slot.field); text = text.replace(`{{${i + 1}}}`, sf ? `{${sf.label}}` : `{{${i + 1}}}`); }
      else text = text.replace(`{{${i + 1}}}`, slot.value || `{{${i + 1}}}`);
    });
    return text;
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const scheduledAtFrappe = frappeDatetimeFromLocalInput(scheduleAt);
      const res = await createBroadcast({
        broadcast_name: name.trim(), subscriber_list: subscriberList, channel,
        wa_channel_account: channel === "WhatsApp" ? waChannelAccount : "",
        wa_template: channel === "WhatsApp" ? selectedTemplate?.name : "",
        wa_language_code: channel === "WhatsApp" ? selectedLanguage : "",
        wa_variable_mode: "same_for_all",
        wa_variable_slots: channel === "WhatsApp" ? JSON.stringify(variableSlots.map((s) => (s.kind === "literal" ? { kind: "literal", value: s.value } : { kind: "subscriber_field", field: s.field }))) : "",
        wa_template_variables: "[]", wa_variable_mapping: "[]",
        wa_header_media: channel === "WhatsApp" ? headerMediaUrl : "",
        wa_button_urls: channel === "WhatsApp" ? JSON.stringify(buttonUrls.filter(Boolean)) : "[]",
        email_subject: channel === "Email" ? emailSubject : "", email_body: channel === "Email" ? emailBody : "",
        scheduled_at: scheduledAtFrappe,
      });
      const bcName = (res as any)?.message?.name;
      if (!bcName) throw new Error("No broadcast name returned");
      await submitBroadcast({ broadcast_name: bcName });
      toast.success(scheduledAtFrappe ? "Broadcast scheduled" : "Broadcast submitted — sending started");
      onCreated(bcName);
    } catch (err: any) {
      let msg = "Failed to create broadcast";
      try { if (err?._server_messages) { const parsed = JSON.parse(err._server_messages); if (typeof parsed?.[0] === "string") msg = JSON.parse(parsed[0])?.message || parsed[0]; } } catch { /* default */ }
      toast.error(msg);
    } finally { setSubmitting(false); }
  };

  const labels = selectedTemplate?.field_names ? selectedTemplate.field_names.split(",").map((s) => s.trim()) : [];
  const filteredLists = lists.filter((l) => !listSearch || l.list_name.toLowerCase().includes(listSearch.toLowerCase()));

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="New broadcast"
      width="w-[560px]"
      footer={
        <>
          <Button variant="ghost" onClick={() => (step === 0 ? onOpenChange(false) : setStep((step - 1) as Step))}>{step === 0 ? "Cancel" : <><ChevronLeft />Back</>}</Button>
          {step < 3 ? (
            <Button variant="primary" disabled={!canNext} onClick={() => setStep((step + 1) as Step)}>Next<ChevronRight /></Button>
          ) : (
            <Button variant="primary" disabled={submitting} onClick={handleSubmit}>{submitting ? <Loader2 className="animate-spin" /> : scheduleAt ? <Timer /> : <Send />}{scheduleAt ? "Schedule" : "Send now"}</Button>
          )}
        </>
      }
    >
      {/* Stepper */}
      <ol className="flex items-center gap-1 px-3 h-10 border-b border-border text-xs shrink-0 min-w-0" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} className={cn("flex items-center gap-1.5 min-w-0", i > 0 && "before:content-[''] before:w-4 before:h-px before:bg-border-strong before:mx-1")}>
            <span className={cn("size-5 rounded-full flex items-center justify-center tabular-nums shrink-0", i < step ? "bg-crayon-green-base text-white" : i === step ? "bg-crayon-blue-base text-white" : "bg-surface-sunken text-ink-3")}>{i < step ? <Check className="size-3" /> : i + 1}</span>
            <span className={cn("truncate", i === step ? "text-ink-1 font-medium" : "text-ink-3", i !== step && "hidden tablet:inline")}>{s}</span>
          </li>
        ))}
      </ol>
      <input ref={fileInputRef} type="file" accept={selectedTemplate ? HEADER_ACCEPT[selectedTemplate.header_type] || "" : ""} onChange={handleFileUpload} className="hidden" />

      <div className="p-3 space-y-4">
        {step === 0 && (
          <>
            <Field label="Broadcast name" required><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. March promo" autoFocus /></Field>
            <Field label="Subscriber list" required>
              <div className="relative mb-2"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={listSearch} onChange={(e) => setListSearch(e.target.value)} placeholder="Filter lists" className="pl-8" /></div>
              {loadingLists ? <div className="flex items-center gap-2 text-ink-3 text-sm py-2"><Loader2 className="size-4 animate-spin" />Loading lists…</div> : filteredLists.length === 0 ? <EmptyState icon={<Users />} title="No lists" hint="Create one under Subscribers first." compact /> : (
                <div className="space-y-1 max-h-[40vh] overflow-y-auto" role="radiogroup">
                  {filteredLists.map((l) => (
                    <button key={l.name} type="button" role="radio" aria-checked={subscriberList === l.name} onClick={() => setSubscriberList(l.name)} className={cn("w-full text-left px-3 h-11 rounded-md border flex items-center gap-2 min-w-0", subscriberList === l.name ? "border-crayon-blue-base bg-crayon-blue-tint" : "border-border hover:bg-surface-hover")}>
                      <span className="text-sm text-ink-1 truncate flex-1">{l.list_name}</span>
                      <span className="text-xs text-ink-3 tabular-nums shrink-0 inline-flex items-center gap-1"><Users className="size-3" />{l.active_subscribers} active</span>
                    </button>
                  ))}
                </div>
              )}
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            <Field label="Channel">
              <div className="grid grid-cols-2 gap-2 [grid-template-columns:repeat(2,minmax(0,1fr))]">
                {(["WhatsApp", "Email"] as const).map((c) => (
                  <button key={c} type="button" onClick={() => setChannel(c)} className={cn("h-10 rounded-md border flex items-center justify-center gap-2 text-sm font-medium", channel === c ? (c === "WhatsApp" ? "border-crayon-green-base bg-crayon-green-tint text-crayon-green-text" : "border-crayon-blue-base bg-crayon-blue-tint text-crayon-blue-text") : "border-border text-ink-2 hover:bg-surface-hover")}>
                    {c === "WhatsApp" ? <MessageSquare className="size-4" /> : <Mail className="size-4" />}{c}
                  </button>
                ))}
              </div>
            </Field>

            {channel === "WhatsApp" && (
              <>
                {waAccounts.length > 0 && (
                  <Field label="Send from" hint="Only templates approved for this number appear below.">
                    <Select value={waChannelAccount} onChange={(e) => setWaChannelAccount(e.target.value)}>
                      <option value="">Select WhatsApp number…</option>
                      {waAccounts.map((a) => <option key={a.name} value={a.name}>{a.account_name || a.name}{a.wa_phone_id ? ` · ${a.wa_phone_id}` : ""}</option>)}
                    </Select>
                  </Field>
                )}
                {!waChannelAccount ? (
                  <p className="text-sm text-ink-3 text-center py-6 border border-dashed border-border-strong rounded-md">Select a WhatsApp number to load its templates.</p>
                ) : !selectedTemplate ? (
                  <Field label="Template" required>
                    <div className="relative mb-2"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input placeholder="Search templates" value={templateSearch} onChange={(e) => setTemplateSearch(e.target.value)} className="pl-8" autoFocus /></div>
                    <div className="space-y-1 max-h-[45vh] overflow-y-auto">
                      {(() => { const seen = new Set<string>(); return templates.filter((t) => { const k = t.actual_name || t.template_name; if (seen.has(k)) return false; seen.add(k); return true; }); })().map((t) => (
                        <button key={t.name} type="button" onClick={() => { setSelectedTemplate(t); setSelectedLanguage(t.language_code); setVariableSlots(emptyVariableSlots(t.variable_count)); setHeaderMediaUrl(""); setHeaderFileName(""); setButtonUrls(new Array((t.buttons || []).filter((b) => b.button_type === "Visit Website" && b.url_type === "Dynamic").length).fill("")); }}
                          className="w-full text-left rounded-md border border-border p-2.5 hover:bg-surface-hover min-w-0">
                          <div className="flex items-center gap-2 min-w-0"><span className="text-sm font-medium text-ink-1 truncate flex-1">{t.template_name}</span><ChevronRight className="size-4 text-ink-muted shrink-0" /></div>
                          <p className="text-xs text-ink-3 line-clamp-2 mt-0.5">{t.template || "No preview"}</p>
                          <div className="chip-row mt-1.5 h-5">
                            <Chip size="sm" label={t.category} />
                            {t.available_languages.length > 1 && <Chip size="sm" accent="teal" label={`${t.available_languages.length} languages`} />}
                            {t.variable_count > 0 && <Chip size="sm" accent="blue" label={`${t.variable_count} variable${t.variable_count > 1 ? "s" : ""}`} />}
                            {t.header_type === "IMAGE" && <Chip size="sm" accent="violet" icon={<ImageIcon />} label="Photo" />}
                            {t.header_type === "DOCUMENT" && <Chip size="sm" accent="amber" icon={<Paperclip />} label="Document" />}
                            {t.has_dynamic_url && <Chip size="sm" accent="amber" label="Dynamic URL" />}
                          </div>
                        </button>
                      ))}
                      {templates.length === 0 && <p className="text-center py-6 text-sm text-ink-3">No approved templates found</p>}
                    </div>
                  </Field>
                ) : (
                  <>
                    <button type="button" onClick={() => setSelectedTemplate(null)} className="text-xs text-ink-3 hover:text-ink-1 inline-flex items-center gap-1"><ChevronLeft className="size-3" />Change template</button>
                    <div className="rounded-md border border-border bg-surface-sunken p-3 min-w-0">
                      <p className="text-xs text-ink-3 mb-1 truncate">{selectedTemplate.template_name}</p>
                      <p className="text-base text-ink-1 whitespace-pre-wrap break-words">{getPreview()}</p>
                      {selectedTemplate.footer && <p className="text-xs text-ink-3 mt-2">{selectedTemplate.footer}</p>}
                    </div>
                    {selectedTemplate.available_languages.length > 1 && (
                      <Field label="Language">
                        <div className="chip-row h-7">
                          {selectedTemplate.available_languages.map((lang) => (
                            <Chip key={lang} label={lang} accent={selectedLanguage === lang ? "green" : "neutral"} onClick={() => { setSelectedLanguage(lang); const m = templates.find((t) => (t.actual_name || t.template_name) === (selectedTemplate.actual_name || selectedTemplate.template_name) && t.language_code === lang); if (m && m.name !== selectedTemplate.name) { setSelectedTemplate(m); setVariableSlots(emptyVariableSlots(m.variable_count)); } }} />
                          ))}
                        </div>
                      </Field>
                    )}
                    {needsMedia && (
                      <Field label={selectedTemplate.header_type === "IMAGE" ? "Header image" : "Header document"} required>
                        {headerMediaUrl ? (
                          <div className="flex items-center gap-3 rounded-md border border-border p-2 min-w-0">
                            {selectedTemplate.header_type === "IMAGE" ? <img src={headerMediaUrl} alt="" className="size-12 rounded object-cover shrink-0" /> : <span className="size-12 rounded bg-crayon-amber-tint flex items-center justify-center shrink-0"><FileText className="size-5 text-crayon-amber-text" /></span>}
                            <div className="flex-1 min-w-0"><p className="text-sm text-ink-1 truncate">{headerFileName}</p><p className="text-xs text-crayon-green-text">Uploaded</p></div>
                            <Button variant="ghost" size="icon" aria-label="Remove" onClick={() => { setHeaderMediaUrl(""); setHeaderFileName(""); }}><Trash2 /></Button>
                          </div>
                        ) : (
                          <Button className="w-full h-16 border-dashed" disabled={uploading} onClick={() => fileInputRef.current?.click()}>{uploading ? <Loader2 className="animate-spin" /> : <Upload />}{uploading ? "Uploading…" : "Upload file"}</Button>
                        )}
                      </Field>
                    )}
                    {selectedTemplate.variable_count > 0 && (
                      <Field label="Template variables" hint="Same value for everyone, or a subscriber field per recipient.">
                        <div className="space-y-2">
                          {variableSlots.map((slot, idx) => (
                            <div key={idx} className="rounded-md border border-border p-2 space-y-1.5 min-w-0">
                              <div className="flex items-center gap-2 min-w-0">
                                <span className="text-xs text-ink-2 truncate flex-1">{labels[idx] || `Variable {{${idx + 1}}}`}</span>
                                <div className="inline-flex rounded bg-surface-sunken p-0.5 shrink-0">
                                  {(["literal", "subscriber_field"] as const).map((k) => (
                                    <button key={k} type="button" onClick={() => { const n = [...variableSlots]; n[idx] = { kind: k, value: k === "literal" ? slot.value : "", field: k === "subscriber_field" ? slot.field : "" }; setVariableSlots(n); }} className={cn("h-6 px-2 rounded text-xs", slot.kind === k ? "bg-surface text-ink-1 shadow-ex" : "text-ink-3")}>{k === "literal" ? "Same for all" : "Per subscriber"}</button>
                                  ))}
                                </div>
                              </div>
                              {slot.kind === "literal" ? (
                                <Input placeholder={selectedTemplate.sample_variables?.[idx] || `Value for {{${idx + 1}}}`} value={slot.value} onChange={(e) => { const n = [...variableSlots]; n[idx] = { ...n[idx], value: e.target.value }; setVariableSlots(n); }} />
                              ) : (
                                <Select value={slot.field} onChange={(e) => { const n = [...variableSlots]; n[idx] = { ...n[idx], field: e.target.value }; setVariableSlots(n); }}>
                                  <option value="">Select subscriber field…</option>
                                  {subscriberFields.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
                                </Select>
                              )}
                            </div>
                          ))}
                        </div>
                      </Field>
                    )}
                    {selectedTemplate.has_dynamic_url && (() => {
                      const dyn = (selectedTemplate.buttons || []).filter((b) => b.button_type === "Visit Website" && b.url_type === "Dynamic");
                      return dyn.length ? (
                        <Field label="Button link parameters">
                          <div className="space-y-2">{dyn.map((b, i) => <Field key={i} label={<span className="truncate">{b.button_label} — {b.website_url}</span>}><Input placeholder={b.example_url || "URL suffix"} value={buttonUrls[i] || ""} onChange={(e) => { const n = [...buttonUrls]; n[i] = e.target.value; setButtonUrls(n); }} /></Field>)}</div>
                        </Field>
                      ) : null;
                    })()}
                  </>
                )}
              </>
            )}

            {channel === "Email" && (
              <>
                <Field label="Subject" required><Input value={emailSubject} onChange={(e) => setEmailSubject(e.target.value)} placeholder="e.g. News from us" autoFocus /></Field>
                <Field label="Body" required hint="Jinja works: {{ subscriber.display_name }}, {{ customer.customer_name }}">
                  <Textarea value={emailBody} onChange={(e) => setEmailBody(e.target.value)} rows={10} placeholder={"Hello {{ subscriber.display_name }},\n\n…"} />
                </Field>
              </>
            )}
          </>
        )}

        {step === 2 && (
          <>
            <Field label="Send time" hint="Leave empty to send as soon as you confirm. Times are in your local timezone.">
              <DateTimePicker value={scheduleAt} onChange={setScheduleAt} min={localDatetimeInputMin()} placeholder="Send immediately" />
            </Field>
            <div className="chip-row h-7">
              <Chip label="Now" accent={!scheduleAt ? "blue" : "neutral"} onClick={() => setScheduleAt("")} />
              {[{ l: "In 1 hour", h: 1 }, { l: "Tomorrow 9:00", h: -1 }, { l: "In 24 hours", h: 24 }].map((q) => (
                <Chip key={q.l} label={q.l} onClick={() => { const d = new Date(); if (q.h < 0) { d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); } else d.setHours(d.getHours() + q.h); const p = (x: number) => String(x).padStart(2, "0"); setScheduleAt(`${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`); }} />
              ))}
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <dl className="rounded-md border border-border divide-y divide-border text-sm">
              {[
                ["Name", name], ["List", selectedListData?.list_name || subscriberList], ["Recipients", `${selectedListData?.active_subscribers ?? "—"} active`], ["Channel", channel],
                ...(channel === "WhatsApp" && waChannelAccount ? [["Account", waAccounts.find((a) => a.name === waChannelAccount)?.account_name || waChannelAccount]] : []),
                ...(channel === "WhatsApp" && selectedTemplate ? [["Template", selectedTemplate.template_name], ["Language", selectedLanguage], ["Variables", selectedTemplate.variable_count === 0 ? "None" : `${variableSlots.filter((s) => s.kind === "literal").length} same · ${variableSlots.filter((s) => s.kind === "subscriber_field").length} per subscriber`]] : []),
                ...(channel === "Email" ? [["Subject", emailSubject]] : []),
                ["Send time", scheduleAt ? new Date(scheduleAt).toLocaleString() : "Immediately"],
              ].map(([k, v]) => (
                <div key={k} className="flex items-center gap-3 px-3 h-9 min-w-0"><dt className="text-ink-3 w-24 shrink-0">{k}</dt><dd className="text-ink-1 truncate flex-1 text-right">{v}</dd></div>
              ))}
            </dl>
            {channel === "WhatsApp" && selectedTemplate && (
              <div className="rounded-md border border-crayon-green-base/40 bg-crayon-green-tint p-3 min-w-0">
                <p className="text-xs text-crayon-green-text mb-1">Preview</p>
                {headerMediaUrl && (selectedTemplate.header_type === "IMAGE" ? <img src={headerMediaUrl} alt="" className="size-16 rounded object-cover mb-2" /> : <p className="text-xs text-ink-3 mb-2 inline-flex items-center gap-1"><FileText className="size-3.5" />{headerFileName}</p>)}
                <p className="text-base text-ink-1 whitespace-pre-wrap break-words">{getPreview()}</p>
              </div>
            )}
            <div className="rounded-md border border-crayon-amber-base/40 bg-crayon-amber-tint p-3 flex items-start gap-2">
              <AlertTriangle className="size-4 text-crayon-amber-text shrink-0 mt-0.5" />
              <p className="text-xs text-ink-2">{scheduleAt ? "At the scheduled time Excom sends to every active subscriber on the list. This cannot be undone." : "Sends immediately to every active subscriber on the list. This cannot be undone."}</p>
            </div>
          </>
        )}
      </div>
    </Sheet>
  );
}
