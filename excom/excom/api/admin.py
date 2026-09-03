"""
Excom Admin API — one place for everything a manager used to do in Desk.

Thin, meta-driven CRUD over a fixed allow-list of Excom doctypes, plus the team/user
operations that don't fit a generic form. Every endpoint requires Excom Manager or
System Manager (``_check_manager_access``).
"""

import json

import frappe
from frappe import _

from excom.excom.api.chat import _check_manager_access

# Doctypes the generic editor may touch. Read-only ones are audit logs.
ADMIN_DOCTYPES: dict[str, dict] = {
    "Excom Channel Account": {"title": "account_name"},
    "Excom Tag": {"title": "tag_name"},
    "Excom Canned Response": {"title": "title"},
    "Excom Sticker": {"title": "sticker_name"},
    "Excom Notification": {"title": "notification_name"},
    "Excom Intake Source": {"title": "source_name"},
    "Excom Email Signature": {"title": "user"},
    "Excom Subscriber List": {"title": "list_name"},
    "WhatsApp Templates": {"title": "template_name"},
    "Excom Settings": {"title": None, "single": True},
    "Excom Thread Transfer Log": {"title": "thread", "read_only": True},
    "Excom Stage Change Log": {"title": "ref_name", "read_only": True},
    "Excom Notification Log": {"title": "name", "read_only": True},
    # Frappe's own auto-assignment engine, scoped to the doctypes Excom drives.
    "Assignment Rule": {"title": "name", "scope_field": "document_type"},
}


def excom_related_doctypes() -> list[str]:
    """Excom module doctypes + the CRM records Excom creates (via the gateway, never named here)."""
    from excom.excom.services.crm_gateway import crm_doctypes
    # Only records people actually get assigned to — not logs, settings, tokens or messages.
    own = ["Excom Thread", "Excom Intake Log", "Excom Broadcast", "Omni Identity"]
    return sorted(set(own) | set(crm_doctypes()) | {"Contact"})


def _scope_filters(cfg: dict) -> dict | None:
    f = cfg.get("scope_field")
    return {f: ["in", excom_related_doctypes()]} if f else None

_LAYOUT = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold", "Heading"}
_SKIP_FIELDS = {"amended_from"}


def _assert_allowed(doctype: str, write: bool = False) -> dict:
    _check_manager_access()
    cfg = ADMIN_DOCTYPES.get(doctype)
    if not cfg:
        frappe.throw(_("{0} is not managed from the Excom admin").format(doctype), frappe.PermissionError)
    if write and cfg.get("read_only"):
        frappe.throw(_("{0} is read-only").format(doctype), frappe.PermissionError)
    return cfg


def _field_dict(df, meta_of_child=True) -> dict:
    d = {
        "fieldname": df.fieldname,
        "label": df.label or df.fieldname,
        "fieldtype": df.fieldtype,
        "options": df.options or "",
        "reqd": int(df.reqd or 0),
        "read_only": int(df.read_only or 0),
        "description": df.description or "",
        "default": df.default,
        "in_list_view": int(df.in_list_view or 0),
        "depends_on": df.depends_on or "",
        "hidden": int(df.hidden or 0),
    }
    if df.fieldtype in ("Table", "Table MultiSelect") and df.options and meta_of_child:
        cm = frappe.get_meta(df.options)
        d["child_fields"] = [_field_dict(c, False) for c in cm.fields if c.fieldtype not in _LAYOUT and not c.hidden]
    return d


@frappe.whitelist()
def get_schema(doctype: str) -> dict:
    """Sections of editable fields (layout-aware) + the list columns for the table."""
    cfg = _assert_allowed(doctype)
    meta = frappe.get_meta(doctype)
    sections: list[dict] = []
    cur = {"label": "", "fields": []}
    for df in meta.fields:
        if df.fieldtype in ("Section Break", "Tab Break"):
            if cur["fields"]:
                sections.append(cur)
            cur = {"label": df.label or "", "fields": []}
            continue
        if df.fieldtype in _LAYOUT or df.fieldname in _SKIP_FIELDS or df.hidden:
            continue
        cur["fields"].append(_field_dict(df))
    if cur["fields"]:
        sections.append(cur)
    list_fields = [f for s in sections for f in s["fields"] if f["in_list_view"] and f["fieldtype"] not in ("Table", "Table MultiSelect", "Password", "Attach", "Code", "JSON", "Text Editor")]
    if not list_fields:
        list_fields = [f for s in sections for f in s["fields"] if f["fieldtype"] in ("Data", "Select", "Link", "Check", "Int", "Datetime", "Date")][:5]
    if cfg.get("scope_field"):
        for sec in sections:
            for f in sec["fields"]:
                if f["fieldname"] == cfg["scope_field"]:
                    f["link_filters"] = {"name": ["in", excom_related_doctypes()]}
                    f["description"] = (f.get("description") or "") + " Only Excom-related doctypes are offered here."
    return {
        "doctype": doctype,
        "title_field": cfg.get("title") or meta.get_title_field() or "name",
        "single": bool(cfg.get("single") or meta.issingle),
        "read_only": bool(cfg.get("read_only")),
        "needs_name": (meta.autoname or "").lower() == "prompt",
        "sections": sections,
        "list_fields": [f["fieldname"] for f in list_fields],
        "list_labels": {f["fieldname"]: f["label"] for f in list_fields},
        "list_types": {f["fieldname"]: f["fieldtype"] for f in list_fields},
    }


