import hashlib
import re

import frappe
from frappe import _
from frappe.model.document import Document

# Note: this module defines its own  below, which keeps digits only and
# drops the +.  works from digits, so the two agree.
from excom.excom.utils.phone import phone_variants


class OmniIdentity(Document):
	def validate(self):
		self.normalize_identifiers()
		self.normalize_aliases()
		self.compute_fingerprint()
		self.validate_merge_state()
		self.prune_dead_links()
		self.validate_unique_links()

	def on_update(self):
		"""Opt-out ticks changed by a save (Desk, merge) go into the consent ledger too.

		set_opt_out / clear_opt_out write with db.set_value and record their own rows, so they never
		pass through here.
		"""
		before = self.get_doc_before_save()
		if not before:
			return
		source = self.flags.consent_source or "manual"
		from excom.excom.services.consent import record_consent

		for field, channel, purpose in (
			("do_not_contact", "all", "all"),
			("whatsapp_opt_out", "whatsapp", "all"),
			("email_opt_out", "email", "all"),
			("wa_marketing_opt_out", "whatsapp", "marketing"),
		):
			if bool(before.get(field)) != bool(self.get(field)):
				record_consent(self.name, "Withdrawn" if self.get(field) else "Granted", channel, purpose, source)

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

	def prune_dead_links(self):
		"""Drop link rows whose target no longer exists.

		Frappe validates every Link row on save, so one link to a deleted Contact makes the whole
		identity unsaveable from then on: the record cannot take a new conversation, a new lead or a
		merge, and the failure surfaces far from the deletion that caused it. This is not
		hypothetical — 489 leads failed to get an identity during the CRM migration for exactly this
		reason, each one reported as "Could not find Row #N: Linked Name: ...".

		Deleting the party the link pointed at is the user's decision; refusing to save the identity
		afterwards is not a way to honour it.
		"""
		alive = []
		for row in self.get("links") or []:
			if not row.linked_doctype or not row.linked_name:
				continue
			if frappe.db.exists(row.linked_doctype, row.linked_name):
				alive.append(row)
			else:
				frappe.logger("excom").info(f"Omni Identity {self.name}: dropped dead link {row.linked_doctype} {row.linked_name}")
		if len(alive) != len(self.get("links") or []):
			self.set("links", alive)

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

		# Consent follows the person: the strictest choice of either record wins (on_update writes
		# the ledger rows for anything the master newly carries).
		for flag in ("do_not_contact", "whatsapp_opt_out", "email_opt_out", "wa_marketing_opt_out"):
			if self.get(flag) and not master_identity.get(flag):
				master_identity.set(flag, 1)
		if self.get("wa_marketing_opt_out") and not master_identity.get("wa_marketing_opt_out_source"):
			master_identity.wa_marketing_opt_out_on = self.get("wa_marketing_opt_out_on")
			master_identity.wa_marketing_opt_out_source = self.get("wa_marketing_opt_out_source")
		master_identity.flags.consent_source = f"merge:{self.name}"
		known = {(a.alias_type, a.alias_value_normalized) for a in master_identity.get("aliases", [])}
		for a in self.get("aliases", []):
			if (a.alias_type, a.alias_value_normalized) not in known:
				master_identity.append("aliases", {"alias_type": a.alias_type, "alias_value_raw": a.alias_value_raw,
					"alias_value_normalized": a.alias_value_normalized, "verified": a.verified, "source": a.source or "Auto"})
		if self.get("wa_bsuid"):
			if not master_identity.get("wa_bsuid"):
				master_identity.wa_bsuid = self.wa_bsuid
				master_identity.wa_bsuid_waba = self.get("wa_bsuid_waba")
			elif ("WhatsApp User ID", self.wa_bsuid) not in known:
				master_identity.append("aliases", {"alias_type": "WhatsApp User ID", "alias_value_raw": self.wa_bsuid,
					"alias_value_normalized": self.wa_bsuid, "verified": 1, "source": "Auto"})
		if self.get("wa_username") and not master_identity.get("wa_username"):
			master_identity.wa_username = self.wa_username

		group_id = master_identity.merge_group_id or master_identity.name
		master_identity.merge_group_id = group_id
		master_identity.flags.ignore_validate = True
		master_identity.save(ignore_permissions=True)

		# Re-parent conversations so the inbox shows one contact (QA T6: threads/messages
		# used to stay on the merged identity and surfaced as a duplicate row).
		moved = frappe.get_all("Excom Thread", filters={"omni_identity": self.name}, fields=["name", "channel", "account"])
		frappe.db.set_value("Excom Thread", {"omni_identity": self.name}, "omni_identity", master_identity.name, update_modified=False)
		for t in moved:
			# the key and the denormalised name follow the identity, or the next inbound message opens a second
			# thread for the master and the inbox keeps showing the merged-away name
			key = f"{t.channel}:{t.account}:{master_identity.name}"
			if frappe.db.get_value("Excom Thread", {"thread_key": key, "name": ["!=", t.name]}, "name"):
				key = f"{key}:merged:{t.name}"
			frappe.db.set_value("Excom Thread", t.name, {"thread_key": key, "display_name": master_identity.display_name, "primary_phone": master_identity.primary_phone}, update_modified=False)
		frappe.db.set_value("Excom Message", {"omni_identity": self.name}, "omni_identity", master_identity.name, update_modified=False)
		# Calls follow too. This doctype arrived with the voice channel, after merging was written,
		# so a merge used to leave every call behind pointing at an identity nobody can open — the
		# call history simply vanished from the contact it belonged to.
		if frappe.db.table_exists("Excom Call"):
			frappe.db.set_value("Excom Call", {"omni_identity": self.name}, "omni_identity", master_identity.name, update_modified=False)

		self.linked_entities = []
		self.status = "Merged"
		self.merged_into = master_identity.name
		self.is_master = 0
		self.merge_group_id = group_id
		self.flags.ignore_validate = True
		self.save(ignore_permissions=True)


