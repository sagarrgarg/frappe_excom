import { useState, useEffect, useCallback, useRef } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Cog, Plus, ToggleLeft, ToggleRight, Pencil, Play, Check, X, Search, ChevronDown, RefreshCw, Loader2 } from "lucide-react";
import { Button, Input, Modal, Select, Textarea, EmptyState, Chip, Badge } from "./primitives";
import { AdminPage } from "./shell/AdminPage";
import { toast } from "sonner";

interface Rule {
  name: string;
  rule_name: string;
  enabled: number;
  subscriber_list: string;
  reference_doctype: string;
  event: string;
  condition: string;
  identity_field: string;
  identity_field_type: string;
  description: string;
}

interface TestResult {
  condition_match: boolean;
  entity_name: string | null;
  identity_name: string | null;
  already_subscribed: boolean;
  would_subscribe: boolean;
}

interface DropdownOption {
  value: string;
  label: string;
  sublabel?: string;
}

function SearchableSelect({
  value,
  onChange,
  fetchOptions,
  placeholder,
  displayValue,
}: {
  value: string;
  onChange: (val: string) => void;
  fetchOptions: (search: string) => Promise<DropdownOption[]>;
  placeholder: string;
  displayValue?: string;
}) {
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<DropdownOption[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = setTimeout(async () => {
      const res = await fetchOptions(search);
      setOptions(res);
    }, 150);
    return () => clearTimeout(timer);
  }, [search, fetchOptions]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => { setOpen(!open); setSearch(""); }}
        className="w-full flex items-center justify-between bg-surface-sunken border border-border-strong rounded-lg px-3 py-2 text-sm text-left focus:outline-none focus:border-border-strong"
      >
        <span className={value ? "text-ink-1" : "text-ink-3"}>
          {displayValue || value || placeholder}
        </span>
        <ChevronDown className="w-4 h-4 text-ink-3 shrink-0" />
      </button>
      {open && (
        <div className="absolute left-0 right-0 mt-1 bg-surface-sunken border border-border-strong rounded-lg shadow-ex z-20 max-h-56 overflow-hidden flex flex-col">
          <div className="p-2 border-b border-border-strong shrink-0">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-ink-3" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search..."
                autoFocus
                className="w-full bg-surface border border-border-strong rounded px-3 py-1.5 pl-8 text-xs text-ink-1 focus:outline-none focus:border-border-strong"
              />
            </div>
          </div>
          <div className="overflow-y-auto flex-1">
            {options.length === 0 ? (
              <div className="px-3 py-2 text-xs text-ink-3 text-center">
                {search ? "No results" : "Type to search..."}
              </div>
            ) : (
              options.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => { onChange(opt.value); setOpen(false); }}
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-surface-active transition-colors ${
                    opt.value === value ? "bg-surface-active text-crayon-blue-text" : "text-ink-1"
                  }`}
                >
                  {opt.label}
                  {opt.sublabel && (
                    <span className="text-xs text-ink-3 ml-2">{opt.sublabel}</span>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function RuleFormDialog({
  existing,
  onSave,
  onClose,
}: {
  existing?: Rule;
  onSave: (data: Partial<Rule>) => void;
  onClose: () => void;
}) {
  const [form, setForm] = useState({
    rule_name: existing?.rule_name || "",
    subscriber_list: existing?.subscriber_list || "",
    reference_doctype: existing?.reference_doctype || "",
    event: existing?.event || "After Insert",
    identity_field: existing?.identity_field || "",
    identity_field_type: existing?.identity_field_type || "Customer",
    condition: existing?.condition || "",
    description: existing?.description || "",
  });

  const [linkFields, setLinkFields] = useState<DropdownOption[]>([]);

  const { call: searchDoctypes } = useFrappePostCall("excom.excom.api.subscriber_rules.search_doctypes");
  const { call: searchLists } = useFrappePostCall("excom.excom.api.subscriber_rules.search_subscriber_lists");
  const { call: fetchFields } = useFrappePostCall("excom.excom.api.subscriber_rules.get_doctype_fields");

  useEffect(() => {
    if (!form.reference_doctype) { setLinkFields([]); return; }
    fetchFields({ doctype: form.reference_doctype }).then((res) => {
      const fields = ((res as any)?.message || []).map((f: any) => ({
        value: f.fieldname,
        label: `${f.label} (${f.fieldname})`,
        sublabel: f.options,
      }));
      setLinkFields(fields);
    }).catch(() => setLinkFields([]));
  }, [form.reference_doctype]);

  const update = (key: string, value: string) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const fetchDoctypeOptions = useCallback(async (q: string): Promise<DropdownOption[]> => {
    try {
      const res = await searchDoctypes({ search: q, limit: 20 });
      return ((res as any)?.message || []).map((d: any) => ({
        value: d.name,
        label: d.name,
        sublabel: d.module,
      }));
    } catch { return []; }
  }, [searchDoctypes]);

  const fetchListOptions = useCallback(async (q: string): Promise<DropdownOption[]> => {
    try {
      const res = await searchLists({ search: q, limit: 20 });
      return ((res as any)?.message || []).map((d: any) => ({
        value: d.name,
        label: d.list_name || d.name,
      }));
    } catch { return []; }
  }, [searchLists]);

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={existing ? "Edit rule" : "Create rule"} width="max-w-xl"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={() => form.rule_name.trim() && onSave(form)} disabled={!form.rule_name.trim() || !form.subscriber_list.trim()}>{existing ? "Save" : "Create"}</Button></>}>
        <div className="space-y-3">
          <Field label="Rule Name">
            <Input
              value={form.rule_name}
              onChange={(e) => update("rule_name", e.target.value)}
              placeholder="e.g. Agra Store POS Customers"
              className="bg-surface-sunken border-border-strong text-ink-1"
              autoFocus
            />
          </Field>
          <Field label="Subscriber List">
            <SearchableSelect
              value={form.subscriber_list}
              onChange={(v) => update("subscriber_list", v)}
              fetchOptions={fetchListOptions}
              placeholder="Select subscriber list..."
            />
          </Field>
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr))]">
            <Field label="Reference DocType">
              <SearchableSelect
                value={form.reference_doctype}
                onChange={(v) => {
                  update("reference_doctype", v);
                  update("identity_field", "");
                }}
                fetchOptions={fetchDoctypeOptions}
                placeholder="Select DocType..."
              />
            </Field>
            <Field label="Event">
              <Select value={form.event} onChange={(e) => update("event", e.target.value)}>
                <option>After Insert</option>
                <option>After Save</option>
                <option>After Submit</option>
              </Select>
            </Field>
          </div>
          <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr))]">
            <Field label="Identity Field">
              {linkFields.length > 0 ? (
                <Select
                  value={form.identity_field}
                  onChange={(e) => {
                    const fieldname = e.target.value;
                    update("identity_field", fieldname);
                    const match = linkFields.find((f) => f.value === fieldname);
                    if (match?.sublabel) update("identity_field_type", match.sublabel);
                  }}
                >
                  <option value="">Select field...</option>
                  {linkFields.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  value={form.identity_field}
                  onChange={(e) => update("identity_field", e.target.value)}
                  placeholder={form.reference_doctype ? "No link fields found, type manually" : "Select DocType first"}
                  className="bg-surface-sunken border-border-strong text-ink-1"
                />
              )}
            </Field>
            <Field label="Identity Field Type">
              <Select value={form.identity_field_type} onChange={(e) => update("identity_field_type", e.target.value)}>
                <option>Customer</option>
                <option>Supplier</option>
                <option>Lead</option>
                <option>Contact</option>
              </Select>
            </Field>
          </div>
          <Field label="Condition (Python expression)">
            <Textarea value={form.condition} onChange={(e) => update("condition", e.target.value)} placeholder='doc.territory == "Agra" and doc.customer' rows={3} className="font-mono" />
          </Field>
          <Field label="Description">
            <Input
              value={form.description}
              onChange={(e) => update("description", e.target.value)}
              placeholder="What does this rule do?"
              className="bg-surface-sunken border-border-strong text-ink-1"
            />
          </Field>
        </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs text-ink-3 mb-1 block">{label}</label>
      {children}
    </div>
  );
}

function TestRuleDialog({
  rule,
  onClose,
}: {
  rule: Rule;
  onClose: () => void;
}) {
  const [docName, setDocName] = useState("");
  const [result, setResult] = useState<TestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const { call: testRule } = useFrappePostCall("excom.excom.api.subscriber_rules.test_rule");

  const runTest = async () => {
    if (!docName.trim()) return;
    setLoading(true);
    try {
      const res = await testRule({ name: rule.name, doc_name: docName.trim() });
      setResult((res as any)?.message || null);
    } catch {
      toast.error("Test failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title="Test rule" footer={<Button variant="ghost" onClick={onClose}>Close</Button>}>
        <p className="text-xs text-ink-3 mb-3">
          Dry-run <span className="text-ink-2">{rule.rule_name}</span> against a{" "}
          {rule.reference_doctype} document.
        </p>
        <div className="flex gap-2 mb-3">
          <Input
            value={docName}
            onChange={(e) => setDocName(e.target.value)}
            placeholder={`${rule.reference_doctype} name...`}
            className="bg-surface-sunken border-border-strong text-ink-1 flex-1"
            autoFocus
          />
          <Button variant="primary" onClick={runTest} disabled={loading || !docName.trim()}><Play />Run</Button>
        </div>

        {result && (
          <div className="bg-surface-sunken rounded-lg p-3 space-y-2">
            <ResultRow label="Condition Match" ok={result.condition_match} />
            <ResultRow
              label="Entity Resolved"
              ok={!!result.entity_name}
              detail={result.entity_name || "Not found"}
            />
            <ResultRow
              label="Identity Found"
              ok={!!result.identity_name}
              detail={result.identity_name || "No identity"}
            />
            <ResultRow
              label="Already Subscribed"
              ok={!result.already_subscribed}
              detail={result.already_subscribed ? "Yes" : "No"}
            />
            <div className="border-t border-border-strong pt-2 mt-2">
              <ResultRow
                label="Would Subscribe"
                ok={result.would_subscribe}
                detail={result.would_subscribe ? "Yes" : "No"}
                highlight
              />
            </div>
          </div>
        )}

    </Modal>
  );
}

function ResultRow({
  label,
  ok,
  detail,
  highlight,
}: {
  label: string;
  ok: boolean;
  detail?: string;
  highlight?: boolean;
}) {
  return (
    <div className={`flex items-center justify-between text-sm ${highlight ? "font-medium" : ""}`}>
      <span className="text-ink-2">{label}</span>
      <span className="flex items-center gap-1.5">
        {detail && <span className="text-xs text-ink-3 mr-1">{detail}</span>}
        {ok ? (
          <Check className="w-4 h-4 text-crayon-green-text" />
        ) : (
          <X className="w-4 h-4 text-crayon-rose-text" />
        )}
      </span>
    </div>
  );
}

export function SubscriberRulesPage({
  onNavigateBack,
  embedded,
}: {
  onNavigateBack: () => void;
  embedded?: boolean;
}) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [editingRule, setEditingRule] = useState<Rule | undefined>();
  const [testingRule, setTestingRule] = useState<Rule | null>(null);

  const [backfilling, setBackfilling] = useState<string | null>(null);

  const { call: fetchRules } = useFrappePostCall("excom.excom.api.subscriber_rules.get_rules");
  const { call: createRule } = useFrappePostCall("excom.excom.api.subscriber_rules.create_rule");
  const { call: updateRule } = useFrappePostCall("excom.excom.api.subscriber_rules.update_rule");
  const { call: toggleRule } = useFrappePostCall("excom.excom.api.subscriber_rules.toggle_rule");
  const { call: applyToExisting } = useFrappePostCall("excom.excom.api.subscriber_rules.apply_rule_to_existing");

  const loadRules = useCallback(async () => {
    try {
      const res = await fetchRules({});
      setRules((res as any)?.message || []);
    } catch {
      toast.error("Failed to load rules");
    }
  }, [fetchRules]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  return (
    <AdminPage title="Subscriber rules" icon={<Cog />} onBack={onNavigateBack} embedded={embedded}
      actions={<><Badge accent="amber" count={rules.filter((r) => r.enabled).length} /><Button variant="primary" size="sm" onClick={() => { setEditingRule(undefined); setShowForm(true); }}><Plus />New rule</Button></>}>
      <div>
        {rules.length === 0 ? (
          <EmptyState icon={<Cog />} title="No subscriber rules yet" hint="Rules auto-subscribe contacts to lists when documents are created or submitted." />
        ) : (
          <div className="space-y-3 max-w-[900px] mx-auto">
            {rules.map((rule) => (
              <div
                key={rule.name}
                className={`bg-surface border rounded-lg p-3 min-w-0 ${
                  rule.enabled ? "border-border" : "border-border opacity-60"
                }`}
              >
                <div className="flex items-start justify-between gap-2 mb-2 min-w-0">
                  <div className="min-w-0 flex-1">
                    <h3 className="text-sm font-medium text-ink-1 truncate">{rule.rule_name}</h3>
                    {rule.description && (
                      <p className="text-xs text-ink-3 mt-0.5 line-clamp-2">{rule.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={async () => {
                        if (!rule.enabled) {
                          toast.error("Enable the rule first");
                          return;
                        }
                        setBackfilling(rule.name);
                        try {
                          const res = await applyToExisting({ name: rule.name });
                          const data = (res as any)?.message || {};
                          toast.success(
                            `Applied: ${data.added} added, ${data.skipped} skipped${data.errors ? `, ${data.errors} errors` : ""}`
                          );
                        } catch {
                          toast.error("Failed to apply rule");
                        } finally {
                          setBackfilling(null);
                        }
                      }}
                      disabled={backfilling === rule.name}
                      className="p-1.5 rounded hover:bg-surface-sunken text-ink-3 hover:text-crayon-amber-text disabled:opacity-40"
                      title="Apply to all existing records"
                    >
                      {backfilling === rule.name ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RefreshCw className="w-4 h-4" />
                      )}
                    </button>
                    <button
                      onClick={() => setTestingRule(rule)}
                      className="p-1.5 rounded hover:bg-surface-sunken text-ink-3 hover:text-crayon-blue-text"
                      title="Test Rule"
                    >
                      <Play className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => {
                        setEditingRule(rule);
                        setShowForm(true);
                      }}
                      className="p-1.5 rounded hover:bg-surface-sunken text-ink-3 hover:text-ink-1"
                      title="Edit"
                    >
                      <Pencil className="w-4 h-4" />
                    </button>
                    <button
                      onClick={async () => {
                        await toggleRule({ name: rule.name, enabled: rule.enabled ? 0 : 1 });
                        toast.success(rule.enabled ? "Rule disabled" : "Rule enabled");
                        loadRules();
                      }}
                      className="p-1.5 rounded hover:bg-surface-sunken text-ink-3 hover:text-ink-1"
                      title={rule.enabled ? "Disable" : "Enable"}
                    >
                      {rule.enabled ? (
                        <ToggleRight className="w-5 h-5 text-crayon-green-text" />
                      ) : (
                        <ToggleLeft className="w-5 h-5" />
                      )}
                    </button>
                  </div>
                </div>

                <div className="chip-row mt-2 h-6">
                  <Chip size="sm" label={`DocType: ${rule.reference_doctype}`} />
                  <Chip size="sm" label={`Event: ${rule.event}`} />
                  <Chip size="sm" label={`List: ${rule.subscriber_list}`} />
                  <Chip size="sm" label={`Field: ${rule.identity_field} (${rule.identity_field_type})`} />
                </div>

                {rule.condition && (
                  <div className="mt-2 bg-surface-sunken rounded-md p-2 min-w-0 overflow-x-auto">
                    <span className="text-xs text-ink-3 block mb-0.5">Condition</span>
                    <code className="text-xs text-crayon-amber-text font-mono whitespace-pre">{rule.condition}</code>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {showForm && (
        <RuleFormDialog
          existing={editingRule}
          onSave={async (data) => {
            try {
              if (editingRule) {
                await updateRule({ name: editingRule.name, ...data });
                toast.success("Rule updated");
              } else {
                await createRule(data as any);
                toast.success("Rule created");
              }
              setShowForm(false);
              setEditingRule(undefined);
              loadRules();
            } catch {
              toast.error("Failed to save rule");
            }
          }}
          onClose={() => {
            setShowForm(false);
            setEditingRule(undefined);
          }}
        />
      )}

      {testingRule && (
        <TestRuleDialog
          rule={testingRule}
          onClose={() => setTestingRule(null)}
        />
      )}
    </AdminPage>
  );
}
