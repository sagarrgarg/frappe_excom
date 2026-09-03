import { useState, useEffect, useCallback, useRef } from "react";
import {
  X,
  Search,
  FileText,
  Send,
  Loader2,
  Clock,
  AlertTriangle,
  ChevronRight,
  Image as ImageIcon,
  Paperclip,
  Upload,
  Trash2,
  Video,
  MapPin,
} from "lucide-react";
import { useFrappePostCall, useFrappeFileUpload } from "frappe-react-sdk";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { toast } from "sonner";

interface WhatsAppTemplatePickerProps {
  threadId: string;
  onClose: () => void;
  onSent: () => void;
}

interface TemplateButton {
  button_type: string;
  button_label: string;
  website_url?: string;
  url_type?: string;
  example_url?: string;
}

interface TemplateItem {
  name: string;
  template_name: string;
  actual_name: string;
  template: string;
  language_code: string;
  category: string;
  header_type: string;
  header: string;
  footer: string;
  sample_values: string;
  field_names: string;
  /** Body ``{{n}}`` count (from template text). */
  variable_count: number;
  /** TEXT header ``{{n}}`` count; inbox sends header values first in ``variables`` JSON. */
  header_variable_count?: number;
  /** Example strings for body placeholders. */
  sample_variables: string[];
  /** Example strings for TEXT header placeholders. */
  header_sample_variables?: string[];
  buttons?: TemplateButton[];
  has_dynamic_url?: boolean;
}

interface WindowInfo {
  window_open: boolean;
  last_inbound_at: string | null;
  hours_remaining: number;
}

