import { useState } from "react";
import { useFrappeFileUpload } from "frappe-react-sdk";
import { Image as ImageIcon, Loader2, Plus, Trash2, Upload, Video } from "lucide-react";
import { Field, Input, Button } from "./primitives";
import { DateTimePicker } from "./ui/date-time-picker";
import { toastError } from "./ErrorDialog";

/**
 * Send-time inputs for the WhatsApp template types beyond text/media (HLD-005 Phase 4):
 * coupon code, offer end, carousel cards, catalogue thumbnail, multi-product sections,
 * single product and call-button values. The server checks the same rules again
 * (excom/excom/services/whatsapp_template_types.py) and builds Meta's components.
 */

export interface ExtrasButton { button_type: string; button_label: string; example_code?: string; ttl_minutes?: number }
export interface ExtrasCard { card_body?: string; button_1_text?: string; button_1_value?: string; button_2_text?: string; button_2_value?: string }
export interface ExtrasTemplate {
  template_type?: string;
  lto_text?: string;
  lto_has_expiration?: number;
  card_header_format?: string;
  card_button_1_type?: string;
  card_button_2_type?: string;
  call_permission_request?: number;
  buttons?: ExtrasButton[];
  cards?: ExtrasCard[];
}

interface CardState { header_media: string; header_name: string; product_retailer_id: string; body: string[]; buttons: string[] }
interface SectionState { title: string; ids: string }
export interface ExtrasState {
  coupon_code: string;
  offer_end: string; // datetime-local "YYYY-MM-DDTHH:mm", the browser's time
  cards: CardState[];
  thumbnail: string;
  sections: SectionState[];
  product: string;
  call_ttl: string;
  call_payload: string;
}

const CAROUSELS = ["Media Carousel", "Product Carousel"];

const placeholders = (text: string): string[] => {
  const out: string[] = [];
  const re = /\{\{([^}]+)\}\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text || "")) !== null) { const n = m[1].trim(); if (!out.includes(n)) out.push(n); }
  return out;
};
const cardButtonTypes = (t: ExtrasTemplate) => [t.card_button_1_type, t.card_button_2_type].filter(Boolean) as string[];
const cardButtonValue = (c: ExtrasCard | undefined, j: number) => (j === 0 ? c?.button_1_value : c?.button_2_value) || "";
const cardButtonText = (c: ExtrasCard | undefined, j: number) => (j === 0 ? c?.button_1_text : c?.button_2_text) || `Button ${j + 1}`;
const hasButton = (t: ExtrasTemplate, kind: string) => (t.buttons || []).some((b) => b.button_type === kind);
const codeMax = (t: ExtrasTemplate) => (t.template_type === "Limited-Time Offer" ? 15 : 20);

const emptyCard = (t: ExtrasTemplate, i: number): CardState => {
  const def = (t.cards || [])[i] || (t.cards || [])[0];
  return { header_media: "", header_name: "", product_retailer_id: "", body: new Array(placeholders(def?.card_body || "").length).fill(""), buttons: cardButtonTypes(t).map(() => "") };
};

export function emptyExtras(t: ExtrasTemplate | null): ExtrasState {
  const n = t && CAROUSELS.includes(t.template_type || "") ? Math.max((t.cards || []).length, 2) : 0;
  return {
    coupon_code: "", offer_end: "", cards: t ? Array.from({ length: n }, (_, i) => emptyCard(t, i)) : [],
    thumbnail: "", sections: [{ title: "", ids: "" }], product: "", call_ttl: "", call_payload: "",
  };
}

/** True when the template asks for any of these inputs. */
export function hasExtras(t: ExtrasTemplate | null): boolean {
  if (!t) return false;
  const tt = t.template_type || "Standard";
  return tt !== "Standard" || hasButton(t, "Copy Code") || hasButton(t, "Voice Call");
}

const splitIds = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

