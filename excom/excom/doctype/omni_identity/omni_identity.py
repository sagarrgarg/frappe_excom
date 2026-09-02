import hashlib
import re

import frappe
from frappe import _
from frappe.model.document import Document


class OmniIdentity(Document):
	def validate(self):
		self.normalize_identifiers()
		self.normalize_aliases()
		self.compute_fingerprint()
		self.validate_merge_state()
		self.validate_unique_links()

	def normalize_identifiers(self):
		if self.primary_phone:
			self.normalized_phone = normalize_phone(self.primary_phone)
			if not self.primary_whatsapp:
				self.primary_whatsapp = self.normalized_phone
		else:
			self.normalized_phone = ""

		if self.primary_email:
			self.normalized_email = normalize_email(self.primary_email)
		else:
			self.normalized_email = ""

	def normalize_aliases(self):
		for alias in self.get("aliases", []):
			if alias.alias_type in ("Phone", "WhatsApp"):
				alias.alias_value_normalized = normalize_phone(alias.alias_value_raw)
			elif alias.alias_type == "Email":
				alias.alias_value_normalized = normalize_email(alias.alias_value_raw)
			else:
				alias.alias_value_normalized = (alias.alias_value_raw or "").strip()

	def compute_fingerprint(self):
		"""Deterministic hash from normalized identifiers for fast dedup lookups."""
		parts = sorted(filter(None, [self.normalized_phone, self.normalized_email]))
		if not parts:
			self.hash_fingerprint = ""
			return
		raw = "|".join(parts)
		self.hash_fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:40]

	def validate_merge_state(self):
		if self.status == "Merged" and not self.merged_into:
			frappe.throw(_("A merged identity must reference the master it was merged into."))
		if self.status == "Merged":
			self.is_master = 0

	def validate_unique_links(self):
		"""Ensure no linked entity is already attached to a different Omni Identity."""
		for ln in self.get("linked_entities", []):
			existing_parent = frappe.db.get_value(
				"Omni Identity Link",
				{"linked_doctype": ln.linked_doctype, "linked_name": ln.linked_name},
				"parent",
			)
			if existing_parent and existing_parent != self.name:
				frappe.throw(
					_("{0} {1} is already linked to Omni Identity {2}").format(
						ln.linked_doctype, ln.linked_name, existing_parent
					)
				)

	def add_channel(self, channel_type: str, channel_user_id: str, verified: bool = False):
		"""Add a channel identifier if it doesn't already exist."""
		for ch in self.channels:
			if ch.channel_type == channel_type and ch.channel_user_id == channel_user_id:
				ch.last_seen = frappe.utils.now_datetime()
				if verified:
					ch.verified = 1
				return ch

		row = self.append("channels", {
			"channel_type": channel_type,
			"channel_user_id": channel_user_id,
			"verified": 1 if verified else 0,
			"last_seen": frappe.utils.now_datetime(),
		})
		return row

	def add_link(self, doctype: str, name: str, role: str = "Unknown"):
		"""Link an ERP entity if not already linked."""
		for ln in self.linked_entities:
			if ln.linked_doctype == doctype and ln.linked_name == name:
				return ln

		row = self.append("linked_entities", {
			"linked_doctype": doctype,
			"linked_name": name,
			"role": role,
		})
		return row

	def merge_into(self, master_identity):
		"""Merge this identity into a master. Moves channels and links, marks self as merged."""
		if self.name == master_identity.name:
			frappe.throw(_("Cannot merge an identity into itself."))

		for ch in self.channels:
			master_identity.add_channel(ch.channel_type, ch.channel_user_id, ch.verified)

		for ln in self.linked_entities:
			master_identity.add_link(ln.linked_doctype, ln.linked_name, ln.role)

		if not master_identity.primary_phone and self.primary_phone:
			master_identity.primary_phone = self.primary_phone
		if not master_identity.primary_email and self.primary_email:
			master_identity.primary_email = self.primary_email
		if not master_identity.primary_whatsapp and self.primary_whatsapp:
			master_identity.primary_whatsapp = self.primary_whatsapp

		group_id = master_identity.merge_group_id or master_identity.name
		master_identity.merge_group_id = group_id
		master_identity.flags.ignore_validate = True
		master_identity.save(ignore_permissions=True)

		self.linked_entities = []
		self.status = "Merged"
		self.merged_into = master_identity.name
		self.is_master = 0
		self.merge_group_id = group_id
		self.flags.ignore_validate = True
		self.save(ignore_permissions=True)


def normalize_phone(phone: str) -> str:
	"""Strip everything except digits from a phone number."""
	if not phone:
		return ""
	return re.sub(r"[^\d]", "", phone)


def normalize_email(email: str) -> str:
	"""Lowercase and strip whitespace."""
	if not email:
		return ""
	return email.strip().lower()