def is_unnamed(display_name: str, phone: str = "", email: str = "") -> bool:
	"""Has this contact ever really been named?

	A contact created by a call is named after the number it dialled, because at that moment there
	is nothing else to call it. The same is true of one created from a bare email address. Those are
	placeholders wearing the shape of a name, and telling them apart from a real one is what decides
	whether it is safe to write a better name over the top.
	"""
	name = (display_name or "").strip()
	if not name or name == "Unknown":
		return True
	digits = re.sub(r"\D", "", name)
	if digits and digits == re.sub(r"\D", "", phone or ""):
		return True
	if digits and not re.sub(r"[\d\s+()\-]", "", name):
		# All digits and punctuation, so it is a number even if it is not the one we hold.
		return True
	return name.lower() == (email or "").strip().lower()


def name_if_unnamed(identity: str, proposed: str) -> bool:
	"""Give a contact a name, but never take one away.

	Returns whether anything changed. A contact may already have been named by an import, by the
	CRM, or by somebody who actually met them; the rule is that a placeholder can become a name and
	a name never becomes something else.
	"""
	proposed = (proposed or "").strip()
	if not identity or not proposed:
		return False
	row = frappe.db.get_value(
		"Omni Identity", identity, ["display_name", "primary_phone", "primary_email"], as_dict=True
	)
	if not row:
		return False
	if not is_unnamed(row.display_name, row.primary_phone, row.primary_email):
		return False
	if is_unnamed(proposed, row.primary_phone, row.primary_email):
		return False  # swapping one placeholder for another helps nobody
	frappe.db.set_value("Omni Identity", identity, "display_name", proposed, update_modified=False)
	rename_threads(identity, proposed)
	return True


def rename_threads(identity: str, display_name: str) -> None:
	"""Every conversation keeps its own copy of the contact's name, taken when it was created.

	Nothing told them when the contact was renamed, so a conversation went on showing the number
	long after the contact had a name — and the inbox searches that copy, so the contact could not
	be found by name either.
	"""
	for thread in frappe.get_all("Excom Thread", filters={"omni_identity": identity}, pluck="name"):
		frappe.db.set_value("Excom Thread", thread, "display_name", display_name, update_modified=False)


def on_identity_renamed(doc, method=None) -> None:
	"""Hook: keep the conversations' copy of the name in step with the contact's own."""
	if doc.has_value_changed("display_name") and doc.display_name:
		rename_threads(doc.name, doc.display_name)


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
	"""HTTP entry point for identity resolution (session-gated).

	Thin wrapper over :func:`resolve_identity_internal` that enforces excom
	access first. Trusted in-process callers (e.g. the transactional outbound
	service another app calls) use the internal function directly, with no HTTP
	session to check.
	"""
	from excom.excom.api.chat import _check_excom_access
	_check_excom_access()
	return resolve_identity_internal(
		phone=phone,
		email=email,
		channel=channel,
		channel_user_id=channel_user_id,
		display_name=display_name,
	)