const HEADER_ACCEPT: Record<string, string> = {
  IMAGE: "image/jpeg,image/png,image/webp",
  VIDEO: "video/mp4,video/3gpp",
  DOCUMENT: "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

export function WhatsAppTemplatePicker({
  threadId,
  onClose,
  onSent,
}: WhatsAppTemplatePickerProps) {
  const [step, setStep] = useState<"list" | "fill">("list");
  const [searchText, setSearchText] = useState("");
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateItem | null>(null);
  const [variables, setVariables] = useState<string[]>([]);
  /** Suffix for each dynamic URL button, same order as template buttons. */
  const [buttonUrls, setButtonUrls] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [windowInfo, setWindowInfo] = useState<WindowInfo | null>(null);

  const [headerMediaUrl, setHeaderMediaUrl] = useState("");
  const [headerFileName, setHeaderFileName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [headerLocation, setHeaderLocation] = useState({
    latitude: "", longitude: "", name: "", address: "",
  });
  const fileInputRef = useRef<HTMLInputElement>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  const { call: fetchTemplates } = useFrappePostCall(
    "excom.excom.api.chat.get_whatsapp_templates"
  );
  const { call: fetchThreadAccount } = useFrappePostCall(
    "excom.excom.api.chat.get_thread_channel_account"
  );
  const { call: sendTemplate } = useFrappePostCall(
    "excom.excom.api.chat.send_template_to_thread"
  );
  const { call: checkWindow } = useFrappePostCall(
    "excom.excom.api.chat.check_24h_window"
  );
  const { upload } = useFrappeFileUpload();

  const [threadWaAccount, setThreadWaAccount] = useState<string | null>(null);
  const [threadMetaLoading, setThreadMetaLoading] = useState(true);

  const loadTemplates = useCallback(
    async (search: string = "") => {
      if (!threadWaAccount) {
        setTemplates([]);
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const res = await fetchTemplates({
          search,
          whatsapp_account: threadWaAccount,
        });
        setTemplates((res as any)?.message || []);
      } catch {
        toast.error("Failed to load templates");
      } finally {
        setLoading(false);
      }
    },
    [fetchTemplates, threadWaAccount]
  );

  useEffect(() => {
    let cancelled = false;
    setThreadMetaLoading(true);
    fetchThreadAccount({ thread_id: threadId })
      .then((res) => {
        if (cancelled) return;
        const msg = (res as any)?.message;
        if (msg?.channel === "whatsapp" && msg?.account) {
          setThreadWaAccount(msg.account);
        } else {
          setThreadWaAccount(null);
        }
      })
      .catch(() => {
        if (!cancelled) setThreadWaAccount(null);
      })
      .finally(() => {
        if (!cancelled) setThreadMetaLoading(false);
      });
    checkWindow({ thread_id: threadId })
      .then((res) => {
        setWindowInfo((res as any)?.message || null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [threadId, fetchThreadAccount]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => loadTemplates(searchText), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchText, loadTemplates]);

  const handleSelectTemplate = (t: TemplateItem) => {
    setSelectedTemplate(t);
    const h = t.header_variable_count ?? 0;
    const b = t.variable_count ?? 0;
    setVariables(new Array(h + b).fill(""));
    const dynCount = (t.buttons || []).filter(
      (x) => x.button_type === "Visit Website" && x.url_type === "Dynamic"
    ).length;
    setButtonUrls(new Array(dynCount).fill(""));
    setHeaderMediaUrl("");
    setHeaderFileName("");
    setHeaderLocation({ latitude: "", longitude: "", name: "", address: "" });
    setStep("fill");
  };

  const handleFileUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setUploading(true);
      try {
        const result = await upload(file, { isPrivate: false });
        setHeaderMediaUrl(result.file_url);
        setHeaderFileName(file.name);
        toast.success("File uploaded");
      } catch {
        toast.error("Upload failed");
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [upload]
  );

  const replacePlaceholders = (text: string, vals: string[]): string => {
    const placeholders: string[] = [];
    const re = /\{\{([^}]+)\}\}/g;
    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      const name = match[1].trim();
      if (!placeholders.includes(name)) placeholders.push(name);
    }
    placeholders.forEach((name, i) => {
      if (i < vals.length) {
        text = text.replaceAll(`{{${name}}}`, vals[i] || `{{${name}}}`);
      }
    });
    return text;
  };

  const getHeaderPreview = (): string => {
    if (!selectedTemplate) return "";
    const ht = (selectedTemplate.header_type || "").toUpperCase();
    if (ht !== "TEXT" || !selectedTemplate.header) return "";
    const h = selectedTemplate.header_variable_count ?? 0;
    return replacePlaceholders(selectedTemplate.header, variables.slice(0, h));
  };

  const getPreview = (): string => {
    if (!selectedTemplate) return "";
    const h = selectedTemplate.header_variable_count ?? 0;
    return replacePlaceholders(selectedTemplate.template || "", variables.slice(h));
  };

  const htUpper = (selectedTemplate?.header_type || "").toUpperCase();
  const needsMedia = htUpper === "IMAGE" || htUpper === "VIDEO" || htUpper === "DOCUMENT";
  const needsLocation = htUpper === "LOCATION";

  const hVar = selectedTemplate?.header_variable_count ?? 0;
  const bVar = selectedTemplate?.variable_count ?? 0;
  const varSlotsFilled =
    hVar + bVar === 0 ||
    variables.slice(0, hVar + bVar).every((v) => String(v ?? "").trim() !== "");
  const dynBtns =
    selectedTemplate?.buttons?.filter(
      (x) => x.button_type === "Visit Website" && x.url_type === "Dynamic"
    ) ?? [];
  const dynFilled =
    dynBtns.length === 0 || buttonUrls.slice(0, dynBtns.length).every((u) => String(u ?? "").trim() !== "");

  const locationFilled =
    !needsLocation ||
    (headerLocation.latitude.trim() !== "" && headerLocation.longitude.trim() !== "");

  const canSend =
    !sending &&
    selectedTemplate &&
    (!needsMedia || headerMediaUrl) &&
    locationFilled &&
    varSlotsFilled &&
    dynFilled;

  const handleSend = async () => {
    if (!selectedTemplate) return;
    if (needsMedia && !headerMediaUrl) {
      const mediaLabel = htUpper === "IMAGE" ? "photo" : htUpper === "VIDEO" ? "video" : "document";
      toast.error(`Please attach a ${mediaLabel} for this template`);
      return;
    }
    setSending(true);
    try {
      const dyn = (selectedTemplate.buttons || []).filter(
        (x) => x.button_type === "Visit Website" && x.url_type === "Dynamic"
      );
      await sendTemplate({
        thread_id: threadId,
        template_name: selectedTemplate.name,
        variables: JSON.stringify(variables),
        header_media_url: headerMediaUrl,
        button_urls: JSON.stringify(buttonUrls.slice(0, dyn.length)),
        header_location: needsLocation ? JSON.stringify(headerLocation) : "",
      });
      toast.success("Template sent");
      onSent();
      onClose();
    } catch (err: any) {
      let msg = "Failed to send template";
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
      setSending(false);
    }
  };

  const labels = selectedTemplate?.field_names
    ? selectedTemplate.field_names.split(",").map((s: string) => s.trim())
    : [];

  return (
    <div
      className="fixed inset-0 bg-ink-1/40 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border-strong rounded-xl w-full max-w-lg shadow-ex max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <FileText className="w-5 h-5 text-crayon-green-text" />
            <h2 className="text-lg font-semibold text-ink-1">
              {step === "list" ? "WhatsApp Templates" : "Fill Variables"}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-surface-sunken text-ink-3 hover:text-ink-1 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {windowInfo && (
          <div
            className={`shrink-0 px-5 py-2.5 text-xs flex items-center gap-2 border-b border-border ${
              windowInfo.window_open
                ? "bg-crayon-green-tint text-crayon-green-text"
                : "bg-crayon-amber-tint text-crayon-amber-text"
            }`}
          >
            {windowInfo.window_open ? (
              <>
                <Clock className="w-3.5 h-3.5 shrink-0" />
                <span>
                  24h window open &mdash; {windowInfo.hours_remaining}h remaining.
                  You can send free-form messages or templates.
                </span>
              </>
            ) : (
              <>
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                <span>24h window closed. Only approved templates can be sent.</span>
              </>
            )}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept={selectedTemplate ? HEADER_ACCEPT[selectedTemplate.header_type] || "" : ""}
          onChange={handleFileUpload}
          className="hidden"
        />

        <div className="flex-1 min-h-0 overflow-y-auto">
          {step === "list" && (
            <div className="p-4 space-y-3">
              {threadMetaLoading ? (
                <div className="flex items-center justify-center py-10">
                  <Loader2 className="w-6 h-6 text-crayon-green-text animate-spin" />
                </div>
              ) : !threadWaAccount ? (
                <div className="text-center py-10 text-sm text-crayon-amber-text/90 px-2">
                  This thread is not tied to a WhatsApp channel account, so templates cannot be
                  filtered. Open a WhatsApp conversation or check Excom Thread account linkage.
                </div>
              ) : (
                <>
                  <p className="text-xs text-ink-3">
                    Showing templates linked to this conversation&apos;s phone number (WABA).
                  </p>
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-ink-3" />
                    <Input
                      placeholder="Search templates..."
                      value={searchText}
                      onChange={(e) => setSearchText(e.target.value)}
                      className="pl-10 bg-surface-sunken border-border-strong text-ink-1"
                      autoFocus
                    />
                  </div>

                  {loading && (
                    <div className="flex items-center justify-center py-10">
                      <Loader2 className="w-6 h-6 text-crayon-green-text animate-spin" />
                    </div>
                  )}

                  {!loading && templates.length === 0 && (
                    <div className="text-center py-10 text-sm text-ink-3">
                      No approved templates for this WhatsApp number
                    </div>
                  )}

                  <div className="space-y-2">
                {templates.map((t) => (
                  <button
                    key={t.name}
                    onClick={() => handleSelectTemplate(t)}
                    className="w-full text-left rounded-lg border border-border p-3.5 hover:border-crayon-green-base/40 hover:bg-crayon-green-tint transition-all group"
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-sm font-medium text-ink-1 group-hover:text-crayon-green-text transition-colors">
                        {t.template_name}
                      </span>
                      <ChevronRight className="w-4 h-4 text-ink-3 group-hover:text-crayon-green-text transition-colors" />
                    </div>
                    <p className="text-xs text-ink-3 line-clamp-2 mb-2">
                      {t.template || "No preview available"}
                    </p>
                    <div className="flex items-center gap-3 text-xs text-ink-3">
                      <span className="bg-surface-sunken px-1.5 py-0.5 rounded">{t.category}</span>
                      <span>{t.language_code}</span>
                      {((t.header_variable_count ?? 0) + (t.variable_count ?? 0)) > 0 && (
                        <span className="text-crayon-blue-text">
                          {(t.header_variable_count ?? 0) + (t.variable_count ?? 0)} variable
                          {(t.header_variable_count ?? 0) + (t.variable_count ?? 0) > 1 ? "s" : ""}
                        </span>
                      )}
                      {(t.header_type || "").toUpperCase() === "IMAGE" && (
                        <span className="text-crayon-violet-text flex items-center gap-0.5">
                          <ImageIcon className="w-3 h-3" /> Photo required
                        </span>
                      )}
                      {(t.header_type || "").toUpperCase() === "VIDEO" && (
                        <span className="text-crayon-blue-text flex items-center gap-0.5">
                          <Video className="w-3 h-3" /> Video required
                        </span>
                      )}
                      {(t.header_type || "").toUpperCase() === "DOCUMENT" && (
                        <span className="text-crayon-amber-text flex items-center gap-0.5">
                          <Paperclip className="w-3 h-3" /> PDF/Doc required
                        </span>
                      )}
                      {(t.header_type || "").toUpperCase() === "LOCATION" && (
                        <span className="text-crayon-green-text flex items-center gap-0.5">
                          <MapPin className="w-3 h-3" /> Location
                        </span>
                      )}
                    </div>
                  </button>
                ))}
                  </div>
                </>
              )}
            </div>
          )}

          {step === "fill" && selectedTemplate && (
            <div className="p-4 space-y-3">
              <button
                onClick={() => setStep("list")}
                className="text-xs text-ink-3 hover:text-ink-1 transition-colors flex items-center gap-1"
              >
                <ChevronRight className="w-3 h-3 rotate-180" />
                Back to templates
              </button>

              <div className="bg-surface-sunken border border-border-strong rounded-lg p-3">
                <p className="text-xs text-ink-3 mb-1 font-medium">
                  {selectedTemplate.template_name}
                </p>
                {getHeaderPreview() && (
                  <p className="text-sm text-ink-1 font-semibold mb-1">
                    {getHeaderPreview()}
                  </p>
                )}
                <p className="text-sm text-ink-2 leading-relaxed whitespace-pre-wrap">
                  {getPreview()}
                </p>
                {selectedTemplate.footer && (
                  <p className="text-xs text-ink-3 mt-2 italic">
                    {selectedTemplate.footer}
                  </p>
                )}
              </div>

              {needsMedia && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    {htUpper === "IMAGE" ? (
                      <ImageIcon className="w-4 h-4 text-crayon-violet-text" />
                    ) : htUpper === "VIDEO" ? (
                      <Video className="w-4 h-4 text-crayon-blue-text" />
                    ) : (
                      <Paperclip className="w-4 h-4 text-crayon-amber-text" />
                    )}
                    <p className="text-xs text-ink-3 font-medium">
                      {htUpper === "IMAGE"
                        ? "Attach Header Image"
                        : htUpper === "VIDEO"
                        ? "Attach Header Video"
                        : "Attach Header Document (PDF)"}
                      <span className="text-crayon-rose-text ml-1">*</span>
                    </p>
                  </div>

                  {headerMediaUrl ? (
                    <div className="flex items-center gap-3 bg-surface-sunken border border-border-strong rounded-lg p-3">
                      {htUpper === "IMAGE" ? (
                        <img
                          src={headerMediaUrl}
                          alt="Header"
                          className="w-14 h-14 rounded-lg object-cover border border-border-strong"
                        />
                      ) : htUpper === "VIDEO" ? (
                        <div className="w-14 h-14 rounded-lg bg-crayon-blue-tint border border-crayon-blue-base/40 flex items-center justify-center">
                          <Video className="w-6 h-6 text-crayon-blue-text" />
                        </div>
                      ) : (
                        <div className="w-14 h-14 rounded-lg bg-crayon-amber-tint border border-crayon-amber-base/40 flex items-center justify-center">
                          <FileText className="w-6 h-6 text-crayon-amber-text" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-ink-1 font-medium truncate">
                          {headerFileName}
                        </p>
                        <p className="text-xs text-crayon-green-text">Uploaded</p>
                      </div>
                      <button
                        onClick={() => {
                          setHeaderMediaUrl("");
                          setHeaderFileName("");
                        }}
                        className="p-1.5 rounded-lg hover:bg-surface-active text-ink-3 hover:text-crayon-rose-text transition-colors shrink-0"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploading}
                      className="w-full border-2 border-dashed border-border-strong hover:border-border-strong rounded-lg p-3 flex flex-col items-center gap-2 transition-colors group"
                    >
                      {uploading ? (
                        <Loader2 className="w-6 h-6 text-crayon-blue-text animate-spin" />
                      ) : (
                        <Upload className="w-6 h-6 text-ink-3 group-hover:text-ink-2 transition-colors" />
                      )}
                      <span className="text-xs text-ink-3 group-hover:text-ink-2 transition-colors">
                        {uploading
                          ? "Uploading..."
                          : htUpper === "IMAGE"
                          ? "Click to upload image (JPEG, PNG, WebP)"
                          : htUpper === "VIDEO"
                          ? "Click to upload video (MP4, max 16MB)"
                          : "Click to upload document (PDF, DOC, XLS)"}
                      </span>
                    </button>
                  )}
                </div>
              )}

              {needsLocation && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <MapPin className="w-4 h-4 text-crayon-green-text" />
                    <p className="text-xs text-ink-3 font-medium">
                      Location Header<span className="text-crayon-rose-text ml-1">*</span>
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="text-xs text-ink-3 mb-0.5 block">Latitude</label>
                      <Input
                        placeholder="e.g. 37.4421"
                        value={headerLocation.latitude}
                        onChange={(e) => setHeaderLocation((p) => ({ ...p, latitude: e.target.value }))}
                        className="bg-surface-sunken border-border-strong text-ink-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-ink-3 mb-0.5 block">Longitude</label>
                      <Input
                        placeholder="e.g. -122.1615"
                        value={headerLocation.longitude}
                        onChange={(e) => setHeaderLocation((p) => ({ ...p, longitude: e.target.value }))}
                        className="bg-surface-sunken border-border-strong text-ink-1"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-ink-3 mb-0.5 block">Location Name</label>
                    <Input
                      placeholder="e.g. Philz Coffee"
                      value={headerLocation.name}
                      onChange={(e) => setHeaderLocation((p) => ({ ...p, name: e.target.value }))}
                      className="bg-surface-sunken border-border-strong text-ink-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-ink-3 mb-0.5 block">Address</label>
                    <Input
                      placeholder="e.g. 101 Forest Ave, Palo Alto, CA"
                      value={headerLocation.address}
                      onChange={(e) => setHeaderLocation((p) => ({ ...p, address: e.target.value }))}
                      className="bg-surface-sunken border-border-strong text-ink-1"
                    />
                  </div>
                </div>
              )}

              {hVar + bVar > 0 && (
                <div className="space-y-3">
                  <p className="text-xs text-ink-3 font-medium">Template variables</p>
                  {hVar > 0 && (
                    <p className="text-xs text-ink-3">Header (filled first for Meta)</p>
                  )}
                  {variables.slice(0, hVar + bVar).map((val, idx) => {
                    const isHeader = idx < hVar;
                    const bodyIdx = idx - hVar;
                    const hdrSamples = selectedTemplate.header_sample_variables || [];
                    const sampleHint = isHeader
                      ? hdrSamples[idx] || ""
                      : selectedTemplate.sample_variables[bodyIdx] || "";
                    const labelHint = isHeader
                      ? `Header {{${idx + 1}}}`
                      : labels[bodyIdx] || `{{${bodyIdx + 1}}}`;
                    return (
                      <div key={idx}>
                        <label className="text-xs text-ink-3 mb-1 block">
                          {isHeader ? `Header · ${labelHint}` : `Body · ${labelHint}`}
                        </label>
                        <Input
                          placeholder={sampleHint || (isHeader ? "Header value" : `Value for {{${bodyIdx + 1}}}`)}
                          value={val}
                          onChange={(e) => {
                            const next = [...variables];
                            next[idx] = e.target.value;
                            setVariables(next);
                          }}
                          className="bg-surface-sunken border-border-strong text-ink-1"
                          autoFocus={idx === 0 && !needsMedia}
                        />
                      </div>
                    );
                  })}
                </div>
              )}

              {dynBtns.length > 0 && (
                <div className="space-y-3">
                  <p className="text-xs text-ink-3 font-medium">Dynamic link buttons</p>
                  {dynBtns.map((btn, idx) => (
                    <div key={idx}>
                      <label className="text-xs text-ink-3 mb-1 block">
                        {btn.button_label} — <span className="text-ink-3">{btn.website_url}</span>
                      </label>
                      <Input
                        placeholder={btn.example_url || "URL suffix"}
                        value={buttonUrls[idx] || ""}
                        onChange={(e) => {
                          const next = [...buttonUrls];
                          next[idx] = e.target.value;
                          setButtonUrls(next);
                        }}
                        className="bg-surface-sunken border-border-strong text-ink-1"
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {step === "fill" && (
          <div className="flex justify-end gap-2 p-4 border-t border-border shrink-0">
            <Button
              variant="outline"
              onClick={() => setStep("list")}
              className="border-border-strong text-ink-2"
            >
              Back
            </Button>
            <Button
              disabled={!canSend}
              onClick={handleSend}
              className="bg-crayon-green-base hover:bg-crayon-green-text text-white min-w-28"
            >
              {sending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  <Send className="w-4 h-4 mr-1.5" />
                  Send Template
                </>
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