@frappe.whitelist()
def resolve_identity(phone: str = "", email: str = "", channel: str = "", channel_user_id: str = "", display_name: str = ""):
	"""
	Identity resolution entry point.

	Resolution chain (first match wins, all skip Merged records):
	  1. normalized_phone on Omni Identity
	  2. alias phone/WhatsApp in Omni Identity Alias
	  3. channel_user_id in Omni Identity Channel
	  4. normalized_email on Omni Identity
	  5. alias email in Omni Identity Alias
	  6. ERPNext reverse lookup: Contact Phone -> linked Omni Identity
	  7. ERPNext reverse lookup: Contact Email -> linked Omni Identity
	  8. No match: create new identity with duplicate detection flag

	On found identity:
	  - New channel is attached.
	  - New phone/email (different from primary) is auto-registered as alias.
	  - Primary phone/email is backfilled if missing.

	Returns the Omni Identity name.
	"""
	norm_phone = normalize_phone(phone)
	norm_email = normalize_email(email)
	identity_name = None

	# --- Step 1: normalized_phone on Omni Identity ---
	if norm_phone:
		identity_name = frappe.db.get_value(
			"Omni Identity",
			{"normalized_phone": norm_phone, "status": ["!=", "Merged"]},
			"name",
		)

	# --- Step 2: phone/WhatsApp alias ---
	if not identity_name and norm_phone:
		alias_hit = frappe.db.sql(
			"""
			SELECT oia.parent FROM `tabOmni Identity Alias` oia
			JOIN `tabOmni Identity` oi ON oi.name = oia.parent
			WHERE oia.alias_value_normalized = %(val)s
			AND oia.alias_type IN ('Phone', 'WhatsApp')
			AND oi.status != 'Merged'
			LIMIT 1
			""",
			{"val": norm_phone},
			as_dict=True,
		)
		if alias_hit:
			identity_name = alias_hit[0].parent

	# --- Step 3: channel_user_id in Omni Identity Channel ---
	if not identity_name and channel and channel_user_id:
		result = frappe.db.sql(
			"""
			SELECT parent FROM `tabOmni Identity Channel`
			WHERE channel_type = %(channel)s AND channel_user_id = %(uid)s
			LIMIT 1
			""",
			{"channel": channel, "uid": channel_user_id},
			as_dict=True,
		)
		if result:
			parent_status = frappe.db.get_value("Omni Identity", result[0].parent, "status")
			if parent_status != "Merged":
				identity_name = result[0].parent

	# --- Step 4: normalized_email on Omni Identity ---
	if not identity_name and norm_email:
		identity_name = frappe.db.get_value(
			"Omni Identity",
			{"normalized_email": norm_email, "status": ["!=", "Merged"]},
			"name",
		)

	# --- Step 5: email alias in Omni Identity Alias ---
	if not identity_name and norm_email:
		email_alias_hit = frappe.db.sql(
			"""
			SELECT oia.parent FROM `tabOmni Identity Alias` oia
			JOIN `tabOmni Identity` oi ON oi.name = oia.parent
			WHERE oia.alias_value_normalized = %(val)s
			AND oia.alias_type = 'Email'
			AND oi.status != 'Merged'
			LIMIT 1
			""",
			{"val": norm_email},
			as_dict=True,
		)
		if email_alias_hit:
			identity_name = email_alias_hit[0].parent

	# --- Step 6: ERPNext reverse lookup via Contact Phone ---
	# A Contact in ERPNext often has both phone and email. If a Contact linked
	# to an Omni Identity already has this phone registered, we can bridge the gap
	# even when the identity itself has a different primary phone.
	if not identity_name and norm_phone:
		contact_by_phone = frappe.db.get_value(
			"Contact Phone", {"phone": norm_phone}, "parent"
		)
		if contact_by_phone:
			linked_oi = frappe.db.get_value(
				"Omni Identity Link",
				{"linked_doctype": "Contact", "linked_name": contact_by_phone},
				"parent",
			)
			if linked_oi:
				oi_status = frappe.db.get_value("Omni Identity", linked_oi, "status")
				if oi_status != "Merged":
					identity_name = linked_oi

	# --- Step 7: ERPNext reverse lookup via Contact Email ---
	if not identity_name and norm_email:
		contact_by_email = frappe.db.get_value(
			"Contact Email", {"email_id": norm_email}, "parent"
		)
		if contact_by_email:
			linked_oi = frappe.db.get_value(
				"Omni Identity Link",
				{"linked_doctype": "Contact", "linked_name": contact_by_email},
				"parent",
			)
			if linked_oi:
				oi_status = frappe.db.get_value("Omni Identity", linked_oi, "status")
				if oi_status != "Merged":
					identity_name = linked_oi

	# --- Found an existing identity: attach and enrich ---
	if identity_name:
		identity = frappe.get_doc("Omni Identity", identity_name)
		changed = False

		if channel and channel_user_id:
			identity.add_channel(channel, channel_user_id, verified=True)
			changed = True

		# Backfill primary identifiers if missing
		if norm_phone and not identity.primary_phone:
			identity.primary_phone = phone
			changed = True
		if norm_email and not identity.primary_email:
			identity.primary_email = email
			changed = True

		# Auto-register new phone as alias when it differs from the primary.
		# This ensures the next message from this number resolves directly via
		# step 2 without needing the ERPNext bridge lookup.
		if norm_phone and identity.normalized_phone and norm_phone != identity.normalized_phone:
			if not any(a.alias_value_normalized == norm_phone for a in identity.get("aliases", [])):
				identity.append("aliases", {
					"alias_type": "Phone",
					"alias_value_raw": phone,
					"alias_value_normalized": norm_phone,
					"verified": 1,
					"source": "Auto",
					"last_seen": frappe.utils.now_datetime(),
				})
				changed = True

		# Auto-register new email as alias when it differs from the primary.
		if norm_email and identity.normalized_email and norm_email != identity.normalized_email:
			if not any(a.alias_value_normalized == norm_email for a in identity.get("aliases", [])):
				identity.append("aliases", {
					"alias_type": "Email",
					"alias_value_raw": email,
					"alias_value_normalized": norm_email,
					"verified": 1,
					"source": "Auto",
					"last_seen": frappe.utils.now_datetime(),
				})
				changed = True

		if changed:
			identity.save(ignore_permissions=True)

		return identity.name

	# --- Step 8: No match found — create new identity ---
	doc = frappe.get_doc({
		"doctype": "Omni Identity",
		"display_name": display_name or phone or email or "Unknown",
		"primary_phone": phone or "",
		"primary_email": email or "",
		"primary_whatsapp": normalize_phone(phone) if phone else "",
		"status": "Active",
		"is_master": 1,
	})

	if channel and channel_user_id:
		doc.append("channels", {
			"channel_type": channel,
			"channel_user_id": channel_user_id,
			"verified": 1,
			"last_seen": frappe.utils.now_datetime(),
		})

	# Duplicate detection: flag if a similar identity is found by name or partial phone.
	# The new record is still created — it is up to a supervisor to review and merge.
	suspected_duplicate = _find_potential_duplicate(
		display_name or phone or email, norm_phone, norm_email,
		normalize_phone(phone) if phone else "",
	)
	if suspected_duplicate:
		doc.needs_review = 1
		doc.potential_duplicate_of = suspected_duplicate

	frappe.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return doc.name


