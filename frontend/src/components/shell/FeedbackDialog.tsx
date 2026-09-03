import { useState } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import { Modal, Button, Textarea, Field } from "../primitives";

/** One-line feedback box. Captures route, viewport and DPR automatically; lands in Excom Settings → UI feedback. */
export function FeedbackDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [text, setText] = useState("");
  const { call, loading } = useFrappePostCall("excom.excom.api.record.submit_ui_feedback");
  const submit = async () => {
    if (!text.trim()) return;
    try {
      await call({ message: text.trim(), route: window.location.pathname + window.location.search, viewport: `${window.innerWidth}×${window.innerHeight}`, dpr: String(window.devicePixelRatio || 1), ui: "next" });
      toast.success("Thanks — feedback sent"); setText(""); onOpenChange(false);
    } catch { toast.error("Could not send feedback"); }
  };
  return (
    <Modal open={open} onOpenChange={onOpenChange} title="Send feedback"
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" onClick={submit} disabled={loading || !text.trim()}>Send</Button></>}>
      <Field label="What is wrong, missing or confusing?" hint="Route, viewport and pixel ratio are attached automatically.">
        <Textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Couldn't find the template picker" rows={3} autoFocus />
      </Field>
    </Modal>
  );
}
