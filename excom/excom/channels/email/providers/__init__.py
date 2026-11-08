"""Mail servers behind one interface: Gmail today, JMAP (Stalwart) alongside, and a mailbox can move
from one to the other without losing its history.

Excom keeps the JMAP model (RFC 8621) whatever the server: every message sits in one folder with a
standard role, and carries read and flagged marks. Gmail's labels are translated onto it
(providers/gmail.py); a JMAP server speaks it natively (providers/jmap.py).

Each provider module implements the same functions, taking the Excom Channel Account document:

    check(account) -> {"ok": bool, "email": str, "error": str}
        Sign in and confirm the mailbox. JMAP also discovers and stores the session.

    sync(account, sink) -> None
        Bring Excom up to date: call sink.ingest(meta) for mail Excom has not seen,
        sink.update(id, folder, seen, flagged) when a message moved or was read elsewhere, and
        sink.remove(id) when it was deleted for good. The provider keeps its own cursor on the
        account (Gmail history id, JMAP Email state). sink.known(id) says whether Excom has it.

    fetch_full(account, id) -> {"headers": {}, "body_html", "body_text",
                                "attachments": [{"filename", "mimeType", "size", "attachmentId"}],
                                "deleted": bool}
    fetch_attachment(account, id, attachment_id) -> bytes

    send(account, mail) -> {"id", "thread_id", "message_id_header"}
        mail: {"to", "cc", "bcc", "subject", "body_html", "in_reply_to", "references",
               "thread_id", "from_name", "draft_id"}. With draft_id, that draft is sent (and leaves
        Drafts) instead of a fresh message.

    move(account, ids, folder) -> None          folder: one of FOLDERS
    set_seen(account, ids, seen: bool) -> None
    set_flagged(account, ids, flagged: bool) -> None

    save_draft(account, mail, draft_id="") -> {"draft_id", "id", "thread_id"}
        Create, or replace, the draft in the mailbox's Drafts folder.
    delete_draft(account, draft_id) -> None
    draft_id_for(account, id) -> str            the draft handle of a message sitting in Drafts

    search(account, query, limit) -> [meta]

`meta` is the same for every server:

    {"id", "thread_id", "folder", "seen", "flagged",
     "categories": [...],      # Gmail's CATEGORY_* sorting; [] elsewhere (used by the Other lane)
     "labels": [...],          # what the server calls it, kept for the record
     "snippet", "received_at": epoch seconds,
     "headers": {"From", "To", "Cc", "Bcc", "Subject", "Date", "Message-ID", "In-Reply-To",
                 "References", "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted"}}

Ids: a provider deals in its own ids. Excom stores them through store_id(), because JMAP ids are
only unique inside one JMAP account while Excom Message.provider_message_id is unique site-wide.
"""

from __future__ import annotations

# The JMAP mailbox roles Excom uses (RFC 8621 §2 / IANA "IMAP Mailbox Name Attributes"),
# as the folder names people see.
INBOX, ARCHIVE, SENT, DRAFTS, SPAM, TRASH = "Inbox", "Archive", "Sent", "Drafts", "Spam", "Trash"
FOLDERS = (INBOX, ARCHIVE, SENT, DRAFTS, SPAM, TRASH)
ROLE_OF = {INBOX: "inbox", ARCHIVE: "archive", SENT: "sent", DRAFTS: "drafts", SPAM: "junk", TRASH: "trash"}
FOLDER_OF_ROLE = {v: k for k, v in ROLE_OF.items()}

# When a message is in several places at once (Gmail labels, JMAP mailboxIds), the one that
# decides what people see first.
PRECEDENCE = (TRASH, SPAM, DRAFTS, INBOX, SENT, ARCHIVE)

GMAIL, JMAP = "Gmail", "JMAP"


def provider_name(account) -> str:
	return JMAP if (account.get("email_provider") or GMAIL) == JMAP else GMAIL


def get(account):
	"""The provider module for this mailbox."""
	if provider_name(account) == JMAP:
		from excom.excom.channels.email.providers import jmap

		return jmap
	from excom.excom.channels.email.providers import gmail

	return gmail


def store_id(account, raw_id: str) -> str:
	"""Excom's site-wide id for a provider message id."""
	if not raw_id:
		return ""
	if provider_name(account) == JMAP:
		prefix = f"jmap:{account.name}:"
		return raw_id if raw_id.startswith(prefix) else prefix + raw_id  # already stored form: as is
	return raw_id


def raw_id(account, stored: str) -> str:
	"""The provider's own id back from Excom's."""
	prefix = f"jmap:{account.name}:"
	if stored and stored.startswith(prefix):
		return stored[len(prefix):]
	return stored or ""


def provider_of_stored(stored: str) -> str:
	"""Which server an id came from: messages synced before a mailbox moved keep their Gmail ids."""
	return JMAP if (stored or "").startswith("jmap:") else GMAIL


def pick_folder(folders) -> str:
	"""One folder from several, by PRECEDENCE; Archive when the message is nowhere in particular."""
	have = set(folders or ())
	for f in PRECEDENCE:
		if f in have:
			return f
	return ARCHIVE
