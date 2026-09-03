import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useThreads } from "../../hooks/useContacts";
import { useRealtimeThreads } from "../../hooks/useRealtimeThreads";
import { useNotifications } from "../../hooks/useNotifications";
import { useBreakpoint, useCoarsePointer, type Breakpoint } from "../../hooks/useBreakpoint";
import {
  DEFAULT_VIEWS, EMPTY_FILTERS, filtersFromParams, loadSavedViews, paramsFromFilters, persistSavedViews,
  viewPredicate, kindMatches, type InboxFilters, type SavedView,
} from "../../lib/views";
import { applyDensity, getDensity, type Density } from "../../lib/ui-flag";
import type { UnifiedContact } from "../../types";

interface InboxCtx {
  bp: Breakpoint;
  coarse: boolean;
  density: Density;
  setDensity: (d: Density) => void;

  filters: InboxFilters;
  setFilters: (f: InboxFilters) => void;
  viewId: string;
  setView: (id: string) => void;
  views: SavedView[];
  saveView: (label: string) => void;
  deleteView: (id: string) => void;

  contacts: UnifiedContact[];
  allContacts: UnifiedContact[];
  isLoading: boolean;
  /** Last list-fetch error (e.g. rate limit) so the list can say so instead of "Nothing here". */
  listError: string | null;
  refresh: () => void;
  totalUnread: number;

  selectedId: string | null;
  selected: UnifiedContact | null;
  openRecord: (id: string, via?: string) => void;
  closeRecord: () => void;

  detailsOpen: boolean;
  setDetailsOpen: (v: boolean) => void;
  toggleDetails: () => void;

  newOpen: boolean;
  setNewOpen: (v: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (v: boolean) => void;
  pendingSelect: React.MutableRefObject<{ id: string; via: string } | null>;
}

const Ctx = createContext<InboxCtx | null>(null);

export function useInbox(): InboxCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useInbox outside InboxProvider");
  return c;
}

const DETAILS_KEY = "excom_details_open";

