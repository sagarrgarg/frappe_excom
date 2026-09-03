import { useState, useEffect, useCallback, useRef } from "react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Plus, Search, Users, Mail, Phone, UserMinus, UserPlus, Trash2, Upload, Radio } from "lucide-react";
import { Button, Input, Field, Select, Modal, EmptyState, Chip, Badge } from "./primitives";
import { AdminPage, DataTable } from "./shell/AdminPage";
import { toast } from "sonner";

interface SubscriberList {
  name: string;
  list_name: string;
  description: string;
  total_subscribers: number;
  active_subscribers: number;
  creation: string;
}

interface Subscriber {
  name: string;
  omni_identity: string;
  status: string;
  subscribed_on: string;
  unsubscribed_on: string;
  display_name: string;
  primary_email: string;
  primary_phone: string;
  primary_whatsapp: string;
}

function CreateListDialog({
  onCreate,
  onClose,
}: {
  onCreate: (name: string, desc: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title="Create subscriber list" footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!name.trim()} onClick={() => onCreate(name.trim(), desc.trim())}>Create</Button></>}>
      <div className="space-y-3">
        <Field label="List name" required><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. VIP customers" autoFocus /></Field>
        <Field label="Description"><Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Optional" /></Field>
      </div>
    </Modal>
  );
}

interface IdentityOption {
  name: string;
  display_name: string;
  primary_email: string;
  primary_phone: string;
  primary_whatsapp: string;
}