def _find_potential_duplicate(display_name: str, norm_phone: str, norm_email: str = "", primary_whatsapp: str = "") -> str:
	"""
	Lightweight duplicate check run before creating a new Omni Identity.

	Checks:
	  1. Exact display_name match on an existing Active identity.
	  2. Last-10-digit phone match to catch different country-code representations.
	  3. Exact normalized_email match on another Active identity.
	  4. Exact primary_whatsapp match on another Active identity.
	  5. Cross-check: phone/email appears in another identity's aliases.

	Returns the name of a suspected duplicate, or empty string if none found.
	"""
	if display_name and display_name not in ("Unknown", ""):
		match = frappe.db.get_value(
			"Omni Identity",
			{"display_name": display_name, "status": "Active"},
			"name",
		)
		if match:
			return match

	if norm_phone and len(norm_phone) > 10:
		short_phone = norm_phone[-10:]
		result = frappe.db.sql(
			"""
			SELECT name FROM `tabOmni Identity`
			WHERE status = 'Active'
			AND normalized_phone != %(full)s
			AND RIGHT(normalized_phone, 10) = %(short)s
			LIMIT 1
			""",
			{"full": norm_phone, "short": short_phone},
			as_dict=True,
		)
		if result:
			return result[0].name

	if norm_email:
		email_match = frappe.db.get_value(
			"Omni Identity",
			{"normalized_email": norm_email, "status": "Active"},
			"name",
		)
		if email_match:
			return email_match

	if primary_whatsapp:
		wa_match = frappe.db.get_value(
			"Omni Identity",
			{"primary_whatsapp": primary_whatsapp, "status": "Active"},
			"name",
		)
		if wa_match:
			return wa_match

	search_values = [v for v in [norm_phone, norm_email] if v]
	if search_values:
		alias_hit = frappe.db.sql(
			"""
			SELECT oia.parent FROM `tabOmni Identity Alias` oia
			JOIN `tabOmni Identity` oi ON oi.name = oia.parent
			WHERE oia.alias_value_normalized IN %(vals)s
			AND oi.status = 'Active'
			LIMIT 1
			""",
			{"vals": search_values},
			as_dict=True,
		)
		if alias_hit:
			return alias_hit[0].parent

	return ""


@frappe.whitelist()
def merge_identities(source: str, target: str):
	"""Merge source identity into target (master)."""
	source_doc = frappe.get_doc("Omni Identity", source)
	target_doc = frappe.get_doc("Omni Identity", target)

	if source_doc.status == "Merged":
		frappe.throw(_("Source identity is already merged."))

	source_doc.merge_into(target_doc)
	frappe.db.commit()

	return {"merged": source, "into": target}
