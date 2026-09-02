import { useFrappeGetCall } from "frappe-react-sdk";

export interface ERPDocument {
  name: string;
  grand_total: number;
  status: string;
  currency: string;
  transaction_date?: string;
  posting_date?: string;
  outstanding_amount?: number;
  customer_name?: string;
  supplier_name?: string;
}

export interface RelatedDocuments {
  quotations: ERPDocument[];
  sales_orders: ERPDocument[];
  delivery_notes: ERPDocument[];
  sales_invoices: ERPDocument[];
  rfqs: ERPDocument[];
  purchase_orders: ERPDocument[];
  purchase_receipts: ERPDocument[];
  purchase_invoices: ERPDocument[];
}

const EMPTY: RelatedDocuments = {
  quotations: [],
  sales_orders: [],
  delivery_notes: [],
  sales_invoices: [],
  rfqs: [],
  purchase_orders: [],
  purchase_receipts: [],
  purchase_invoices: [],
};

/**
 * Fetches all ERP transaction documents linked to an Omni Identity
 * through its linked Customer/Supplier entities.
 */
export function useRelatedDocuments(omniIdentity: string | null) {
  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message?: RelatedDocuments;
  }>(
    omniIdentity
      ? "excom.excom.api.chat.get_related_documents"
      : null,
    omniIdentity ? { omni_identity: omniIdentity } : undefined,
  );

  return {
    documents: data?.message ?? EMPTY,
    error,
    isLoading,
    refresh: mutate,
  };
}

export function getDocDate(doc: ERPDocument): string {
  return doc.posting_date || doc.transaction_date || "";
}

export function getDocPartyName(doc: ERPDocument): string {
  return doc.customer_name || doc.supplier_name || "";
}
