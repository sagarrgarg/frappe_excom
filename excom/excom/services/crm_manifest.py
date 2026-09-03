"""
Manifest enforcement (P4 §4.3). `roadmap/design/native_crm_manifest.yaml` lists every native doctype,
field and helper excom depends on.

  completeness — every native field name used in crm_gateway.py / crm_compat.py / crm_shadow.py is declared
  existence    — every declared doctype / field / helper exists on the installed ERPNext
  against_schema(dir) — the same existence check against a directory of upstream *.json (offline v16 dry run)

    bench --site <site> execute excom.excom.services.crm_manifest.check
    bench --site <site> execute excom.excom.services.crm_manifest.check --kwargs "{'schema_dir': '/path/to/v16/json'}"
"""

import importlib
import json
import os
import re

import frappe

APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MANIFEST = os.path.join(APP_ROOT, "roadmap", "design", "native_crm_manifest.yaml")
GATEWAY_FILES = ["excom/excom/services/crm_gateway.py", "excom/excom/services/crm_compat.py", "excom/excom/services/crm_shadow.py"]
# Gateway vocabulary and frappe-core columns that are not "native CRM fields" and need no declaration.
IGNORE_FIELDS = {"name", "doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx", "parent", "parenttype", "parentfield", "_assign", "title", "notes", "note"}


def load_manifest() -> dict:
	"""Tiny YAML reader for the manifest's shape (mapping of mappings / lists) — no PyYAML dependency."""
	data: dict = {}
	section = None
	with open(MANIFEST) as f:
		for raw in f:
			line = raw.rstrip("\n")
			if not line.strip() or line.lstrip().startswith("#"):
				continue
			if not line.startswith(" "):
				section = line.split(":")[0].strip()
				data[section] = {}
				continue
			key, _, val = line.strip().partition(":")
			val = val.strip()
			if val.startswith("["):
				data[section][key] = [v.strip() for v in val.strip("[]").split(",") if v.strip()]
			elif val.startswith("{"):
				data[section][key] = val
			else:
				data[section][key] = val
	return data


def _fields_in(meta_like) -> set[str]:
	return {f["fieldname"] for f in meta_like.get("fields", []) if f.get("fieldname")}


def _installed_meta(doctype: str) -> dict | None:
	if not frappe.db.exists("DocType", doctype):
		return None
	m = frappe.get_meta(doctype)
	return {"fields": [{"fieldname": f.fieldname} for f in m.fields]}


def _schema_meta(schema_dir: str, doctype: str) -> dict | None:
	fname = doctype.lower().replace(" ", "_") + ".json"
	for root, _, files in os.walk(schema_dir):
		if fname in files:
			with open(os.path.join(root, fname)) as f:
				return json.load(f)
	return None


def existence(manifest: dict, schema_dir: str | None = None) -> list[str]:
	"""Problems as 'Doctype.field: declared in manifest, absent on <target>' lines."""
	target = f"schema dir {schema_dir}" if schema_dir else f"installed erpnext {_erpnext_version()}"
	get = (lambda dt: _schema_meta(schema_dir, dt)) if schema_dir else _installed_meta
	problems = []
	major = _major_version(schema_dir)
	for dt, spec in manifest.get("doctypes", {}).items():
		spec = str(spec)
		if "since: v16" in spec and major < 16:
			continue  # not expected before v16
		if "until: v16" in spec and major >= 16:
			continue  # removed in v16 by design (attribution moved to UTM *)
		if get(dt) is None:
			problems.append(f"{dt}: doctype declared in manifest, absent on {target}")
	for dt, fields in manifest.get("native_fields_reused", {}).items():
		meta = get(dt)
		if meta is None:
			continue
		have = _fields_in(meta)
		for f in fields:
			if f not in have:
				problems.append(f"{dt}.{f}: declared in manifest, absent on {target} — see RES-001 §3.2")
	if not schema_dir:
		for dt, fields in manifest.get("excom_custom_fields", {}).items():
			meta = get(dt)
			if meta is None:
				continue
			have = _fields_in(meta)
			for f in fields:
				if f not in have:
					problems.append(f"{dt}.{f}: excom custom field missing on the installed site — run crm_schema.apply()")
		for helper in manifest.get("helpers", {}):
			mod, _, fn = helper.rpartition(".")
			try:
				if not hasattr(importlib.import_module(mod), fn):
					problems.append(f"{helper}: helper absent on {target}")
			except ImportError:
				problems.append(f"{helper}: module absent on {target}")
	return problems


def completeness(manifest: dict) -> list[str]:
	"""Native field names the gateway touches that the manifest does not declare."""
	declared: dict[str, set[str]] = {}
	for dt, fields in manifest.get("native_fields_reused", {}).items():
		declared.setdefault(dt, set()).update(fields)
	for dt, fields in manifest.get("excom_custom_fields", {}).items():
		declared.setdefault(dt, set()).update(fields)
	# attribution fields live in crm_compat and are version-dependent by design
	declared.setdefault("Lead", set()).update({"source", "campaign_name", "utm_source", "utm_campaign", "utm_medium"})
	declared.setdefault("Opportunity", set()).update({"source", "campaign", "utm_source", "utm_campaign", "utm_medium", "pipeline_stage"})
	src = ""
	for rel in GATEWAY_FILES:
		p = os.path.join(APP_ROOT, rel)
		if os.path.exists(p):
			src += open(p).read() + "\n"
	literals = set(re.findall(r'"([a-z_][a-z0-9_]{2,})"', src))
	problems = []
	for dt in ("Lead", "Opportunity", "Customer", "Prospect"):
		if not frappe.db.exists("DocType", dt):
			continue
		native = {f.fieldname for f in frappe.get_meta(dt).fields if f.fieldname and not getattr(f, "is_custom_field", False) and not (f.fieldname in {x for s in manifest.get("excom_custom_fields", {}).values() for x in s})}
		used = (literals & native) - IGNORE_FIELDS
		undeclared = sorted(used - declared.get(dt, set()))
		if undeclared:
			problems.append(f"{dt}: gateway uses native fields not in the manifest: {', '.join(undeclared)}")
	return problems


def _major_version(schema_dir: str | None) -> int:
	if schema_dir:
		# upstream checkout: erpnext/__init__.py next to the json is not fetched; infer from the UTM doctypes' presence
		return 16 if _schema_meta(schema_dir, "UTM Source") else 15
	try:
		return int(_erpnext_version().split(".")[0])
	except Exception:
		return 15


def _erpnext_version() -> str:
	try:
		import erpnext
		return erpnext.__version__
	except Exception:
		return "?"


def check(schema_dir: str | None = None, raise_on_error: bool = False) -> dict:
	manifest = load_manifest()
	problems = existence(manifest, schema_dir)
	if not schema_dir:
		problems += completeness(manifest)
	out = {"target": schema_dir or f"installed erpnext {_erpnext_version()}", "ok": not problems, "problems": problems}
	for p in problems:
		print("MANIFEST:", p)
	if not problems:
		print(f"MANIFEST OK against {out['target']}")
	if raise_on_error and problems:
		frappe.throw("\n".join(problems))
	return out
