import { useMemo, useState } from "react";
import { Bug } from "lucide-react";
import { PageFrame } from "../components/shell/PageFrame";
import { ThreadRow } from "../components/inbox/ThreadRow";
import { MessageBubble } from "../components/record/MessageBubble";
import { RecordHeader } from "../components/record/RecordHeader";
import { ContextStrip } from "../components/record/ContextStrip";
import { Chip, Badge, Avatar, SegmentedControl, Input, Field, Button, EmptyState } from "../components/primitives";
import { STRESS_CONTACT, STRESS_MESSAGES, STRESS_TAGS, STRESS_WIDTHS } from "../fixtures/stress";
import { CHANNEL_ORDER, channelMeta } from "../lib/channels";
import { cn } from "../components/ui/utils";

/**
 * /dev/stress — every list/detail component with the worst-case record at once (UX-001 §10.4).
 * Width toggles let you eyeball overlap at each QA width inside one page.
 */
export function StressRoute() {
  const [w, setW] = useState<string>("auto");
  const noop = useMemo(() => ({ archive: () => {}, unarchive: () => {}, assignToMe: () => {}, toggleRead: () => {}, spam: () => {}, del: () => {}, copy: () => {}, snooze: () => {} }), []);
  const style = w === "auto" ? undefined : { width: Number(w), maxWidth: "100%" };
  return (
    <PageFrame title="Stress harness" icon={<Bug />} wide>
      <div className="chip-row mb-3">
        {["auto", ...STRESS_WIDTHS.map(String)].map((x) => (
          <Chip key={x} label={x === "auto" ? "auto" : `${x}px`} accent={x === w ? "blue" : "neutral"} onClick={() => setW(x)} />
        ))}
      </div>

      <div className="space-y-4" style={style}>
        <Section title="ThreadRow — list column at 320 / 360 / 100%">
          {[320, 360, 0].map((lw) => (
            <div key={lw} className="border border-border rounded-md overflow-hidden mb-2" style={lw ? { width: lw } : undefined}>
              <ThreadRow c={STRESS_CONTACT} selected={lw === 360} onOpen={() => {}} actions={noop} coarse={false} isSystemManager />
            </div>
          ))}
        </Section>

        <Section title="RecordHeader + ContextStrip (container-width collapse)">
          {[360, 640, 990, 0].map((cw) => (
            <div key={cw} className="border border-border rounded-md overflow-hidden mb-2" style={cw ? { width: cw } : undefined}>
              <RecordHeader contact={STRESS_CONTACT} record={{ doctype: "Lead", name: "CRM-LEAD-2026-000042", title: STRESS_CONTACT.contactName }} onBack={() => {}} showBack menuGroups={[]} />
              <ContextStrip contact={STRESS_CONTACT} record={{ doctype: "Lead", name: "CRM-LEAD-2026-000042", title: STRESS_CONTACT.contactName }} amount="₹1,23,45,678" />
            </div>
          ))}
        </Section>

        <Section title="MessageBubble — every type, both directions">
          <div className="border border-border rounded-md p-3 space-y-3 bg-surface">
            {STRESS_MESSAGES.map((m) => (
              <MessageBubble key={m.id} message={m} contactName={STRESS_CONTACT.contactName} onContextMenu={() => {}} onReply={() => {}} onRetry={() => {}} onRefresh={() => {}} retrying={false} />
            ))}
          </div>
        </Section>

        <Section title="Chips / badges / avatars / tabs">
          <div className="chip-row mb-2">
            {CHANNEL_ORDER.map((c) => { const m = channelMeta(c); return <Chip key={c} icon={<m.icon />} label={m.label} accent={m.accent} count={1234} />; })}
            {STRESS_TAGS.map((t) => <Chip key={t.tag} label={t.tag_name} dotColor={t.color} onRemove={() => {}} />)}
            <Chip label="A very long chip label that must truncate rather than wrap the row" accent="amber" />
          </div>
          <div className="flex items-center gap-2 mb-2">
            <Badge solid count={7} /><Badge solid count={123} /><Badge accent="rose" count={99999} /><Badge accent="green">Sent</Badge>
            <Avatar name={STRESS_CONTACT.contactName} size={20} /><Avatar name={STRESS_CONTACT.contactName} size={32} /><Avatar name={STRESS_CONTACT.contactName} size={56} />
          </div>
          <SegmentedControl value="chat" onChange={() => {}} segments={[{ value: "chat", label: "Chat" }, { value: "tasks", label: "Tasks", count: 12 }, { value: "notes", label: "Notes", count: 3 }, { value: "activity", label: "Activity" }, { value: "details", label: "Details", disabled: true, hint: "P3" }]} />
        </Section>

        <Section title="Form controls">
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(0,minmax(180px,1fr)))]">
            <Field label="Phone" required><Input defaultValue="+91 987654321012345" /></Field>
            <Field label="Company" hint="48 characters"><Input defaultValue={STRESS_CONTACT.contactInfo.company} /></Field>
            <Field label="Amount"><Input defaultValue="₹1,23,45,678.00" className="tabular-nums" /></Field>
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            <Button variant="primary">Primary</Button><Button>Default</Button><Button variant="subtle">Subtle</Button><Button variant="ghost">Ghost</Button><Button variant="danger">Danger</Button>
          </div>
        </Section>

        <Section title="Empty state">
          <EmptyState icon={<Bug />} title="Nothing to show" hint="This copy is intentionally long to verify that hints wrap inside their max width and never overflow the container at 360px." compact />
        </Section>
      </div>
    </PageFrame>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className={cn("min-w-0")}>
      <h2 className="text-xs text-ink-3 mb-1.5">{title}</h2>
      {children}
    </section>
  );
}
