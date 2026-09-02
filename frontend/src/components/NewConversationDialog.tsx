import { useState, useEffect, useCallback, useRef } from "react";
import {
  X,
  Search,
  MessageCircle,
  Mail,
  Phone,
  User,
  Loader2,
  Plus,
  ChevronDown,
} from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { toast } from "sonner";

interface NewConversationDialogProps {
  onClose: () => void;
  onConversationCreated: (threadId: string, identityName: string) => void;
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
}: NewConversationDialogProps) {
  const [tab, setTab] = useState<TabMode>("search");
  const [channel, setChannel] = useState<"whatsapp" | "email" | "voice">("whatsapp");

  const [searchText, setSearchText] = useState("");
  const [identities, setIdentities] = useState<IdentityResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [selectedIdentity, setSelectedIdentity] = useState<IdentityResult | null>(null);

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
        if (channel === "whatsapp" || channel === "voice") {
          if (!newPhone.trim()) {
            toast.error("Phone number is required");
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
        if (channel === "voice") {
          const targetPhone = selectedIdentity?.primary_phone || newPhone.trim();
          if (targetPhone) {
            try {
              await (window as any).frappe?.call?.({
                method: "excom.excom.api.voice.initiate_call",
                args: { to_number: targetPhone, account_name: selectedAccount, thread_id: data.thread_id }
              });
              toast.success("Call initiated to " + targetPhone);
            } catch (err: any) {
              console.error("Outbound call error:", err);
            }
          }
        }
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
        (((channel === "whatsapp" || channel === "voice") && newPhone.trim()) ||
          (channel === "email" && newEmail.trim()))));

  const getAccountLabel = (acc: ChannelAccount): string => {
    const identifier =
      acc.channel === "email" ? acc.email_address : acc.wa_phone_id;
    return identifier
      ? `${acc.account_name} (${identifier})`
      : acc.account_name;
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-zinc-50 border border-zinc-300 rounded-xl w-full max-w-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-zinc-200">
          <div className="flex items-center gap-2">
            <Plus className="w-5 h-5 text-blue-700" />
            <h2 className="text-lg font-semibold text-zinc-900">New Conversation</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-600 hover:text-zinc-900 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-5">
          {/* Channel Selection */}
          <div>
            <label className="text-xs text-zinc-600 mb-2 block font-medium">Channel</label>
            <div className="flex gap-2">
              <button
                onClick={() => setChannel("whatsapp")}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all border ${
                  channel === "whatsapp"
                    ? "bg-green-500/15 text-green-700 border-green-500/40"
                    : "bg-zinc-100 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                }`}
              >
                <MessageCircle className="w-4 h-4" />
                WhatsApp
              </button>
              <button
                onClick={() => setChannel("email")}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all border ${
                  channel === "email"
                    ? "bg-blue-500/15 text-blue-700 border-blue-500/40"
                    : "bg-zinc-100 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                }`}
              >
                <Mail className="w-4 h-4" />
                Email
              </button>
              <button
                onClick={() => setChannel("voice")}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm font-medium transition-all border ${
                  channel === "voice"
                    ? "bg-emerald-500/15 text-emerald-700 border-emerald-500/40"
                    : "bg-zinc-100 text-zinc-600 border-zinc-300 hover:text-zinc-700"
                }`}
              >
                <Phone className="w-4 h-4" />
                Call
              </button>
            </div>
          </div>

          {/* Account Selection */}
          <div>
            <label className="text-xs text-zinc-600 mb-1.5 block font-medium">
              Send From Account
            </label>
            {loadingAccounts ? (
              <div className="flex items-center gap-2 py-2 text-xs text-zinc-600">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Loading accounts...
              </div>
            ) : accounts.length === 0 ? (
              <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-3 text-xs text-red-700">
                No {channel} accounts found. Create one in Excom Channel Account first.
              </div>
            ) : (
              <div className="relative">
                <select
                  value={selectedAccount}
                  onChange={(e) => setSelectedAccount(e.target.value)}
                  className="w-full appearance-none bg-zinc-100 border border-zinc-300 rounded-lg px-3 py-2.5 text-sm text-zinc-900 focus:outline-none focus:border-zinc-300 pr-8"
                >
                  {accounts.map((acc) => (
                    <option key={acc.name} value={acc.name}>
                      {getAccountLabel(acc)}
                      {acc.is_default_outgoing ? " (default)" : ""}
                    </option>
                  ))}
                </select>
                <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-600 pointer-events-none" />
              </div>
            )}
          </div>

          {/* Tabs */}
          <div className="flex gap-1 bg-zinc-100/50 rounded-lg p-1">
            <button
              onClick={() => { setTab("search"); setSelectedIdentity(null); }}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-all ${
                tab === "search"
                  ? "bg-zinc-200 text-zinc-900"
                  : "text-zinc-600 hover:text-zinc-700"
              }`}
            >
              <Search className="w-3 h-3 inline mr-1.5" />
              Existing Contact
            </button>
            <button
              onClick={() => { setTab("create"); setSelectedIdentity(null); }}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-all ${
                tab === "create"
                  ? "bg-zinc-200 text-zinc-900"
                  : "text-zinc-600 hover:text-zinc-700"
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
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-600" />
                <Input
                  placeholder="Search by name, phone, email..."
                  value={searchText}
                  onChange={(e) => setSearchText(e.target.value)}
                  className="pl-10 bg-zinc-100 border-zinc-300 text-zinc-900"
                  autoFocus
                />
              </div>

              <div className="max-h-56 overflow-y-auto space-y-1 rounded-lg border border-zinc-200">
                {searching && (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="w-5 h-5 text-blue-700 animate-spin" />
                  </div>
                )}
                {!searching && identities.length === 0 && searchText.trim() && (
                  <div className="text-center py-4 text-sm text-zinc-600">
                    No identities found
                  </div>
                )}
                {!searching && identities.length === 0 && !searchText.trim() && (
                  <div className="text-center py-4 text-sm text-zinc-600">
                    Start typing to search
                  </div>
                )}
                {identities.map((id) => (
                  <button
                    key={id.name}
                    onClick={() => setSelectedIdentity(id)}
                    className={`w-full text-left px-3 py-2.5 flex items-center gap-3 transition-colors ${
                      selectedIdentity?.name === id.name
                        ? "bg-blue-500/15 border-l-2 border-blue-500"
                        : "hover:bg-zinc-100/70 border-l-2 border-transparent"
                    }`}
                  >
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-xs font-medium shrink-0">
                      {(id.display_name || id.name)
                        .split(" ")
                        .map((w: string) => w[0])
                        .join("")
                        .slice(0, 2)
                        .toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-zinc-900 font-medium truncate">
                        {id.display_name || id.name}
                      </p>
                      <div className="flex items-center gap-3 text-[11px] text-zinc-600">
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
                <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-3 flex items-center gap-3">
                  <User className="w-4 h-4 text-blue-700 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-zinc-900 font-medium truncate">
                      {selectedIdentity.display_name || selectedIdentity.name}
                    </p>
                    <p className="text-[11px] text-zinc-600 truncate">
                      {channel === "whatsapp"
                        ? selectedIdentity.primary_whatsapp || selectedIdentity.primary_phone || "No phone"
                        : selectedIdentity.primary_email || "No email"}
                    </p>
                  </div>
                  <button
                    onClick={() => setSelectedIdentity(null)}
                    className="p-1 rounded hover:bg-zinc-200 text-zinc-600 hover:text-zinc-900"
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
                <label className="text-xs text-zinc-600 mb-1 block">Name</label>
                <Input
                  placeholder="Contact name"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="bg-zinc-100 border-zinc-300 text-zinc-900"
                  autoFocus
                />
              </div>
              {channel === "whatsapp" && (
                <div>
                  <label className="text-xs text-zinc-600 mb-1 block">
                    Phone Number <span className="text-red-700">*</span>
                  </label>
                  <Input
                    placeholder="+91 98765 43210"
                    value={newPhone}
                    onChange={(e) => setNewPhone(e.target.value)}
                    className="bg-zinc-100 border-zinc-300 text-zinc-900"
                  />
                  <p className="text-[10px] text-zinc-600 mt-1">
                    Include country code (e.g. +91)
                  </p>
                </div>
              )}
              {channel === "email" && (
                <div>
                  <label className="text-xs text-zinc-600 mb-1 block">
                    Email Address <span className="text-red-700">*</span>
                  </label>
                  <Input
                    placeholder="contact@example.com"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    className="bg-zinc-100 border-zinc-300 text-zinc-900"
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-zinc-200">
          <Button
            variant="outline"
            onClick={onClose}
            className="border-zinc-300 text-zinc-700"
          >
            Cancel
          </Button>
          <Button
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
            className="bg-gradient-to-r from-blue-500 to-purple-600 hover:from-blue-600 hover:to-purple-700 text-white min-w-28"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <MessageCircle className="w-4 h-4 mr-1.5" />
                Start Chat
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