function AddSubscriberDialog({
  listName,
  onAddByIdentity,
  onAddByContact,
  onClose,
}: {
  listName: string;
  onAddByIdentity: (identityName: string) => void;
  onAddByContact: (phone: string, email: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"search" | "manual">("search");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<IdentityOption[]>([]);
  const [selected, setSelected] = useState<IdentityOption | null>(null);
  const { call: searchIdentities } = useFrappePostCall("excom.excom.api.subscribers.search_identities");

  useEffect(() => {
    const timer = setTimeout(async () => {
      if (tab !== "search") return;
      try {
        const res = await searchIdentities({ search, limit: 15 });
        setResults((res as any)?.message || []);
      } catch {
        setResults([]);
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [search, tab, searchIdentities]);

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={`Add subscriber to ${listName}`}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>{tab === "search" ? <Button variant="primary" disabled={!selected} onClick={() => selected && onAddByIdentity(selected.name)}>Add</Button> : <Button variant="primary" disabled={!phone.trim() && !email.trim()} onClick={() => onAddByContact(phone.trim(), email.trim())}>Add</Button>}</>}>
      <div className="inline-flex rounded-md bg-surface-sunken p-0.5 mb-3" role="tablist">
        {(["search", "manual"] as const).map((t) => <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`h-7 px-3 rounded text-xs font-medium ${tab === t ? "bg-surface text-ink-1 shadow-ex" : "text-ink-3"}`}>{t === "search" ? "Search identity" : "By phone / email"}</button>)}
      </div>
      {tab === "search" ? (
        <div>
          <div className="relative mb-2"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={search} onChange={(e) => { setSearch(e.target.value); setSelected(null); }} placeholder="Search by name, email, phone" className="pl-8" autoFocus /></div>
          <div className="max-h-52 overflow-y-auto rounded-md border border-border divide-y divide-border">
            {results.length === 0 ? <p className="text-xs text-ink-3 text-center py-4">{search ? "No identities found" : "Type to search contacts"}</p> : results.map((r) => (
              <button key={r.name} type="button" onClick={() => setSelected(r)} className={`w-full text-left px-3 py-2 min-w-0 ${selected?.name === r.name ? "bg-crayon-blue-tint" : "hover:bg-surface-hover"}`}>
                <div className="text-sm text-ink-1 font-medium truncate">{r.display_name || r.name}</div>
                <div className="flex gap-3 mt-0.5 text-xs text-ink-3 min-w-0">{r.primary_email && <span className="inline-flex items-center gap-1 truncate"><Mail className="size-3" />{r.primary_email}</span>}{r.primary_phone && <span className="inline-flex items-center gap-1 shrink-0"><Phone className="size-3" />{r.primary_phone}</span>}</div>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <Field label="Phone"><Input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" /></Field>
          <Field label="Email"><Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" /></Field>
        </div>
      )}
    </Modal>
  );
}

function ImportDialog({
  listName,
  onImport,
  onClose,
}: {
  listName: string;
  onImport: (doctype: string, filters: object, limit: number) => void;
  onClose: () => void;
}) {
  const [doctype, setDoctype] = useState("Customer");
  const [limit, setLimit] = useState(0);

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title="Import subscribers" description={`Bulk-add from ERPNext into ${listName}. Omni Identities are auto-created.`}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={() => onImport(doctype, {}, limit)}><Upload />Import</Button></>}>
      <p className="text-xs text-ink-3 mb-3">Bulk-add from ERPNext into <span className="text-ink-1">{listName}</span>. Omni Identities are auto-created.</p>
      <div className="space-y-3">
        <Field label="Source DocType"><Select value={doctype} onChange={(e) => setDoctype(e.target.value)}><option value="Customer">Customer</option><option value="Supplier">Supplier</option><option value="Lead">Lead</option></Select></Field>
        <Field label="Limit" hint="0 = all"><Input type="number" value={limit} onChange={(e) => setLimit(parseInt(e.target.value) || 0)} /></Field>
      </div>
    </Modal>
  );
}

function SubscriberDetailView({
  list,
  subscribers,
  stats,
  searchQuery,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  onBack,
  onRefresh,
  onBroadcast,
  embedded,
}: {
  list: SubscriberList;
  embedded?: boolean;
  subscribers: Subscriber[];
  stats: { total: number; active: number; unsubscribed: number };
  searchQuery: string;
  onSearchChange: (q: string) => void;
  statusFilter: string;
  onStatusFilterChange: (s: string) => void;
  onBack: () => void;
  onRefresh: () => void;
  onBroadcast?: (listName: string) => void;
}) {
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);

  const { call: addByIdentity } = useFrappePostCall(
    "excom.excom.api.subscribers.add_subscriber"
  );
  const { call: addByContact } = useFrappePostCall(
    "excom.excom.api.subscribers.add_subscriber_by_contact"
  );
  const { call: removeSubscriber } = useFrappePostCall(
    "excom.excom.api.subscribers.remove_subscriber"
  );
  const { call: unsubscribeCall } = useFrappePostCall(
    "excom.excom.api.subscribers.unsubscribe_subscriber"
  );
  const { call: resubscribeCall } = useFrappePostCall(
    "excom.excom.api.subscribers.resubscribe"
  );
  const { call: importFromDoctype } = useFrappePostCall(
    "excom.excom.api.subscribers.import_from_doctype"
  );

  return (
    <AdminPage title={list.list_name} icon={<Users />} onBack={onBack} embedded={embedded} bleed
      actions={<><Button size="sm" onClick={() => setShowImportDialog(true)}><Upload /><span className="hidden tablet:inline">Import</span></Button><Button size="sm" variant="primary" onClick={() => setShowAddDialog(true)}><Plus />Add</Button>{onBroadcast && <Button size="sm" onClick={() => onBroadcast(list.name)}><Radio /><span className="hidden tablet:inline">Broadcast</span></Button>}</>}
      toolbar={
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative flex-1 min-w-0"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={searchQuery} onChange={(e) => onSearchChange(e.target.value)} placeholder="Search by name, email or phone" className="pl-8 bg-surface" /></div>
          <Select value={statusFilter} onChange={(e) => onStatusFilterChange(e.target.value)} className="w-[130px] shrink-0" aria-label="Status"><option value="">All status</option><option value="Subscribed">Subscribed</option><option value="Unsubscribed">Unsubscribed</option></Select>
        </div>
      }
    >
      <div className="px-3 pt-2 flex items-center gap-4 text-xs text-ink-3 tabular-nums">
        {list.description && <span className="truncate">{list.description}</span>}
        <span className="ml-auto shrink-0"><b className="text-ink-1">{stats.total}</b> total</span><span className="shrink-0"><b className="text-crayon-green-text">{stats.active}</b> active</span><span className="shrink-0"><b className="text-crayon-rose-text">{stats.unsubscribed}</b> unsubscribed</span>
      </div>
      <div className="mt-2">
        <DataTable
          rows={subscribers}
          keyOf={(s) => s.name}
          empty={<EmptyState icon={<UserPlus />} title="No subscribers yet" hint="Add subscribers manually or import from ERPNext." compact />}
          columns={[
            { key: "name", label: "Name", primary: true, render: (s) => <span className="text-ink-1">{s.display_name || "Unknown"}</span> },
            { key: "email", label: "Email", render: (s) => s.primary_email ? <span className="inline-flex items-center gap-1.5 text-ink-3 min-w-0"><Mail className="size-3.5 shrink-0" /><span className="truncate">{s.primary_email}</span></span> : <span className="text-ink-muted">—</span> },
            { key: "phone", label: "Phone", render: (s) => s.primary_phone ? <span className="inline-flex items-center gap-1.5 text-ink-3"><Phone className="size-3.5" />{s.primary_phone}</span> : <span className="text-ink-muted">—</span> },
            { key: "status", label: "Status", render: (s) => <Chip size="sm" accent={s.status === "Subscribed" ? "green" : "rose"} label={s.status} /> },
            { key: "since", label: "Since", render: (s) => <span className="text-xs text-ink-3">{s.subscribed_on ? new Date(s.subscribed_on).toLocaleDateString() : "—"}</span> },
            { key: "actions", label: "", align: "right", render: (s) => (
              <span className="inline-flex items-center gap-0.5">
                {s.status === "Subscribed"
                  ? <Button variant="ghost" size="icon-sm" title="Unsubscribe" aria-label="Unsubscribe" onClick={async () => { await unsubscribeCall({ subscriber_name: s.name }); toast.success("Unsubscribed"); onRefresh(); }}><UserMinus /></Button>
                  : <Button variant="ghost" size="icon-sm" title="Re-subscribe" aria-label="Re-subscribe" onClick={async () => { await resubscribeCall({ subscriber_name: s.name }); toast.success("Re-subscribed"); onRefresh(); }}><UserPlus /></Button>}
                <Button variant="ghost" size="icon-sm" title="Remove" aria-label="Remove" onClick={async () => { await removeSubscriber({ subscriber_name: s.name }); toast.success("Removed"); onRefresh(); }}><Trash2 /></Button>
              </span>
            ) },
          ]}
        />
      </div>

      {showAddDialog && (
        <AddSubscriberDialog
          listName={list.list_name}
          onAddByIdentity={async (identityName) => {
            try {
              const res = await addByIdentity({ subscriber_list: list.name, omni_identity: identityName });
              const data = (res as any)?.message || {};
              if (data.success === false) {
                toast.info(data.message || "Already subscribed");
              } else {
                toast.success("Subscriber added");
              }
              setShowAddDialog(false);
              onRefresh();
            } catch {
              toast.error("Failed to add subscriber");
            }
          }}
          onAddByContact={async (phone, email) => {
            try {
              await addByContact({ subscriber_list: list.name, phone, email });
              toast.success("Subscriber added");
              setShowAddDialog(false);
              onRefresh();
            } catch {
              toast.error("Failed to add subscriber");
            }
          }}
          onClose={() => setShowAddDialog(false)}
        />
      )}

      {showImportDialog && (
        <ImportDialog
          listName={list.name}
          onImport={async (doctype, filters, limit) => {
            try {
              const res = await importFromDoctype({
                subscriber_list: list.name,
                doctype,
                filters: JSON.stringify(filters),
                limit,
              });
              const data = (res as any)?.message || {};
              toast.success(`Imported: ${data.added} added, ${data.skipped} skipped`);
              setShowImportDialog(false);
              onRefresh();
            } catch {
              toast.error("Import failed");
            }
          }}
          onClose={() => setShowImportDialog(false)}
        />
      )}
    </AdminPage>
  );
}

export function SubscriberListPage({
  onNavigateBack,
  onNavigateToBroadcasts,
  embedded,
}: {
  onNavigateBack: () => void;
  onNavigateToBroadcasts?: () => void;
  embedded?: boolean;
}) {
  const [viewMode, setViewMode] = useState<"lists" | "detail">("lists");
  const [selectedList, setSelectedList] = useState<SubscriberList | null>(null);
  const [lists, setLists] = useState<SubscriberList[]>([]);
  const [subscribers, setSubscribers] = useState<Subscriber[]>([]);
  const [subscriberStats, setSubscriberStats] = useState({
    total: 0,
    active: 0,
    unsubscribed: 0,
  });
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  const { call: fetchLists } = useFrappePostCall(
    "excom.excom.api.subscribers.get_subscriber_lists"
  );
  const { call: fetchSubscribers } = useFrappePostCall(
    "excom.excom.api.subscribers.get_subscribers"
  );
  const { call: createList } = useFrappePostCall(
    "excom.excom.api.subscribers.create_subscriber_list"
  );

  const loadLists = useCallback(async () => {
    try {
      const res = await fetchLists({ search: searchQuery });
      setLists((res as any)?.message || []);
    } catch {
      toast.error("Failed to load subscriber lists");
    }
  }, [fetchLists, searchQuery]);

  const loadSubscribers = useCallback(async () => {
    if (!selectedList) return;
    try {
      const res = await fetchSubscribers({
        subscriber_list: selectedList.name,
        search: searchQuery,
        status: statusFilter,
      });
      const data = (res as any)?.message || {};
      setSubscribers(data.subscribers || []);
      setSubscriberStats({
        total: data.total || 0,
        active: data.active || 0,
        unsubscribed: data.unsubscribed || 0,
      });
    } catch {
      toast.error("Failed to load subscribers");
    }
  }, [fetchSubscribers, selectedList, searchQuery, statusFilter]);

  useEffect(() => {
    if (viewMode === "lists") loadLists();
  }, [viewMode, loadLists]);

  useEffect(() => {
    if (viewMode === "detail") loadSubscribers();
  }, [viewMode, loadSubscribers]);

  const handleSelectList = (list: SubscriberList) => {
    setSelectedList(list);
    setSearchQuery("");
    setStatusFilter("");
    setViewMode("detail");
  };

  const handleBackToLists = () => {
    setViewMode("lists");
    setSelectedList(null);
    setSearchQuery("");
  };

  if (viewMode === "detail" && selectedList) {
    return (
      <SubscriberDetailView
        list={selectedList}
        subscribers={subscribers}
        stats={subscriberStats}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        onBack={handleBackToLists}
        onRefresh={loadSubscribers}
        onBroadcast={onNavigateToBroadcasts ? () => onNavigateToBroadcasts() : undefined}
        embedded={embedded}
      />
    );
  }

  return (
    <AdminPage title="Subscriber lists" icon={<Users />} onBack={onNavigateBack} embedded={embedded}
      actions={<Button variant="primary" size="sm" onClick={() => setShowCreateDialog(true)}><Plus />New list</Button>}
      toolbar={<div className="relative"><Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-ink-muted" /><Input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Search lists" className="pl-8 bg-surface" /></div>}>
      {lists.length === 0 ? (
        <EmptyState icon={<Users />} title="No subscriber lists yet" hint="Create a list to group contacts for broadcasts." />
      ) : (
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr))]">
          {lists.map((list) => (
            <button key={list.name} type="button" onClick={() => handleSelectList(list)} className="rounded-lg border border-border p-3 text-left hover:bg-surface-hover min-w-0">
              <div className="flex items-center gap-2 min-w-0"><h3 className="text-sm font-medium text-ink-1 truncate flex-1">{list.list_name}</h3><Badge accent="green" count={list.active_subscribers} /></div>
              {list.description && <p className="text-xs text-ink-3 line-clamp-2 mt-1">{list.description}</p>}
              <p className="text-xs text-ink-3 mt-1.5 tabular-nums">{list.total_subscribers} total</p>
            </button>
          ))}
        </div>
      )}

      {showCreateDialog && (
        <CreateListDialog
          onCreate={async (name, desc) => {
            try {
              await createList({ list_name: name, description: desc });
              toast.success("List created");
              setShowCreateDialog(false);
              loadLists();
            } catch {
              toast.error("Failed to create list");
            }
          }}
          onClose={() => setShowCreateDialog(false)}
        />
      )}
    </AdminPage>
  );
}
