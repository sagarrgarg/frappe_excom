"""
Email outbound adapter.

Sends through the mailbox's own server (Gmail or JMAP, channels/email/providers) and records an
Excom Message with the metadata only; the body stays in the mailbox. A reply carries the standard
In-Reply-To / References headers, so it threads on every server and in the recipient's mail app.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.channels.email import providers as P


def _cj(raw) -> dict:
	try:
		return json.loads(raw or "{}") or {}
	except (json.JSONDecodeError, TypeError):
		return {}


def reply_headers(thread_name: str, in_reply_to: str = "") -> dict:
	"""In-Reply-To, References and the mailbox thread for an email on this conversation.

	`in_reply_to` is the Excom Message name or the stored mailbox id of the email answered.
	"""
	out = {"in_reply_to": "", "references": "", "thread_id": ""}
	row = None
	if in_reply_to:
		row = frappe.db.get_value(
			"Excom Message", {"provider_message_id": in_reply_to}, ["content_json", "internet_message_id", "provider_thread_id"], as_dict=True
		) or frappe.db.get_value("Excom Message", in_reply_to, ["content_json", "internet_message_id", "provider_thread_id"], as_dict=True)
	if row:
		cj = _cj(row.content_json)
		mid = row.internet_message_id or cj.get("message_id_header", "")
		out["in_reply_to"] = mid
		out["references"] = " ".join(x for x in ((cj.get("references") or "").strip(), mid) if x)
		out["thread_id"] = row.provider_thread_id or cj.get("gmail_thread_id", "")
	if not out["thread_id"]:
		latest = frappe.db.get_value(
			"Excom Message", {"thread": thread_name, "channel": "email", "provider_message_id": ["is", "set"]},
			["content_json", "provider_thread_id"], order_by="provider_timestamp DESC", as_dict=True,
		)
		if latest:
			out["thread_id"] = latest.provider_thread_id or _cj(latest.content_json).get("gmail_thread_id", "")
	return out


def send_email_reply(
	thread_name: str,
	to: str,
	subject: str,
	body_html: str,
	cc: str = "",
	bcc: str = "",
	in_reply_to_gmail_id: str = "",
	draft_name: str = "",
) -> str:
	"""
	Send an email on a conversation and record it. Returns the Excom Message name.

	in_reply_to_gmail_id: the stored mailbox id of the email answered (historic name; any server).
	draft_name: the Excom Email Draft being sent, so the mailbox sends that draft and it leaves Drafts.
	"""
	thread = frappe.get_doc("Excom Thread", thread_name)
	if thread.channel != "email":
		frappe.throw(_("Thread {0} is not an email thread").format(thread_name))
	from excom.excom_ai import send_rules

	# An AI agent's own user only: its own mailboxes, consent, and every recipient is the person.
	send_rules.check(thread, recipients=[to, cc, bcc])

	account = frappe.get_doc("Excom Channel Account", thread.account)
	provider = P.get(account)
	headers = reply_headers(thread_name, in_reply_to_gmail_id)
	mail = {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body_html": body_html, "from_name": "", **headers}

	from excom.excom.channels.email import drafts

	handle = drafts.handle_for_send(draft_name, mail) if draft_name else ""
	result = provider.send(account, {**mail, "draft_id": handle})
	if draft_name:
		drafts.sent(draft_name, account, handle)

	sid = P.store_id(account, result.get("id", ""))
	provider_thread = result.get("thread_id", "")
	snippet = frappe.utils.strip_html_tags(body_html)[:150]
	preview = f"{subject}: {snippet}"[:120]
	content_json = {
		"gmail_message_id": sid,
		"gmail_thread_id": provider_thread if P.provider_name(account) == P.GMAIL else "",
		"provider": P.provider_name(account),
		"provider_thread_id": provider_thread,
		"subject": subject,
		"from_email": account.email_address,
		"from_name": "",
		"to": to,
		"cc": cc,
		"date": "",
		"message_id_header": result.get("message_id_header", ""),
		"in_reply_to": headers["in_reply_to"],
		"references": headers["references"],
		"label_ids": result.get("labels", []),
		"snippet": snippet,
	}

	now = now_datetime()
	msg = frappe.get_doc({
		"doctype": "Excom Message",
		"thread": thread_name,
		"omni_identity": thread.omni_identity,
		"channel": "email",
		"account_doctype": "Excom Channel Account",
		"account": account.name,
		"direction": "Outbound",
		"message_type": "Email",
		"provider_message_id": sid,
		"provider_timestamp": now,
		"content_text": snippet,
		"content_json": json.dumps(content_json),
		"delivery_status": "Sent",
		"created_by_user": frappe.session.user,
		"mail_folder": P.SENT,
		"mail_seen": 1,
		"internet_message_id": (result.get("message_id_header") or "")[:500],
		"provider_thread_id": provider_thread[:255],
	})
	msg.insert(ignore_permissions=True)

	from excom.excom.services.thread_service import _auto_claim_thread
	_auto_claim_thread(thread, frappe.session.user)

	frappe.db.sql(
		"""
		UPDATE `tabExcom Thread`
		SET last_message_at = %(now)s,
			last_outbound_at = %(now)s,
			last_message_preview = %(preview)s,
			last_message_direction = 'Outbound',
			modified = %(now)s
		WHERE name = %(thread)s
		""",
		{"now": now, "preview": preview, "thread": thread_name},
	)

	frappe.publish_realtime(
		"excom:message_sent",
		{
			"thread": thread_name,
			"message": msg.name,
			"omni_identity": thread.omni_identity,
			"direction": "Outbound",
			"preview": preview,
			"delivery_status": "Sent",
		},
		after_commit=True,
	)
	frappe.publish_realtime("excom:thread_updated", {"thread": thread_name, "event": "new_outbound"}, after_commit=True)

	return msg.name


def send_plain(account_name: str, to: str, subject: str, body_html: str) -> dict:
	"""A one-off email from a mailbox outside any conversation (broadcasts, transactional sends),
	through whichever server the mailbox is on."""
	account = frappe.get_doc("Excom Channel Account", account_name)
	return P.get(account).send(account, {"to": to, "subject": subject, "body_html": body_html, "cc": "", "bcc": "",
		"in_reply_to": "", "references": "", "thread_id": "", "from_name": "", "draft_id": ""})