export function InboxProvider({ children }: { children: React.ReactNode }) {
  const bp = useBreakpoint();
  const coarse = useCoarsePointer();
  const [density, setDensityState] = useState<Density>(getDensity);
  useEffect(() => { applyDensity(density); }, [density]);
  const setDensity = useCallback((d: Density) => setDensityState(d), []);

  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const [sp, setSp] = useSearchParams();

  const isRecordRoute = location.pathname.startsWith("/t/");
  const selectedId = isRecordRoute ? params.recordId || null : null;
  const viewId = (isRecordRoute ? sp.get("view") : params.view) || "all";

  const [custom, setCustom] = useState<SavedView[]>(loadSavedViews);
  const views = useMemo(() => [...DEFAULT_VIEWS, ...custom], [custom]);
  const view = views.find((v) => v.id === viewId);

  const filters = useMemo<InboxFilters>(() => {
    const fromUrl = filtersFromParams(sp);
    const base = { ...EMPTY_FILTERS, ...(view?.filters || {}) };
    // URL params override the view's own filters (a view is a starting point).
    const merged: InboxFilters = { ...base, ...Object.fromEntries(Object.entries(fromUrl).filter(([k, v]) => (Array.isArray(v) ? v.length : v))) } as InboxFilters;
    merged.tags = fromUrl.tags.length ? fromUrl.tags : base.tags || [];
    return merged;
  }, [sp, view]);

  const setFilters = useCallback((f: InboxFilters) => {
    const next = paramsFromFilters(f);
    if (isRecordRoute && viewId !== "all") next.set("view", viewId);
    for (const k of ["panel", "tab", "via"]) { const v = sp.get(k); if (v) next.set(k, v); }
    setSp(next, { replace: true });
  }, [setSp, sp, isRecordRoute, viewId]);

  const setView = useCallback((id: string) => {
    const keep = new URLSearchParams();
    for (const k of ["panel", "tab", "via"]) { const v = sp.get(k); if (v) keep.set(k, v); }
    if (isRecordRoute) {
      if (id !== "all") keep.set("view", id);
      navigate({ pathname: location.pathname, search: keep.toString() ? `?${keep}` : "" });
    } else {
      navigate({ pathname: id === "all" ? "/inbox" : `/inbox/${id}`, search: "" });
    }
  }, [navigate, isRecordRoute, location.pathname, sp]);

  const saveView = useCallback((label: string) => {
    const id = `v-${Date.now().toString(36)}`;
    const next = [...custom, { id, label, filters: { ...filters } }];
    setCustom(next); persistSavedViews(next);
    navigate(`/inbox/${id}`);
  }, [custom, filters, navigate]);

  const deleteView = useCallback((id: string) => {
    const next = custom.filter((v) => v.id !== id);
    setCustom(next); persistSavedViews(next);
    if (viewId === id) navigate("/inbox");
  }, [custom, viewId, navigate]);

  // Server-side filters → useThreads; view predicate + tags + company → client-side.
  const { unifiedContacts, isLoading, refresh, error: listErr } = useThreads(
    filters.q, filters.team, filters.broadcast, filters.bstatus, filters.channel, filters.account, filters.from, filters.to, filters.archived === "1"
  );
  const contacts = useMemo(() => {
    const pred = viewPredicate(view);
    return unifiedContacts.filter((c) => {
      if (!pred(c)) return false;
      if (filters.tags.length && !filters.tags.every((t) => c.tags?.some((x) => x.tag === t || x.tag_name === t))) return false;
      if (filters.company && (c.contactInfo.company || "") !== filters.company) return false;
      if (filters.kind && !kindMatches(c, filters.kind)) return false;
      return true;
    });
  }, [unifiedContacts, view, filters.tags, filters.company]);

  const totalUnread = useMemo(() => unifiedContacts.reduce((s, c) => s + c.totalUnreadCount, 0), [unifiedContacts]);
  useNotifications(totalUnread);
  const refreshCb = useCallback(() => { refresh(); }, [refresh]);
  useRealtimeThreads(refreshCb);

  const selected = useMemo(() => unifiedContacts.find((c) => c.id === selectedId) || null, [unifiedContacts, selectedId]);

  const openRecord = useCallback((id: string, via?: string) => {
    const next = new URLSearchParams(sp);
    next.delete("panel"); next.delete("tab");
    if (via) next.set("via", via); else next.delete("via");
    if (!isRecordRoute && viewId !== "all") next.set("view", viewId);
    navigate({ pathname: `/t/${encodeURIComponent(id)}`, search: next.toString() ? `?${next}` : "" });
  }, [navigate, sp, isRecordRoute, viewId]);

  const closeRecord = useCallback(() => {
    const next = new URLSearchParams(sp);
    const v = next.get("view") || "all";
    for (const k of ["view", "panel", "tab", "via"]) next.delete(k);
    navigate({ pathname: v === "all" ? "/inbox" : `/inbox/${v}`, search: next.toString() ? `?${next}` : "" });
  }, [navigate, sp]);

  // Details: wide = persistent; laptop = push drawer (⌘.), remembered; tablet/phone = sheet via ?panel=details (history-backed).
  // Laptop default: closed, so the record pane keeps 990px at 1366 (UX-001 §3.1). ⌘. opens it and the choice is remembered.
  const [detailsPref, setDetailsPref] = useState<boolean>(() => { try { return localStorage.getItem(DETAILS_KEY) === "true"; } catch { return false; } });
  const detailsOpen = bp === "wide" ? true : bp === "laptop" ? detailsPref : sp.get("panel") === "details";
  const setDetailsOpen = useCallback((v: boolean) => {
    if (bp === "wide") return;
    if (bp === "laptop") { setDetailsPref(v); try { localStorage.setItem(DETAILS_KEY, String(v)); } catch { /* ignore */ } return; }
    const next = new URLSearchParams(sp);
    if (v) { next.set("panel", "details"); setSp(next); }
    else if (next.get("panel") === "details") { navigate(-1); }
  }, [bp, sp, setSp, navigate]);
  const toggleDetails = useCallback(() => setDetailsOpen(!detailsOpen), [detailsOpen, setDetailsOpen]);

  const [newOpen, setNewOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const pendingSelect = useRef<{ id: string; via: string } | null>(null);
  useEffect(() => {
    if (!pendingSelect.current) return;
    const p = pendingSelect.current;
    if (unifiedContacts.some((c) => c.id === p.id)) { pendingSelect.current = null; openRecord(p.id, p.via); }
  }, [unifiedContacts, openRecord]);

  const value: InboxCtx = {
    bp, coarse, density, setDensity,
    filters, setFilters, viewId, setView, views, saveView, deleteView,
    contacts, allContacts: unifiedContacts, isLoading, refresh, totalUnread,
    listError: listErr ? (listErr.httpStatus === 429 ? "Too many requests — the list is paused for a moment." : (listErr.message || "Could not load conversations.")) : null,
    selectedId, selected, openRecord, closeRecord,
    detailsOpen, setDetailsOpen, toggleDetails,
    newOpen, setNewOpen, paletteOpen, setPaletteOpen, pendingSelect,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
