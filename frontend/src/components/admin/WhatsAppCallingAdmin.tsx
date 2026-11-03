import { useEffect, useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { AlertTriangle, Info, Plus, RefreshCw, Save, Trash2, PhoneCall } from "lucide-react";
import { toast } from "sonner";
import { useFrappeGetCall } from "@/lib/api";
import { toastError } from "../ErrorDialog";
import { Button, EmptyState, Field, Input, Select } from "../primitives";

/**
 * WhatsApp Business Calling settings, per number (`POST /{phone id}/settings`, the `calling` object).
 *
 * Meta replaces the whole call-hours block on every save, holidays included, so this screen always
 * sends the full set it shows. "Read from Meta" first is the safe order: the copy Excom holds may be
 * older than a change made in WhatsApp Manager.
 *
 * The call icon countries are sent only when edited here: sending the stored copy back would wipe a
 * restriction set in WhatsApp Manager since the last read.
 */

interface Hours { day_of_week: string; open_time: string; close_time: string }
interface Holiday { date: string; start_time: string; end_time: string }
interface Settings {
  status: string;
  call_icon_visibility: string;
  call_icon_countries: string;
  callback_permission_status: string;
  call_hours_status: string;
  timezone_id: string;
  weekly_operating_hours: Hours[];
  holiday_schedule: Holiday[];
  sip_status: string;
  restrictions: string;
  rate_per_minute: number;
  synced_on: string;
}
interface Warning { level: "warning" | "info"; text: string }
interface NumberRow { account: string; account_name: string; business_number: string; messaging_limit: string; settings: Settings; warnings: Warning[] }

const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"];

export function WhatsAppCallingAdmin() {
  const { data, mutate } = useFrappeGetCall<{ message: NumberRow[] }>(
    "excom.excom.api.whatsapp_calling.numbers", undefined, "wa-calling-numbers", { revalidateOnFocus: false });
  const numbers = data?.message ?? [];
  const [account, setAccount] = useState("");
  const active = numbers.find((n) => n.account === account) || numbers[0];
  const [form, setForm] = useState<Settings | null>(null);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [iconsEdited, setIconsEdited] = useState(false);
  const { call: read, loading: reading } = useFrappePostCall("excom.excom.api.whatsapp_calling.get_settings");
  const { call: save, loading: saving } = useFrappePostCall("excom.excom.api.whatsapp_calling.save_settings");

  useEffect(() => {
    if (active) {
      setForm({ ...active.settings });
      setWarnings(active.warnings);
      setIconsEdited(false);
    }
  }, [active?.account, active?.settings?.synced_on]);

  if (!numbers.length) {
    return (
      <div className="p-3">
        <EmptyState icon={<PhoneCall />} title="No WhatsApp number" hint="Add a WhatsApp Channel Account first; calling is set per number." />
      </div>
    );
  }
  if (!form || !active) return <p className="p-3 text-sm text-ink-3">Loading…</p>;

  const set = (patch: Partial<Settings>) => setForm({ ...form, ...patch });
  const setHour = (i: number, patch: Partial<Hours>) =>
    set({ weekly_operating_hours: form.weekly_operating_hours.map((h, j) => (j === i ? { ...h, ...patch } : h)) });
  const setHoliday = (i: number, patch: Partial<Holiday>) =>
    set({ holiday_schedule: form.holiday_schedule.map((h, j) => (j === i ? { ...h, ...patch } : h)) });

  const readFromMeta = async () => {
    try {
      const r = await read({ account: active.account });
      setForm({ ...r.message.settings });
      setWarnings(r.message.warnings);
      setIconsEdited(false);
      toast.success("Read from Meta");
      void mutate();
    } catch (e) {
      toastError(e, "Meta's calling settings could not be read");
    }
  };

  const saveToMeta = async () => {
    const values: Record<string, unknown> = {
      status: form.status,
      call_icon_visibility: form.call_icon_visibility,
      // Only when edited, with the flag the server requires to send call_icons at all.
      ...(iconsEdited ? { call_icon_countries: form.call_icon_countries, call_icons_edited: 1 } : {}),
      callback_permission_status: form.callback_permission_status,
      call_hours_status: form.call_hours_status,
      timezone_id: form.timezone_id,
      weekly_operating_hours: form.weekly_operating_hours,
      holiday_schedule: form.holiday_schedule,
      rate_per_minute: Number(form.rate_per_minute) || 0,
    };
    try {
      const r = await save({ account: active.account, values: JSON.stringify(values) });
      setForm({ ...r.message.settings });
      setWarnings(r.message.warnings);
      setIconsEdited(false);
      toast.success("Saved to Meta", { description: "WhatsApp apps can take up to 7 days to show changes; Meta applies them at once." });
      void mutate();
    } catch (e) {
      toastError(e, "Meta refused the calling settings");
    }
  };

  const onOff = (value: string, onChange: (v: string) => void) => (
    <Select value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">— not set —</option>
      <option value="ENABLED">Enabled</option>
      <option value="DISABLED">Disabled</option>
    </Select>
  );

  return (
    <div className="p-3 space-y-4 max-w-3xl">
      <div className="flex items-center gap-2 flex-wrap">
        {numbers.length > 1 && (
          <Select value={active.account} onChange={(e) => setAccount(e.target.value)} className="max-w-xs">
            {numbers.map((n) => <option key={n.account} value={n.account}>{n.account_name}{n.business_number ? ` · ${n.business_number}` : ""}</option>)}
          </Select>
        )}
        <span className="text-xs text-ink-3">Messaging limit {active.messaging_limit || "unknown"} · read {form.synced_on ? form.synced_on.slice(0, 16) : "never"}</span>
        <div className="ml-auto flex gap-2">
          <Button size="sm" variant="ghost" disabled={reading} onClick={readFromMeta}><RefreshCw className={reading ? "animate-spin" : ""} />Read from Meta</Button>
          <Button size="sm" variant="primary" disabled={saving} onClick={saveToMeta}><Save />Save to Meta</Button>
        </div>
      </div>

      {warnings.length > 0 && (
        <ul className="space-y-1.5">
          {warnings.map((w, i) => (
            <li key={i} className={`flex items-start gap-1.5 rounded-lg border px-3 py-2 text-xs ${w.level === "warning" ? "border-crayon-amber-base/40 bg-crayon-amber-tint text-crayon-amber-text" : "border-border bg-surface-sunken text-ink-2"}`}>
              {w.level === "warning" ? <AlertTriangle className="mt-0.5 size-3.5 shrink-0" /> : <Info className="mt-0.5 size-3.5 shrink-0" />}
              <span>{w.text}</span>
            </li>
          ))}
        </ul>
      )}

      <section className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
        <Field label="Calling">{onOff(form.status, (v) => set({ status: v }))}</Field>
        <Field label="Call icon in chat and profile" hint="Hidden still lets call buttons in messages work.">
          <Select value={form.call_icon_visibility || ""} onChange={(e) => set({ call_icon_visibility: e.target.value })}>
            <option value="">— not set —</option>
            <option value="DEFAULT">Shown</option>
            <option value="DISABLE_ALL">Hidden (no unsolicited calls)</option>
          </Select>
        </Field>
        <Field label="Show the call icon only in" hint="Two-letter country codes, comma separated; empty for everywhere.">
          <Input
            value={form.call_icon_countries}
            onChange={(e) => {
              setIconsEdited(true);
              set({ call_icon_countries: e.target.value });
            }}
            placeholder="IN, AE"
          />
        </Field>
        <Field label="Ask callers for call permission" hint="After a call, the person is asked to let you call back.">{onOff(form.callback_permission_status, (v) => set({ callback_permission_status: v }))}</Field>
        <Field label="Business-initiated rate per minute" hint="For the cost estimate only. India list rate ₹0.3885 ($0.0053); calls people make to you are free.">
          <Input type="number" step="0.0001" min="0" value={String(form.rate_per_minute ?? 0)} onChange={(e) => set({ rate_per_minute: Number(e.target.value) })} />
        </Field>
        <Field label="SIP" hint="If enabled, calls go to the SIP server and Excom cannot take them.">
          <Input value={form.sip_status || "not used"} readOnly />
        </Field>
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm text-ink-1 font-medium">Call hours</h3>
          <div className="w-40">{onOff(form.call_hours_status, (v) => set({ call_hours_status: v }))}</div>
          <Input className="max-w-[14rem]" value={form.timezone_id} onChange={(e) => set({ timezone_id: e.target.value })} placeholder="Asia/Kolkata" aria-label="Time zone" />
        </div>
        <p className="text-xs text-ink-3">Up to two ranges a day, 24-hour times like 0930. Outside them people see "chat" or "request a callback".</p>
        {form.weekly_operating_hours.map((h, i) => (
          <div key={i} className="flex items-center gap-2">
            <Select className="w-40" value={h.day_of_week} onChange={(e) => setHour(i, { day_of_week: e.target.value })}>
              {DAYS.map((d) => <option key={d} value={d}>{d.charAt(0) + d.slice(1).toLowerCase()}</option>)}
            </Select>
            <Input className="w-24" value={h.open_time} onChange={(e) => setHour(i, { open_time: e.target.value })} aria-label="Opens" />
            <span className="text-xs text-ink-3">to</span>
            <Input className="w-24" value={h.close_time} onChange={(e) => setHour(i, { close_time: e.target.value })} aria-label="Closes" />
            <Button size="icon-sm" variant="ghost" aria-label="Remove" onClick={() => set({ weekly_operating_hours: form.weekly_operating_hours.filter((_, j) => j !== i) })}><Trash2 /></Button>
          </div>
        ))}
        <Button size="sm" variant="ghost" onClick={() => set({ weekly_operating_hours: [...form.weekly_operating_hours, { day_of_week: "MONDAY", open_time: "0930", close_time: "1830" }] })}><Plus />Add opening time</Button>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm text-ink-1 font-medium">Holidays <span className="text-xs text-ink-3 font-normal">({form.holiday_schedule.length}/20, no past dates)</span></h3>
        {form.holiday_schedule.map((h, i) => (
          <div key={i} className="flex items-center gap-2">
            <Input className="w-40" type="date" value={h.date} onChange={(e) => setHoliday(i, { date: e.target.value })} aria-label="Date" />
            <Input className="w-24" value={h.start_time} onChange={(e) => setHoliday(i, { start_time: e.target.value })} aria-label="From" />
            <span className="text-xs text-ink-3">to</span>
            <Input className="w-24" value={h.end_time} onChange={(e) => setHoliday(i, { end_time: e.target.value })} aria-label="Until" />
            <Button size="icon-sm" variant="ghost" aria-label="Remove" onClick={() => set({ holiday_schedule: form.holiday_schedule.filter((_, j) => j !== i) })}><Trash2 /></Button>
          </div>
        ))}
        <Button size="sm" variant="ghost" disabled={form.holiday_schedule.length >= 20} onClick={() => set({ holiday_schedule: [...form.holiday_schedule, { date: "", start_time: "0000", end_time: "2359" }] })}><Plus />Add holiday</Button>
      </section>

      <p className="text-xs text-ink-3">
        STUN / TURN servers for agents' browsers are in Excom settings → Calls → WhatsApp calling. Meta provides none.
        {form.restrictions ? ` Restrictions: ${form.restrictions}` : ""}
      </p>
    </div>
  );
}