def resolve_identity_internal(phone: str = "", email: str = "", channel: str = "", channel_user_id: str = "", display_name: str = "", wa_user_id: str = "", wa_username: str = "", wa_waba: str = ""):
	"""
	Identity resolution entry point.

	Resolution chain (first match wins, all skip Merged records):
	  0. WhatsApp business-scoped user id (BSUID) — Meta always sends it, even when the person
	     hides their number behind a username (2026), so it is the most specific key
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
	wa_user_id = (wa_user_id or "").strip()
	wa_username = (wa_username or "").strip()
	identity_name = find_by_bsuid(wa_user_id) if wa_user_id else None
	matched_by_bsuid = bool(identity_name)
	recycled_from = ""

	# --- Step 1: normalized_phone on Omni Identity ---
	# Every spelling of the number, not just the one we were handed. An exact match is why the same
	# person arrived twice: a telephony webhook says 919250333699, an older import of the same
	# contact says 09250333699, and as strings those never met — so a second identity was created
	# and the conversation split in half. The variants are exact and bounded, never a LIKE.
	if not identity_name and norm_phone:
		for candidate in phone_variants(norm_phone):
			identity_name = frappe.db.get_value(
				"Omni Identity",
				{"normalized_phone": candidate, "status": ["!=", "Merged"]},
				"name",
			)
			if identity_name:
				break

	# --- Step 2: phone/WhatsApp alias ---
	if not identity_name and norm_phone:
		alias_hit = frappe.db.sql(
			"""
			SELECT oia.parent FROM `tabOmni Identity Alias` oia
			JOIN `tabOmni Identity` oi ON oi.name = oia.parent
			WHERE oia.alias_value_normalized IN %(vals)s
			AND oia.alias_type IN ('Phone', 'WhatsApp')
			AND oi.status != 'Merged'
			LIMIT 1
			""",
			{"vals": tuple(phone_variants(norm_phone))},
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

	# --- A known number arriving with a different user id from the same WhatsApp account ---
	# A user id only changes together with a user_changed_* system message (handled by
	# relink_whatsapp_user). A different one on a number we know therefore means a different
	# person — a recycled number, common in India. Do not hand them the old customer's record,
	# threads and opt-outs: open a new identity and flag it for review.
	if identity_name and not matched_by_bsuid and wa_user_id and _bsuid_conflict(identity_name, wa_user_id, wa_waba):
		recycled_from, identity_name = identity_name, None
		if norm_phone:
			_release_number(recycled_from, norm_phone)

	# --- Found an existing identity: attach and enrich ---
	if identity_name:
		identity = frappe.get_doc("Omni Identity", identity_name)
		changed = False

		if channel and channel_user_id:
			identity.add_channel(channel, channel_user_id, verified=True)
			changed = True

		if wa_user_id and _attach_bsuid(identity, wa_user_id, wa_username, wa_waba):
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
		"display_name": display_name or wa_username or phone or email or "Unknown",
		"primary_phone": phone or "",
		"primary_email": email or "",
		"primary_whatsapp": normalize_phone(phone) if phone else "",
		"status": "Active",
		"is_master": 1,
	})
	if wa_user_id:
		_attach_bsuid(doc, wa_user_id, wa_username, wa_waba)

	if channel and channel_user_id:
		doc.append("channels", {
			"channel_type": channel,
			"channel_user_id": channel_user_id,
			"verified": 1,
			"last_seen": frappe.utils.now_datetime(),
		})

	# Duplicate detection: flag if a similar identity is found by name or partial phone.
	# The new record is still created — it is up to a supervisor to review and merge.
	suspected_duplicate = recycled_from or _find_potential_duplicate(
		display_name or phone or email, norm_phone, norm_email,
		normalize_phone(phone) if phone else "",
	)
	if suspected_duplicate:
		doc.needs_review = 1
		doc.potential_duplicate_of = suspected_duplicate

	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	return doc.name


def find_by_bsuid(wa_user_id: str) -> str:
	"""Identity holding this WhatsApp business-scoped user id (current or a past one), or ''."""
	if not wa_user_id:
		return ""
	name = frappe.db.get_value("Omni Identity", {"wa_bsuid": wa_user_id, "status": ["!=", "Merged"]}, "name")
	if name:
		return name
	hit = frappe.db.sql(
		"""
		SELECT oia.parent FROM `tabOmni Identity Alias` oia
		JOIN `tabOmni Identity` oi ON oi.name = oia.parent
		WHERE oia.alias_type = 'WhatsApp User ID' AND oia.alias_value_normalized = %(v)s
		AND oi.status != 'Merged'
		LIMIT 1
		""",
		{"v": wa_user_id},
		as_dict=True,
	)
	return hit[0].parent if hit else ""


def _release_number(identity_name: str, norm_phone: str) -> None:
	"""The number now belongs to someone else: the old record keeps it as history only.

	Its primary phone, phone aliases and WhatsApp channel rows for that number go (they would
	route the new owner's messages here), and the record is flagged for review.
	"""
	variants = set(phone_variants(norm_phone))
	doc = frappe.get_doc("Omni Identity", identity_name)
	if doc.normalized_phone in variants:
		doc.primary_phone = ""
		if normalize_phone(doc.primary_whatsapp) in variants:
			doc.primary_whatsapp = ""
	doc.set("aliases", [a for a in doc.get("aliases", []) if not (a.alias_type in ("Phone", "WhatsApp") and a.alias_value_normalized in variants)])
	doc.set("channels", [c for c in doc.get("channels", []) if not (c.channel_type == "whatsapp" and normalize_phone(c.channel_user_id) in variants)])
	doc.append("aliases", {"alias_type": "Other", "alias_value_raw": f"former WhatsApp number {norm_phone} (now another WhatsApp account)", "verified": 1, "source": "Inbound"})
	doc.needs_review = 1
	doc.save(ignore_permissions=True)


def _bsuid_conflict(identity_name: str, wa_user_id: str, wa_waba: str) -> bool:
	"""True when the identity already has a *different* current user id from the same WhatsApp account."""
	if not wa_waba:
		return False  # user ids differ across business portfolios, so without the account nothing is proven
	row = frappe.db.get_value("Omni Identity", identity_name, ["wa_bsuid", "wa_bsuid_waba"], as_dict=True) or {}
	return bool(row.get("wa_bsuid")) and row.get("wa_bsuid_waba") == wa_waba and row.get("wa_bsuid") != wa_user_id


def _attach_bsuid(identity, wa_user_id: str, wa_username: str = "", wa_waba: str = "") -> bool:
	"""Record a WhatsApp user id on the identity; every id it ever had stays as an alias.

	User ids are per business portfolio and change when the person changes number, so one human
	can carry several. The id from another account than the current one is kept as an alias only.
	Returns True when the document changed (caller saves).
	"""
	changed = False
	if wa_username and identity.get("wa_username") != wa_username:
		identity.wa_username = wa_username
		changed = True
	other_account = bool(wa_waba and identity.get("wa_bsuid_waba") and identity.get("wa_bsuid_waba") != wa_waba)
	if identity.get("wa_bsuid") != wa_user_id and (not identity.get("wa_bsuid") or not other_account):
		identity.wa_bsuid = wa_user_id
		if wa_waba:
			identity.wa_bsuid_waba = wa_waba
		changed = True
	elif not identity.get("wa_bsuid_waba") and wa_waba and identity.get("wa_bsuid") == wa_user_id:
		identity.wa_bsuid_waba = wa_waba
		changed = True
	if not any(a.alias_type == "WhatsApp User ID" and (a.alias_value_normalized or a.alias_value_raw) == wa_user_id for a in identity.get("aliases", [])):
		identity.append("aliases", {
			"alias_type": "WhatsApp User ID",
			"alias_value_raw": f"{wa_user_id}@{wa_waba}" if wa_waba else wa_user_id,
			"alias_value_normalized": wa_user_id,
			"verified": 1,
			"source": "Inbound",
			"last_seen": frappe.utils.now_datetime(),
		})
		changed = True
	return changed


def relink_whatsapp_user(previous_user_id: str = "", new_user_id: str = "", new_phone: str = "", old_phone: str = "", wa_waba: str = "") -> str:
	"""Follow a WhatsApp identity change (system messages user_changed_number / user_changed_user_id).

	The identity is found by the previous BSUID (or the old phone); the new BSUID becomes current
	and the old one stays as an alias. A new number becomes the primary one and the old number is
	kept as an alias, so history and future messages land on the same person.
	Returns the identity name, or '' when nobody matched.
	"""
	name = find_by_bsuid(previous_user_id) if previous_user_id else ""
	if not name and old_phone:
		for candidate in phone_variants(normalize_phone(old_phone)):
			name = frappe.db.get_value("Omni Identity", {"normalized_phone": candidate, "status": ["!=", "Merged"]}, "name")
			if name:
				break
	if not name:
		return ""
	identity = frappe.get_doc("Omni Identity", name)
	changed = False
	if previous_user_id and not any(a.alias_type == "WhatsApp User ID" and a.alias_value_normalized == previous_user_id for a in identity.get("aliases", [])):
		identity.append("aliases", {"alias_type": "WhatsApp User ID", "alias_value_raw": previous_user_id, "alias_value_normalized": previous_user_id, "verified": 1, "source": "Inbound"})
		changed = True
	if new_user_id and identity.get("wa_bsuid") != new_user_id:
		# a relink replaces the current id (same account by definition); the old one stays an alias
		identity.wa_bsuid = ""
		changed = _attach_bsuid(identity, new_user_id, wa_waba=wa_waba or identity.get("wa_bsuid_waba") or "") or changed
	norm_new = normalize_phone(new_phone)
	if norm_new and norm_new != identity.normalized_phone:
		old = identity.normalized_phone
		if old:
			# Kept as history only ("Other" aliases never resolve): the old number may be reissued to
			# someone else, who must not land on this person's record.
			identity.append("aliases", {"alias_type": "Other", "alias_value_raw": f"former WhatsApp number {old}", "alias_value_normalized": f"former:{old}", "verified": 1, "source": "Inbound"})
			identity.set("aliases", [a for a in identity.get("aliases", []) if not (a.alias_type in ("Phone", "WhatsApp") and a.alias_value_normalized in phone_variants(old))])
			identity.set("channels", [c for c in identity.get("channels", []) if not (c.channel_type == "whatsapp" and normalize_phone(c.channel_user_id) in phone_variants(old))])
		holder = frappe.db.get_value("Omni Identity", {"normalized_phone": ["in", phone_variants(norm_new)], "status": ["!=", "Merged"], "name": ["!=", identity.name]}, "name")
		if holder:
			identity.needs_review = 1
			identity.potential_duplicate_of = holder
		identity.primary_phone = norm_new
		identity.primary_whatsapp = norm_new
		identity.add_channel("whatsapp", norm_new, verified=True)
		changed = True
	if changed:
		identity.save(ignore_permissions=True)
	return identity.name


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
	from excom.excom.api.chat import _check_manager_access
	_check_manager_access()
	source_doc = frappe.get_doc("Omni Identity", source)
	target_doc = frappe.get_doc("Omni Identity", target)

	if source_doc.status == "Merged":
		frappe.throw(_("Source identity is already merged."))

	source_doc.merge_into(target_doc)
	frappe.db.commit()

	return {"merged": source, "into": target}


def set_opt_out(identity_name: str, channel: str = "all", source: str = "manual") -> None:
	"""Record a marketing opt-out on an Omni Identity.

	``channel`` is "whatsapp", "email", or "all". "all" also sets the global
	``do_not_contact`` that the transactional send honours on every channel.
	Written with db.set_value so it never trips identity validation and is safe
	to call from the inbound webhook on a STOP reply.
	"""
	updates = {"opt_out_on": frappe.utils.now_datetime(), "opt_out_source": source}
	if channel in ("whatsapp", "all"):
		updates["whatsapp_opt_out"] = 1
	if channel in ("email", "all"):
		updates["email_opt_out"] = 1
	if channel == "all":
		updates["do_not_contact"] = 1
	frappe.db.set_value("Omni Identity", identity_name, updates, update_modified=False)
	from excom.excom.services.consent import record_consent
	record_consent(identity_name, "Withdrawn", channel=channel, purpose="all", source=source)


def clear_opt_out(identity_name: str, channel: str = "all", source: str = "manual") -> None:
	"""Reverse :func:`set_opt_out` — e.g. a START/UNSTOP reply opts back in."""
	updates = {"opt_out_on": frappe.utils.now_datetime(), "opt_out_source": source}
	if channel in ("whatsapp", "all"):
		updates["whatsapp_opt_out"] = 0
	if channel in ("email", "all"):
		updates["email_opt_out"] = 0
	if channel == "all":
		updates["do_not_contact"] = 0
	frappe.db.set_value("Omni Identity", identity_name, updates, update_modified=False)
	from excom.excom.services.consent import record_consent
	record_consent(identity_name, "Granted", channel=channel, purpose="all", source=source)


def on_doctype_update():
	# Identity resolution looks a contact up by phone or email on every inbound message.
	frappe.db.add_index("Omni Identity", ["primary_phone"])
	frappe.db.add_index("Omni Identity", ["primary_email"])
