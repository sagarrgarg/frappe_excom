import { useState, useEffect, useCallback, useRef } from "react";
import { X, Search, MessageCircle, Mail, Phone, User, Loader2, Plus } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Button, Input, Modal, Select, Avatar } from "./primitives";
import { toast } from "sonner";

interface NewConversationDialogProps {
  onClose: () => void;
  onConversationCreated: (threadId: string, identityName: string) => void;
  /** Open with this contact already selected (record pane → Start conversation). */
  initialIdentity?: IdentityResult | null;
}

type TabMode = "search" | "create";

interface IdentityResult {
  name: string;
  display_name: string;
  primary_email: string;
  primary_phone: string;
  primary_whatsapp: string;
}

interface ChannelAccount {
  name: string;
  account_name: string;
  channel: string;
  is_default_incoming: number;
  is_default_outgoing: number;
  wa_phone_id: string;
  email_address: string;
}

export function NewConversationDialog({
  onClose,
  onConversationCreated,
  initialIdentity,
}: NewConversationDialogProps) {
  const [tab, setTab] = useState<TabMode>("search");
  const [channel, setChannel] = useState<"whatsapp" | "email">("whatsapp");

  const [searchText, setSearchText] = useState("");
  const [identities, setIdentities] = useState<IdentityResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedIdentity, setSelectedIdentity] = useState<IdentityResult | null>(initialIdentity ?? null);

  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [newEmail, setNewEmail] = useState("");

  const [accounts, setAccounts] = useState<ChannelAccount[]>([]);
  const [selectedAccount, setSelectedAccount] = useState("");
  const [loadingAccounts, setLoadingAccounts] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const { call: searchIdentities } = useFrappePostCall(
    "excom.excom.api.subscribers.search_identities"
  );
  const { call: initiateOutbound } = useFrappePostCall(
    "excom.excom.api.chat.initiate_outbound"
  );
  const { call: fetchAccounts } = useFrappePostCall(
    "excom.excom.api.chat.get_channel_accounts"
  );

  const loadAccounts = useCallback(
    async (ch: string) => {
      setLoadingAccounts(true);
      try {
        const res = await fetchAccounts({ channel: ch });
        const list: ChannelAccount[] = (res as any)?.message || [];
        setAccounts(list);
        const defaultAcc = list.find((a) => a.is_default_outgoing) || list[0];
        setSelectedAccount(defaultAcc?.name || "");
      } catch {
        setAccounts([]);
        setSelectedAccount("");
      } finally {
        setLoadingAccounts(false);
      }
    },
    [fetchAccounts]
  );

  useEffect(() => {
    loadAccounts(channel);
  }, [channel, loadAccounts]);

  const doSearch = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setIdentities([]);
        return;
      }
      setSearching(true);
      try {
        const res = await searchIdentities({ search: q, limit: 15 });
        setIdentities((res as any)?.message || []);
      } catch {
        setIdentities([]);
      } finally {
        setSearching(false);
      }
    },
    [searchIdentities]
  );

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(searchText), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchText, doSearch]);

  const handleSubmit = async () => {
    if (!selectedAccount) {
      toast.error("Please select an account to send from");
      return;
    }
    setSubmitting(true);
    try {
      const payload: Record<string, string> = { channel, account: selectedAccount };

      if (tab === "search" && selectedIdentity) {
        payload.omni_identity = selectedIdentity.name;
      } else if (tab === "create") {
        payload.display_name = newName.trim();
        if (channel === "whatsapp") {
          if (!newPhone.trim()) {
            toast.error("Phone number is required for WhatsApp");
            setSubmitting(false);
            return;
          }
          payload.phone = newPhone.trim();
        } else {
          if (!newEmail.trim()) {
            toast.error("Email is required");
            setSubmitting(false);
            return;
          }
          payload.email = newEmail.trim();
        }
      } else {
        toast.error("Select an identity or create a new one");
        setSubmitting(false);
        return;
      }

      const res = await initiateOutbound(payload);
      const data = (res as any)?.message;
      if (data?.thread_id) {
        toast.success("Conversation started");
        onConversationCreated(data.thread_id, data.omni_identity);
      } else {
        toast.error("Unexpected response");
      }
    } catch (err: any) {
      let msg = "Failed to start conversation";
      try {
        if (err?._server_messages) {
          const parsed = JSON.parse(err._server_messages);
          if (typeof parsed?.[0] === "string") {
            const inner = JSON.parse(parsed[0]);
            msg = inner?.message || parsed[0];
          }
        }
      } catch {
        // use default
      }
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit =
    selectedAccount &&
    ((tab === "search" && selectedIdentity) ||
      (tab === "create" &&
        ((channel === "whatsapp" && newPhone.trim()) ||
          (channel === "email" && newEmail.trim()))));

  const getAccountLabel = (acc: ChannelAccount): string => {
    const identifier =
      acc.channel === "email" ? acc.email_address : acc.wa_phone_id;
    return identifier
      ? `${acc.account_name} (${identifier})`
      : acc.account_name;
  };

  return (
    <Modal
      open
      onOpenChange={(v) => !v && onClose()}
      title="New conversation"
      width="max-w-lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!canSubmit || submitting} onClick={handleSubmit}>{submitting ? <Loader2 className="animate-spin" /> : <MessageCircle />}Start chat</Button>
        </>
      }
    >
        <div className="space-y-4">
          {/* Channel Selection */}
          <div>
            <label className="text-xs text-ink-3 mb-2 block font-medium">Channel</label>
            <div className="flex gap-2">
              <button
                onClick={() => setChannel("whatsapp")}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all border ${
                  channel === "whatsapp"
                    ? "bg-crayon-green-tint text-crayon-green-text border-crayon-green-base/40"
                    : "bg-surface-sunken text-ink-3 border-border-strong hover:text-ink-2"
                }`}
              >
                <MessageCircle className="w-4 h-4" />
                WhatsApp
              </button>
              <button
                onClick={() => setChannel("email")}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all border ${
                  channel === "email"
                    ? "bg-crayon-blue-tint text-crayon-blue-text border-crayon-blue-base/40"
                    : "bg-surface-sunken text-ink-3 border-border-strong hover:text-ink-2"
                }`}
              >
                <Mail className="w-4 h-4" />
                Email
              </button>
            </div>
          </div>

          {/* Account Selection */}
          <div>
            <label className="text-xs text-ink-3 mb-1.5 block font-medium">
              Send From Account
            </label>
            {loadingAccounts ? (
              <div className="flex items-center gap-2 py-2 text-xs text-ink-3">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Loading accounts...
              </div>
            ) : accounts.length === 0 ? (
              <div className="bg-crayon-rose-tint border border-crayon-rose-base/40 rounded-lg p-3 text-xs text-crayon-rose-text">
                No {channel} accounts found. Create one in Excom Channel Account first.
              </div>
            ) : (
              <Select value={selectedAccount} onChange={(e) => setSelectedAccount(e.target.value)}>
                {accounts.map((acc) => (
                  <option key={acc.name} value={acc.name}>
                    {getAccountLabel(acc)}
                    {acc.is_default_outgoing ? " (default)" : ""}
                  </option>
                ))}
              </Select>
            )}
          </div>

          {/* Tabs */}
          <div className="flex gap-1 bg-surface-sunken rounded-lg p-1">
            <button
              onClick={() => { setTab("search"); setSelectedIdentity(null); }}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-all ${
                tab === "search"
                  ? "bg-surface-active text-ink-1"
                  : "text-ink-3 hover:text-ink-2"
              }`}
            >
              <Search className="w-3 h-3 inline mr-1.5" />
              Existing Contact
            </button>
            <button
              onClick={() => { setTab("create"); setSelectedIdentity(null); }}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-all ${
                tab === "create"
                  ? "bg-surface-active text-ink-1"
                  : "text-ink-3 hover:text-ink-2"
              }`}
            >
              <Plus className="w-3 h-3 inline mr-1.5" />
              New Contact
            </button>
          </div>

          {/* Search Tab */}
          {tab === "search" && (
            <div className="space-y-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-3" />
                <Input
                  placeholder="Search by name, phone, email..."
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  className="pl-10 bg-surface-sunken border-border-strong text-ink-1"
                  autoFocus
                />
              </div>

              <div className="max-h-56 overflow-y-auto space-y-1 rounded-lg border border-border">
                {searching && (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="w-5 h-5 text-crayon-blue-text animate-spin" />
                  </div>
                )}
                {!searching && identities.length === 0 && searchText.trim() && (
                  <div className="text-center py-4 text-sm text-ink-3">
                    No identities found
                  </div>
                )}
                {!searching && identities.length === 0 && !searchText.trim() && (
                  <div className="text-center py-4 text-sm text-ink-3">
                    Start typing to search
                  </div>
                )}
                {identities.map((id) => (
                  <button
                    key={id.name}
                    onClick={() => setSelectedIdentity(id)}
                    className={`w-full text-left px-3 py-2.5 flex items-center gap-3 transition-colors ${
                      selectedIdentity?.name === id.name
                        ? "bg-crayon-blue-tint border-l-2 border-crayon-blue-base/40"
                        : "hover:bg-surface-sunken border-l-2 border-transparent"
                    }`}
                  >
                    <Avatar name={id.display_name || id.name} size={32} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-ink-1 font-medium truncate">
                        {id.display_name || id.name}
                      </p>
                      <div className="flex items-center gap-3 text-xs text-ink-3">
                        {id.primary_phone && (
                          <span className="flex items-center gap-1">
                            <Phone className="w-3 h-3" />
                            {id.primary_phone}
                          </span>
                        )}
                        {id.primary_email && (
                          <span className="flex items-center gap-1">
                            <Mail className="w-3 h-3" />
                            {id.primary_email}
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>

              {selectedIdentity && (
                <div className="bg-crayon-blue-tint border border-crayon-blue-base/40 rounded-lg p-3 flex items-center gap-3">
                  <User className="w-4 h-4 text-crayon-blue-text shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink-1 font-medium truncate">
                      {selectedIdentity.display_name || selectedIdentity.name}
                    </p>
                    <p className="text-xs text-ink-3 truncate">
                      {channel === "whatsapp"
                        ? selectedIdentity.primary_whatsapp || selectedIdentity.primary_phone || "No phone"
                        : selectedIdentity.primary_email || "No email"}
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedIdentity(null)}
                    className="p-1 rounded hover:bg-surface-active text-ink-3 hover:text-ink-1"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Create Tab */}
          {tab === "create" && (
            <div className="space-y-3">
              <div>
                <label className="text-xs text-ink-3 mb-1 block">Name</label>
                <Input
                  placeholder="Contact name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="bg-surface-sunken border-border-strong text-ink-1"
                  autoFocus
                />
              </div>
              {channel === "whatsapp" && (
                <div>
                  <label className="text-xs text-ink-3 mb-1 block">
                    Phone Number <span className="text-crayon-rose-text">*</span>
                  </label>
                  <Input
                    placeholder="+91 98765 43210"
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    className="bg-surface-sunken border-border-strong text-ink-1"
                  />
                  <p className="text-xs text-ink-3 mt-1">
                    Include country code (e.g. +91)
                  </p>
                </div>
              )}
              {channel === "email" && (
                <div>
                  <label className="text-xs text-ink-3 mb-1 block">
                    Email Address <span className="text-crayon-rose-text">*</span>
                  </label>
                  <Input
                    placeholder="contact@example.com"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    className="bg-surface-sunken border-border-strong text-ink-1"
                  />
                </div>
              )}
            </div>
          )}
        </div>

    </Modal>
  );
}