/** The `extras` JSON object the server expects. */
export function buildExtras(t: ExtrasTemplate | null, s: ExtrasState): Record<string, unknown> {
  if (!t) return {};
  const tt = t.template_type || "Standard";
  const out: Record<string, unknown> = {};
  if (hasButton(t, "Copy Code") && s.coupon_code.trim()) out.coupon_code = s.coupon_code.trim();
  if (tt === "Limited-Time Offer" && t.lto_has_expiration && s.offer_end) out.offer_expiration_ms = new Date(s.offer_end).getTime();
  if (CAROUSELS.includes(tt)) {
    out.cards = s.cards.map((c) => (tt === "Product Carousel"
      ? { product_retailer_id: c.product_retailer_id.trim(), buttons: c.buttons }
      : { header_media: c.header_media, body: c.body, buttons: c.buttons }));
  }
  if ((tt === "Catalog" || tt === "Multi-Product") && s.thumbnail.trim()) out.thumbnail_product_retailer_id = s.thumbnail.trim();
  if (tt === "Multi-Product") out.sections = s.sections.filter((x) => x.title.trim() || x.ids.trim()).map((x) => ({ title: x.title.trim(), product_retailer_ids: splitIds(x.ids) }));
  if (tt === "Single-Product") out.product_retailer_id = s.product.trim();
  if (hasButton(t, "Voice Call") && (s.call_ttl.trim() || s.call_payload.trim())) {
    out.voice_call = { ...(s.call_ttl.trim() ? { ttl_minutes: Number(s.call_ttl) } : {}), ...(s.call_payload.trim() ? { payload: s.call_payload.trim() } : {}) };
  }
  return out;
}

/** The first missing or invalid input, or "" when the extras are complete. */
export function extrasProblem(t: ExtrasTemplate | null, s: ExtrasState): string {
  if (!t) return "";
  const tt = t.template_type || "Standard";
  if (hasButton(t, "Copy Code")) {
    if (!s.coupon_code.trim()) return "Enter the coupon code";
    if (s.coupon_code.trim().length > codeMax(t)) return `Coupon code: at most ${codeMax(t)} characters`;
  }
  if (tt === "Limited-Time Offer" && t.lto_has_expiration) {
    if (!s.offer_end) return "Set when the offer ends";
    if (new Date(s.offer_end).getTime() <= Date.now()) return "The offer end is in the past";
  }
  if (CAROUSELS.includes(tt)) {
    const types = cardButtonTypes(t);
    if (tt === "Product Carousel" && (s.cards.length < 2 || s.cards.length > 10)) return "A product carousel sends 2–10 cards";
    for (let i = 0; i < s.cards.length; i++) {
      const c = s.cards[i];
      const def = (t.cards || [])[i] || (t.cards || [])[0];
      if (tt === "Product Carousel" ? !c.product_retailer_id.trim() : !c.header_media) return `Card ${i + 1}: ${tt === "Product Carousel" ? "enter the product id" : "attach the media"}`;
      if (c.body.some((v) => !v.trim())) return `Card ${i + 1}: fill the card text`;
      for (let j = 0; j < types.length; j++) if (types[j] === "Visit Website" && cardButtonValue(def, j).includes("{{") && !(c.buttons[j] || "").trim()) return `Card ${i + 1}: enter the link ending`;
    }
  }
  if (tt === "Multi-Product") {
    const secs = s.sections.filter((x) => x.title.trim() || x.ids.trim());
    if (secs.length === 0) return "Add at least one product section";
    if (secs.length > 10) return "At most 10 sections";
    if (secs.some((x) => !x.title.trim() || splitIds(x.ids).length === 0)) return "Every section needs a title and products";
    if (secs.some((x) => x.title.trim().length > 24)) return "Section titles: at most 24 characters";
    if (secs.reduce((n, x) => n + splitIds(x.ids).length, 0) > 30) return "At most 30 products in all";
  }
  if (tt === "Single-Product" && !s.product.trim()) return "Enter the product id";
  if (s.call_ttl.trim() && !(Number(s.call_ttl) >= 1 && Number(s.call_ttl) <= 43200)) return "Call link: 1–43200 minutes";
  return "";
}

/** Short summary for a review screen. */
export function extrasSummary(t: ExtrasTemplate | null, s: ExtrasState): string {
  if (!t || !hasExtras(t)) return "";
  const tt = t.template_type || "Standard";
  const parts: string[] = [];
  if (s.coupon_code) parts.push(`code ${s.coupon_code}`);
  if (s.offer_end) parts.push(`ends ${new Date(s.offer_end).toLocaleString()}`);
  if (CAROUSELS.includes(tt)) parts.push(`${s.cards.length} cards`);
  if (tt === "Multi-Product") parts.push(`${s.sections.reduce((n, x) => n + splitIds(x.ids).length, 0)} products`);
  if (tt === "Single-Product" && s.product) parts.push(`product ${s.product}`);
  return parts.join(" · ") || tt;
}

