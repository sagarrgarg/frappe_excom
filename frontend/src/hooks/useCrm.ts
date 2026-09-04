import { useCallback } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { useFrappeGetCall } from "@/lib/api";
import { toast } from "sonner";

/** Gateway vocabulary: refs are {doctype, name}; the UI never hardcodes field lists. */
export interface CrmRef { doctype: string; name: string }
export interface CrmRecordSummary extends CrmRef { title: string; customer_type?: string; pipeline_stage?: string; intake_stage?: string; status?: string; next_action_at?: string; opportunity_amount?: number; currency?: string }
export interface SchemaField { fieldname: string; label: string; fieldtype: string; options?: string; reqd: boolean; read_only: boolean; description?: string; priority: number }
export interface FieldSchema { doctype: string; customer_type: string; sections: { label: string; fields: SchemaField[]; collapsed?: boolean; meta?: boolean }[]; can_write: boolean; stages: string[]; compact?: boolean }
export interface GateStatus { flags: Record<string, number>; stages: string[]; current?: string; blocked: Record<string, string[]> }
export interface CrmOptions { customer_types: string[]; pipelines: Record<string, string[]>; stages: Record<string, { sales_stage: string | null; probability: number }>; intake_stages: string[] }

const M = "excom.excom.api.crm";

export function useCrmOptions() {
  const { data } = useFrappeGetCall<{ message: CrmOptions }>(`${M}.get_options`, undefined, undefined, { revalidateOnFocus: false });
  return data?.message ?? { customer_types: [], pipelines: {}, stages: {}, intake_stages: [] };
}

/** CRM records linked to an identity, precedence order (Customer → Opportunity → Lead). */
export function useIdentityRecords(omniIdentity: string | null) {
  const { data, mutate, isLoading } = useFrappeGetCall<{ message: CrmRecordSummary[] }>(
    omniIdentity ? `${M}.get_records_for_identity` : null,
    omniIdentity ? { omni_identity: omniIdentity } : undefined
  );
  return { records: data?.message ?? [], primary: data?.message?.[0] ?? null, refresh: mutate, isLoading };
}

export function useCrmRecord(ref: CrmRef | null) {
  const { data, mutate, isLoading, error } = useFrappeGetCall<{ message: any }>(
    ref ? `${M}.get_record` : null,
    ref ? { doctype: ref.doctype, name: ref.name } : undefined
  );
  return { record: data?.message ?? null, refresh: mutate, isLoading, error };
}

export function useFieldSchema(doctype: string | null, customerType: string) {
  const { data, isLoading } = useFrappeGetCall<{ message: FieldSchema }>(
    doctype ? `${M}.get_field_schema` : null,
    doctype ? { doctype, customer_type: customerType || "" } : undefined,
    undefined,
    { revalidateOnFocus: false }
  );
  return { schema: data?.message ?? null, isLoading };
}

export function useCrmActions(onChanged?: () => void) {
  const { call: promote } = useFrappePostCall(`${M}.promote_thread`);
  const { call: classify } = useFrappePostCall(`${M}.classify_lead`);
  const { call: convertCall } = useFrappePostCall(`${M}.convert`);
  const { call: setStageCall } = useFrappePostCall(`${M}.set_stage`);
  const { call: updateCall } = useFrappePostCall(`${M}.update_record`);
  const { call: overrideCall } = useFrappePostCall(`${M}.override_gate`);
  const { call: nextActionCall } = useFrappePostCall(`${M}.set_next_action`);
  const { call: bulkCall } = useFrappePostCall(`${M}.bulk_classify`);

  const wrap = useCallback(async <T,>(p: Promise<T>, ok?: string): Promise<T | null> => {
    try { const r = await p; if (ok) toast.success(ok); onChanged?.(); return r; }
    catch (e: any) { toast.error(serverMessage(e) || "Action failed"); return null; }
  }, [onChanged]);

  return {
    promoteThread: (thread: string, customer_type = "") => wrap(promote({ thread, customer_type }), "Lead created"),
    classifyLead: (name: string, customer_type: string, extra: Record<string, string> = {}) => wrap(classify({ name, customer_type, ...extra }), "Classified"),
    bulkClassify: (names: string[], customer_type: string) => wrap(bulkCall({ names: JSON.stringify(names), customer_type }), "Classified"),
    convert: (ref: CrmRef, target: string) => wrap(convertCall({ doctype: ref.doctype, name: ref.name, target }), `${target} created`),
    setStage: (name: string, stage: string, note = "") => wrap(setStageCall({ name, stage, note })),
    updateRecord: (ref: CrmRef, values: Record<string, unknown>) => wrap(updateCall({ doctype: ref.doctype, name: ref.name, values: JSON.stringify(values) }), "Saved"),
    overrideGate: (name: string, gate: string, reason: string) => wrap(overrideCall({ name, gate, reason }), "Gate overridden"),
    setNextAction: (ref: CrmRef, at: string) => wrap(nextActionCall({ doctype: ref.doctype, name: ref.name, next_action_at: at }), "Next action set"),
  };
}

export function serverMessage(e: any): string {
  try {
    if (e?._server_messages) {
      const parsed = JSON.parse(e._server_messages);
      const inner = typeof parsed?.[0] === "string" ? JSON.parse(parsed[0]) : parsed?.[0];
      return (inner?.message || "").replace(/<[^>]+>/g, "");
    }
  } catch { /* fallthrough */ }
  return e?.message || e?.exception || "";
}
