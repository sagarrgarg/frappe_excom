import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { Modal, Button, Textarea, Field } from "../primitives";

/**
 * One-line feedback box shown when switching UI (UX-001 §10.2). Captures route, viewport and DPR automatically.
 */
export function FeedbackDialog({ open, onOpenChange, onDone, target }: { open: boolean; onOpenChange: (v: boolean) => void; onDone: () => void; target: "legacy" | "next" }) {
  const [text, setText] = useState("");
  const { call, loading } = useFrappePostCall("excom.excom.api.record.submit_ui_feedback");
  const submit = async () => {
    if (text.trim()) {
      try {
        await call({
          message: text.trim(),
          route: window.location.pathname + window.location.search,
          viewport: `${window.innerWidth}×${window.innerHeight}`,
          dpr: String(window.devicePixelRatio || 1),
          ui: target === "legacy" ? "next→legacy" : "legacy→next",
        });
      } catch {
        toast.error("Could not send feedback — switching anyway");
      }
    }
    onDone();
  };
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title={target === "legacy" ? "Switch to the old UI" : "Try the new UI"}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" onClick={submit} disabled={loading}>{text.trim() ? "Send & switch" : "Switch"}</Button>
        </>
      }
    >
      <Field label="What made you switch? (optional)" hint="Route, viewport and pixel ratio are attached automatically.">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Couldn't find the template picker" rows={3} autoFocus />
      </Field>
    </Modal>
  );
}