function localMin(): string {
  const n = new Date(); const p = (x: number) => String(x).padStart(2, "0");
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}T${p(n.getHours())}:${p(n.getMinutes())}`;
}

/** The inputs themselves. Renders nothing for a plain template. */
export function TemplateExtrasFields({ template: t, value: s, onChange }: { template: ExtrasTemplate; value: ExtrasState; onChange: (s: ExtrasState) => void }) {
  const { upload } = useFrappeFileUpload();
  const [uploadingCard, setUploadingCard] = useState<number | null>(null);
  if (!hasExtras(t)) return null;
  const tt = t.template_type || "Standard";
  const set = (patch: Partial<ExtrasState>) => onChange({ ...s, ...patch });
  const setCard = (i: number, patch: Partial<CardState>) => set({ cards: s.cards.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  const types = cardButtonTypes(t);
  const fmt = (t.card_header_format || "IMAGE").toUpperCase();

  const uploadCard = async (i: number, file: File | undefined) => {
    if (!file) return;
    setUploadingCard(i);
    try { const r = await upload(file, { isPrivate: false }); setCard(i, { header_media: r.file_url, header_name: file.name }); }
    catch (err) { toastError(err, "Upload failed"); } finally { setUploadingCard(null); }
  };

  return (
    <div className="space-y-3 min-w-0">
      {tt === "Limited-Time Offer" && (
        <div className="rounded-md border border-crayon-amber-base/40 bg-crayon-amber-tint px-3 py-2 text-xs text-crayon-amber-text">Limited-time offer: “{t.lto_text}”{t.lto_has_expiration ? " with a countdown" : ""}</div>
      )}
      {hasButton(t, "Copy Code") && (
        <Field label="Coupon code" required hint={`Shown with a copy button. Up to ${codeMax(t)} characters.`}>
          <Input value={s.coupon_code} maxLength={codeMax(t)} onChange={(e) => set({ coupon_code: e.target.value })} placeholder={(t.buttons || []).find((b) => b.button_type === "Copy Code")?.example_code || "e.g. WINTER25"} />
        </Field>
      )}
      {tt === "Limited-Time Offer" && !!t.lto_has_expiration && (
        <Field label="Offer ends" required hint="The countdown runs to this time (your local time).">
          <DateTimePicker value={s.offer_end} onChange={(v) => set({ offer_end: v })} min={localMin()} placeholder="Pick date and time" />
        </Field>
      )}

      {CAROUSELS.includes(tt) && (
        <Field label={`Cards (${s.cards.length})`} required hint={tt === "Media Carousel" ? `This carousel was approved with ${(t.cards || []).length} cards; send exactly that many.` : "2–10 products from the WhatsApp catalogue."}>
          <div className="space-y-2">
            {s.cards.map((c, i) => {
              const def = (t.cards || [])[i] || (t.cards || [])[0];
              const bodyNames = placeholders(def?.card_body || "");
              return (
                <div key={i} className="rounded-md border border-border p-2 space-y-2 min-w-0">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-medium text-ink-2 flex-1">Card {i + 1}</span>
                    {tt === "Product Carousel" && s.cards.length > 2 && (
                      <Button variant="ghost" size="icon" aria-label="Remove card" onClick={() => set({ cards: s.cards.filter((_, j) => j !== i) })}><Trash2 /></Button>
                    )}
                  </div>
                  {tt === "Product Carousel" ? (
                    <Input value={c.product_retailer_id} onChange={(e) => setCard(i, { product_retailer_id: e.target.value })} placeholder="Product id (Content ID in Commerce Manager)" />
                  ) : c.header_media ? (
                    <div className="flex items-center gap-2 min-w-0">
                      {fmt === "IMAGE" ? <img src={c.header_media} alt="" className="size-10 rounded object-cover shrink-0" /> : <span className="size-10 rounded bg-crayon-blue-tint flex items-center justify-center shrink-0"><Video className="size-4 text-crayon-blue-text" /></span>}
                      <span className="text-xs text-ink-1 truncate flex-1">{c.header_name || c.header_media}</span>
                      <Button variant="ghost" size="icon" aria-label="Remove media" onClick={() => setCard(i, { header_media: "", header_name: "" })}><Trash2 /></Button>
                    </div>
                  ) : (
                    <label className="flex items-center justify-center gap-2 h-10 rounded-md border border-dashed border-border-strong text-xs text-ink-3 hover:text-ink-1 cursor-pointer">
                      {uploadingCard === i ? <Loader2 className="size-4 animate-spin" /> : fmt === "IMAGE" ? <ImageIcon className="size-4" /> : <Upload className="size-4" />}
                      {uploadingCard === i ? "Uploading…" : `Upload ${fmt === "IMAGE" ? "image" : "video"}`}
                      <input type="file" className="hidden" accept={fmt === "IMAGE" ? "image/jpeg,image/png" : "video/mp4,video/3gpp"} onChange={(e) => { uploadCard(i, e.target.files?.[0]); e.target.value = ""; }} />
                    </label>
                  )}
                  {bodyNames.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs text-ink-3 break-words">{def?.card_body}</p>
                      {bodyNames.map((n, k) => (
                        <Input key={k} value={c.body[k] || ""} onChange={(e) => { const b = [...c.body]; b[k] = e.target.value; setCard(i, { body: b }); }} placeholder={`Card text {{${n}}}`} />
                      ))}
                    </div>
                  )}
                  {types.map((ty, j) => {
                    if (ty === "Quick Reply") return <Input key={j} value={c.buttons[j] || ""} onChange={(e) => { const b = [...c.buttons]; b[j] = e.target.value; setCard(i, { buttons: b }); }} placeholder={`“${cardButtonText(def, j)}” reply id (optional)`} />;
                    if (ty === "Visit Website" && cardButtonValue(def, j).includes("{{")) return <Input key={j} value={c.buttons[j] || ""} onChange={(e) => { const b = [...c.buttons]; b[j] = e.target.value; setCard(i, { buttons: b }); }} placeholder={`Link ending for ${cardButtonValue(def, j)}`} />;
                    return null;
                  })}
                </div>
              );
            })}
            {tt === "Product Carousel" && s.cards.length < 10 && (
              <Button size="sm" variant="ghost" onClick={() => set({ cards: [...s.cards, emptyCard(t, s.cards.length)] })}><Plus />Add card</Button>
            )}
          </div>
        </Field>
      )}

      {(tt === "Catalog" || tt === "Multi-Product") && (
        <Field label="Thumbnail product id" hint={tt === "Catalog" ? "Optional: the first catalogue item is used otherwise." : "Optional: the first product below is used otherwise."}>
          <Input value={s.thumbnail} onChange={(e) => set({ thumbnail: e.target.value })} placeholder="Content ID" />
        </Field>
      )}
      {tt === "Multi-Product" && (
        <Field label="Product sections" required hint="Up to 10 sections and 30 products. Product ids separated by commas.">
          <div className="space-y-2">
            {s.sections.map((sec, i) => (
              <div key={i} className="rounded-md border border-border p-2 space-y-1.5 min-w-0">
                <div className="flex items-center gap-2">
                  <Input value={sec.title} maxLength={24} onChange={(e) => set({ sections: s.sections.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) })} placeholder="Section title" />
                  {s.sections.length > 1 && <Button variant="ghost" size="icon" aria-label="Remove section" onClick={() => set({ sections: s.sections.filter((_, j) => j !== i) })}><Trash2 /></Button>}
                </div>
                <Input value={sec.ids} onChange={(e) => set({ sections: s.sections.map((x, j) => (j === i ? { ...x, ids: e.target.value } : x)) })} placeholder="SKU1, SKU2, SKU3" />
              </div>
            ))}
            {s.sections.length < 10 && <Button size="sm" variant="ghost" onClick={() => set({ sections: [...s.sections, { title: "", ids: "" }] })}><Plus />Add section</Button>}
          </div>
        </Field>
      )}
      {tt === "Single-Product" && (
        <Field label="Product id" required hint="Content ID of the product in the WhatsApp catalogue.">
          <Input value={s.product} onChange={(e) => set({ product: e.target.value })} placeholder="e.g. nqryix03ez" />
        </Field>
      )}
      {hasButton(t, "Voice Call") && (
        <div className="grid grid-cols-2 gap-2 min-w-0">
          <Field label="Call link minutes" hint="Optional, 1–43200"><Input type="number" min={1} max={43200} value={s.call_ttl} onChange={(e) => set({ call_ttl: e.target.value })} placeholder={String((t.buttons || []).find((b) => b.button_type === "Voice Call")?.ttl_minutes || 10080)} /></Field>
          <Field label="Call reference" hint="Optional, comes back with the call"><Input value={s.call_payload} maxLength={512} onChange={(e) => set({ call_payload: e.target.value })} /></Field>
        </div>
      )}
      {!!t.call_permission_request && <p className="text-xs text-ink-3">This template asks the person for permission to call them.</p>}
    </div>
  );
}