@frappe.whitelist()
def list_docs(doctype: str, q: str = "", limit: int = 200, order_by: str = "modified desc") -> list:
    cfg = _assert_allowed(doctype)
    schema = get_schema(doctype)
    if schema["single"]:
        return [{"name": doctype}]
    fields = ["name", "modified", "owner"] + [f for f in schema["list_fields"] if f != "name"]
    if cfg.get("title") and cfg["title"] not in fields:
        fields.append(cfg["title"])
    or_filters = None
    filters = _scope_filters(cfg)
    if q:
        meta = frappe.get_meta(doctype)
        searchable = [f for f in fields if f != "modified" and (meta.get_field(f) is None or meta.get_field(f).fieldtype in ("Data", "Link", "Select", "Small Text"))]
        or_filters = [[doctype, f, "like", f"%{q}%"] for f in searchable]
    return frappe.get_all(doctype, fields=fields, filters=filters, or_filters=or_filters, limit=min(int(limit), 1000), order_by=order_by)


def _mask_passwords(doc: dict, meta) -> dict:
    for df in meta.fields:
        if df.fieldtype == "Password":
            doc[df.fieldname] = "" if not doc.get(df.fieldname) else "__SET__"
    return doc


@frappe.whitelist()
def get_doc(doctype: str, name: str = "") -> dict:
    cfg = _assert_allowed(doctype)
    meta = frappe.get_meta(doctype)
    if cfg.get("single") or meta.issingle:
        doc = frappe.get_single(doctype)
    else:
        doc = frappe.get_doc(doctype, name)
        _assert_in_scope(cfg, doc)
    d = doc.as_dict()
    for k in list(d.keys()):
        if isinstance(d[k], list):
            d[k] = [{kk: vv for kk, vv in row.items() if not kk.startswith("_") and kk not in ("docstatus", "doctype")} for row in d[k]]
    return _mask_passwords(d, meta)


@frappe.whitelist()
def save_doc(doctype: str, values: str | dict, name: str = "") -> dict:
    """Insert (no name) or update. Password fields are only written when a non-empty value
    other than the ``__SET__`` mask is sent; child tables are replaced wholesale."""
    cfg = _assert_allowed(doctype, write=True)
    values = json.loads(values) if isinstance(values, str) else (values or {})
    meta = frappe.get_meta(doctype)
    if cfg.get("single") or meta.issingle:
        doc = frappe.get_single(doctype)
    elif name:
        doc = frappe.get_doc(doctype, name)
    else:
        doc = frappe.new_doc(doctype)
        if values.get("__newname"):
            doc.__newname = values["__newname"]  # doctypes with autoname = Prompt
    for df in meta.fields:
        if df.fieldtype in _LAYOUT or df.fieldname not in values:
            continue
        v = values[df.fieldname]
        if df.fieldtype == "Password":
            if v and v != "__SET__":
                doc.set(df.fieldname, v)
            continue
        if df.fieldtype in ("Table", "Table MultiSelect"):
            doc.set(df.fieldname, [])
            for row in v or []:
                doc.append(df.fieldname, {k: vv for k, vv in (row or {}).items() if k not in ("name", "idx", "parent", "parenttype", "parentfield", "creation", "modified", "owner", "modified_by")})
            continue
        doc.set(df.fieldname, v)
    _assert_in_scope(cfg, doc)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


def _assert_in_scope(cfg: dict, doc) -> None:
    f = cfg.get("scope_field")
    if f and doc.get(f) not in excom_related_doctypes():
        frappe.throw(_("{0} is outside Excom's scope — manage it in Desk").format(doc.get(f) or doc.name), frappe.PermissionError)


@frappe.whitelist()
def delete_doc(doctype: str, name: str) -> dict:
    cfg = _assert_allowed(doctype, write=True)
    _assert_in_scope(cfg, frappe.get_doc(doctype, name))
    frappe.delete_doc(doctype, name, ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


# ─── Teams ───────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_teams_overview() -> list:
    """Teams with members (incl. disabled-user flag), open-thread counts and account access."""
    _check_manager_access()
    teams = frappe.get_all("Excom Team", fields=["name", "team_name", "description", "parent_team"], order_by="team_name asc")
    members = frappe.db.sql(
        """SELECT tm.parent AS team, tm.user, tm.role, u.full_name, u.user_image, COALESCE(u.enabled, 0) AS enabled
           FROM `tabExcom Team Member` tm LEFT JOIN `tabUser` u ON u.name = tm.user
           WHERE tm.parenttype = 'Excom Team' ORDER BY tm.role DESC, u.full_name""", as_dict=True)
    open_by_team = dict(frappe.db.sql("SELECT assigned_team, COUNT(*) FROM `tabExcom Thread` WHERE status != 'Closed' AND assigned_team IS NOT NULL GROUP BY assigned_team"))
    access = frappe.db.sql(
        """SELECT at.team, at.parent AS account, a.account_name, a.channel FROM `tabExcom Account Team` at
           JOIN `tabExcom Channel Account` a ON a.name = at.parent WHERE at.parenttype = 'Excom Channel Account'""", as_dict=True)
    by_team: dict = {t.name: t for t in teams}
    for t in teams:
        t["members"], t["accounts"], t["open_threads"] = [], [], int(open_by_team.get(t.name) or 0)
    for m in members:
        if m.team in by_team:
            by_team[m.team]["members"].append(m)
    for a in access:
        if a.team in by_team:
            by_team[a.team]["accounts"].append({"name": a.account, "account_name": a.account_name, "channel": a.channel})
    return teams


@frappe.whitelist()
def update_team(team: str, team_name: str = "", description: str = "", parent_team: str = "") -> dict:
    _check_manager_access()
    doc = frappe.get_doc("Excom Team", team)
    doc.description = description
    doc.parent_team = parent_team or None
    doc.save(ignore_permissions=True)
    new_name = doc.name
    if team_name and team_name != doc.team_name:
        doc.db_set("team_name", team_name)
        if doc.name != team_name:
            from frappe.model.rename_doc import rename_doc as _rename
            new_name = _rename("Excom Team", doc.name, team_name, ignore_permissions=True, show_alert=False)
    frappe.db.commit()
    return {"name": new_name}


@frappe.whitelist()
def delete_team(team: str, move_threads_to: str = "") -> dict:
    """Delete a team; its open threads move to another team (or become team-less)."""
    _check_manager_access()
    frappe.db.sql("UPDATE `tabExcom Thread` SET assigned_team = %s WHERE assigned_team = %s", (move_threads_to or None, team))
    frappe.db.delete("Excom Account Team", {"team": team})
    frappe.delete_doc("Excom Team", team, ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def set_member_role(team: str, user: str, role: str) -> dict:
    _check_manager_access()
    if role not in ("Manager", "Member"):
        frappe.throw(_("Role must be Manager or Member"))
    doc = frappe.get_doc("Excom Team", team)
    for m in doc.members:
        if m.user == user:
            m.role = role
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def set_team_accounts(team: str, accounts: str | list) -> dict:
    """Which channel accounts this team may see (writes Excom Channel Account.allowed_teams)."""
    _check_manager_access()
    accounts = json.loads(accounts) if isinstance(accounts, str) else (accounts or [])
    wanted = set(accounts)
    for acc in frappe.get_all("Excom Channel Account", pluck="name"):
        doc = frappe.get_doc("Excom Channel Account", acc)
        has = any(r.team == team for r in doc.allowed_teams)
        if acc in wanted and not has:
            doc.append("allowed_teams", {"team": team})
        elif acc not in wanted and has:
            doc.allowed_teams = [r for r in doc.allowed_teams if r.team != team]
        else:
            continue
        doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


# ─── Users ───────────────────────────────────────────────────────────────────

EXCOM_ROLE_NAMES = ["Excom User", "Excom Manager"]


@frappe.whitelist()
def list_users(q: str = "") -> list:
    """System users with their Excom roles, teams, enabled flag and open-chat count."""
    _check_manager_access()
    filters = {"user_type": "System User", "name": ["not in", ["Guest"]]}
    if q:
        filters = [["user_type", "=", "System User"], ["name", "!=", "Guest"], ["full_name", "like", f"%{q}%"]]
    users = frappe.get_all("User", filters=filters, fields=["name", "full_name", "user_image", "enabled", "last_active"], order_by="enabled desc, full_name asc", limit=500)
    names = [u.name for u in users]
    if not names:
        return []
    roles = frappe.get_all("Has Role", filters={"parent": ["in", names], "parenttype": "User", "role": ["in", EXCOM_ROLE_NAMES + ["System Manager"]]}, fields=["parent", "role"])
    teams = frappe.get_all("Excom Team Member", filters={"user": ["in", names], "parenttype": "Excom Team"}, fields=["user", "parent", "role"])
    open_by_user = dict(frappe.db.sql("SELECT assigned_to, COUNT(*) FROM `tabExcom Thread` WHERE status != 'Closed' AND assigned_to IS NOT NULL GROUP BY assigned_to"))
    from excom.excom.services.crm_gateway import open_lead_counts_by_owner
    leads_by_user = open_lead_counts_by_owner()
    r_by, t_by = {}, {}
    for r in roles:
        r_by.setdefault(r.parent, []).append(r.role)
    for t in teams:
        t_by.setdefault(t.user, []).append({"team": t.parent, "role": t.role})
    for u in users:
        u["roles"] = r_by.get(u.name, [])
        u["teams"] = t_by.get(u.name, [])
        u["open_threads"] = int(open_by_user.get(u.name) or 0)
        u["open_leads"] = int(leads_by_user.get(u.name) or 0)
    return users


@frappe.whitelist()
def set_user_roles(user: str, roles: str | list) -> dict:
    """Grant/revoke the two Excom roles only. System Manager is never touched here."""
    _check_manager_access()
    roles = json.loads(roles) if isinstance(roles, str) else (roles or [])
    wanted = {r for r in roles if r in EXCOM_ROLE_NAMES}
    doc = frappe.get_doc("User", user)
    have = {r.role for r in doc.roles if r.role in EXCOM_ROLE_NAMES}
    if wanted - have:
        doc.add_roles(*(wanted - have))
    if have - wanted:
        doc.remove_roles(*(have - wanted))
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def reassign_user_work(from_user: str, to_user: str = "", include_leads: int = 1) -> dict:
    """Move every open chat (and optionally open Lead) from one user to another — or to nobody.
    The usual fix after a user is disabled or leaves."""
    _check_manager_access()
    to_user = to_user or None
    if to_user and not frappe.db.get_value("User", to_user, "enabled"):
        frappe.throw(_("{0} is disabled").format(to_user))
    threads = frappe.get_all("Excom Thread", filters={"assigned_to": from_user, "status": ["!=", "Closed"]}, pluck="name")
    for t in threads:
        frappe.db.set_value("Excom Thread", t, "assigned_to", to_user, update_modified=False)
    leads = 0
    if int(include_leads or 0):
        from excom.excom.services.crm_gateway import reassign_open_leads
        leads = reassign_open_leads(from_user, to_user)
    if to_user:
        frappe.db.sql("UPDATE `tabToDo` SET allocated_to = %s WHERE allocated_to = %s AND status = 'Open'", (to_user, from_user))
    frappe.db.commit()
    for t in threads:
        frappe.publish_realtime("excom:thread_updated", {"thread": t, "event": "assigned"})
    return {"threads": len(threads), "leads": leads}


# ─── Templates ───────────────────────────────────────────────────────────────

@frappe.whitelist()
def sync_whatsapp_templates() -> dict:
    """Pull approved templates from Meta for every WhatsApp account (same code Desk's Fetch button runs)."""
    _check_manager_access()
    from excom.excom.doctype.whatsapp_templates.whatsapp_templates import fetch
    fetch()
    frappe.db.commit()
    return {"count": frappe.db.count("WhatsApp Templates")}


@frappe.whitelist()
def get_admin_overview() -> dict:
    """Counts for the admin landing cards."""
    _check_manager_access()
    disabled_owners = frappe.db.sql(
        """SELECT COUNT(*) FROM `tabExcom Thread` t JOIN `tabUser` u ON u.name = t.assigned_to
           WHERE t.status != 'Closed' AND u.enabled = 0""")[0][0]
    return {
        "teams": frappe.db.count("Excom Team"),
        "users": frappe.db.count("Has Role", {"role": ["in", EXCOM_ROLE_NAMES], "parenttype": "User"}),
        "accounts": frappe.db.count("Excom Channel Account", {"status": "Active"}),
        "templates": frappe.db.count("WhatsApp Templates"),
        "canned": frappe.db.count("Excom Canned Response"),
        "tags": frappe.db.count("Excom Tag"),
        "stickers": frappe.db.count("Excom Sticker", {"enabled": 1}),
        "notifications": frappe.db.count("Excom Notification", {"disabled": 0}),
        "intake_sources": frappe.db.count("Excom Intake Source", {"enabled": 1}),
        "assignment_rules": frappe.db.count("Assignment Rule", {"disabled": 0, "document_type": ["in", excom_related_doctypes()]}),
        "unassigned": frappe.db.count("Excom Thread", {"status": ["!=", "Closed"], "assigned_to": ["is", "not set"]}),
        "disabled_owner_threads": int(disabled_owners or 0),
    }
