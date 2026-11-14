"""Fixed facts of the Excom AI module: packs, policies and their ceilings, seeded types, signals, tools.

Records hold everything a business edits (HLD-007 §2). What lives here is what must not be
editable: the most an action may ever be allowed to do, and the seeds created on migrate.
"""

PACKS = ("sales", "purchase", "accounts", "compliance", "custom")

# Each agent acts as its own user; its pack role carries only what that pack needs (HLD-007 §14.2).
PACK_ROLES = {
	"sales": "Excom AI Sales",
	"purchase": "Excom AI Purchase",
	"accounts": "Excom AI Accounts",
	"compliance": "Excom AI Compliance",
	"custom": "Excom AI Custom",
}

# What each pack's role may do in ERPNext (HLD-007 §14.2): only what that type of work needs. No
# submit, cancel or delete anywhere: documents a person must sign off stay drafts.
PACK_PERMISSIONS = {
	"sales": {
		"read": ("Item", "Item Group", "Brand", "Price List", "Item Price", "Pricing Rule", "Currency Exchange", "UOM",
			"Customer", "Customer Group", "Territory", "Country", "Currency", "Company", "Payment Terms Template", "Incoterm",
			"Contact", "Sales Order", "Sales Invoice", "Delivery Note"),
		"write": ("Lead", "Prospect", "Opportunity", "Quotation", "Address", "ToDo"),
	},
	"accounts": {
		"read": ("Sales Invoice", "Payment Entry", "Customer", "Customer Group", "Contact", "Address", "Bank Transaction",
			"Bank Account", "Purchase Invoice", "Supplier", "Supplier Group", "Payment Terms Template", "Company", "Currency",
			"Journal Entry", "Dunning", "Dunning Type"),
		"write": ("ToDo",),
	},
	"purchase": {
		"read": ("Item", "Item Group", "Brand", "UOM", "Supplier", "Supplier Group", "Purchase Order", "Purchase Receipt",
			"Purchase Invoice", "Item Price", "Price List", "Material Request", "Company", "Currency", "Country"),
		"write": ("Request for Quotation", "Supplier Quotation", "Supplier", "Address", "Contact", "ToDo"),
		"select": ("Account",),  # ERPNext reads the supplier's payable account when a Supplier Quotation is saved
	},
	"compliance": {
		"read": ("Sales Invoice", "Purchase Invoice", "Customer", "Supplier", "Company", "Address", "Delivery Note",
			"Payment Entry", "Employee"),
		"write": ("ToDo",),
	},
	"custom": {
		"read": ("Item", "Item Group", "Customer", "Supplier", "Lead", "Opportunity", "Company"),
		"write": ("ToDo",),
	},
}

MODES = ("Off", "Shadow", "Review", "Auto")

# An agent's channel rows that send outreach ("Replies" only answers; "Calls" is a voice line).
OUTREACH_PURPOSES = ("Outreach and replies", "Outreach")

# The most each action may ever be set to, whatever an owner chooses (HLD-007 §12.1).
# Prices, terms and quotations stay with a person; submitting a document is never done by an agent.
CEILINGS = {
	"create_record": "Auto",
	"write_draft": "Auto",
	"first_email": "Auto",
	"follow_up_email": "Auto",
	"reply": "Auto",  # Auto only for the safe intents listed in the policy's condition
	"whatsapp_template": "Review",
	"prices_and_terms": "Review",
	"create_opportunity": "Auto",
	"quotation": "Review",
	"submit_document": "Off",
	"payment_reminder": "Auto",
	"todo_note": "Auto",
	"playbook_change": "Review",  # the coach's suggestions: always a person's decision
	"statement": "Review",  # Accounts: a customer's statement
	"rfq": "Review",  # Purchase: a request for quotation to a supplier
	"po_chase": "Auto",  # Purchase: asking a supplier about a late order
	"voice_call": "Auto",
	"template": "Review",  # HLD-008: an email template (or a WhatsApp one with auto-submission off) waits for a person  # Phase 3: taking the calls nobody answers (Auto is the only mode that takes one)
}

SAFE_REPLY_INTENTS = "ooo, thanks, scheduling, public catalogue"
SAFE_INTENTS = frozenset(i.strip() for i in SAFE_REPLY_INTENTS.split(","))


def intents(condition: str | None) -> set[str]:
	return {i.strip().lower() for i in (condition or "").split(",") if i.strip()}


def ceiling(action: str, pack: str | None = None, condition: str | None = None) -> str:
	"""The most an action may be set to for this pack and condition.

	- the action's own ceiling (CEILINGS; unknown actions: Review);
	- a Custom agent works in Review at most, whatever the action;
	- a reply goes out on its own only for the safe intents: Auto needs a condition naming only
	  those (ooo, thanks, scheduling, public catalogue); otherwise Review.
	"""
	top = CEILINGS.get(action, "Review")
	if pack == "custom" and MODES.index(top) > MODES.index("Review"):
		top = "Review"
	if action == "reply" and top == "Auto":
		wanted = intents(condition)
		if not wanted or not wanted <= SAFE_INTENTS:
			top = "Review"
	return top


def check_policy_rows(rows, pack: str | None) -> None:
	"""Refuse a policy table that names an action twice or goes past a ceiling."""
	import frappe
	from frappe import _

	seen = set()
	for row in rows or []:
		if row.action in seen:
			frappe.throw(_("Row {0}: {1} has two policies.").format(row.idx, row.action))
		seen.add(row.action)
		top = ceiling(row.action, pack, row.condition)
		if MODES.index(row.mode) > MODES.index(top):
			hint = ""
			if row.action == "reply" and pack != "custom":
				hint = " " + _("Replies go out on their own only when the condition names just: {0}.").format(
					SAFE_REPLY_INTENTS
				)
			frappe.throw(_("Row {0}: {1} can be set to {2} at most.").format(row.idx, row.action, top) + hint)


def capped(mode: str, action: str, pack: str | None = None, condition: str | None = None) -> str:
	top = ceiling(action, pack, condition)
	return mode if MODES.index(mode) <= MODES.index(top) else top

DEFAULT_POLICIES = {
	"sales": [
		("voice_call", "Off"),
		("create_record", "Auto"),
		("write_draft", "Auto"),
		("first_email", "Review"),
		("follow_up_email", "Review"),
		("reply", "Review"),
		("whatsapp_template", "Review"),
		("prices_and_terms", "Review"),
		("create_opportunity", "Auto"),
		("quotation", "Review"),
		("submit_document", "Off"),
		("todo_note", "Auto"),
	],
	"purchase": [
		("voice_call", "Off"),
		("create_record", "Auto"),
		("write_draft", "Auto"),
		("rfq", "Review"),
		("po_chase", "Review"),
		("first_email", "Review"),
		("follow_up_email", "Review"),
		("reply", "Review"),
		("prices_and_terms", "Review"),
		("submit_document", "Off"),
		("todo_note", "Auto"),
	],
	"accounts": [
		("voice_call", "Off"),
		("write_draft", "Auto"),
		("create_record", "Auto"),
		("payment_reminder", "Review"),
		("statement", "Review"),
		("reply", "Review"),
		("prices_and_terms", "Review"),
		("submit_document", "Off"),
		("todo_note", "Auto"),
	],
	"compliance": [
		("write_draft", "Auto"),
		("todo_note", "Auto"),
		("submit_document", "Off"),
	],
	"custom": [
		("write_draft", "Review"),
		("reply", "Review"),
		("todo_note", "Review"),
		("submit_document", "Off"),
	],
}

TYPES = [
	("Sales", "sales", "Finds and talks to buyers, answers from the playbook and quotes from your price list.", "#2490EF"),
	("Purchase", "purchase", "Finds suppliers, asks for quotes and chases deliveries.", "#29CD42"),
	("Accounts", "accounts", "Chases payments, keeps promises on record and watches payables.", "#ECAD4B"),
	("Compliance", "compliance", "Tracks due dates and checks documents and transactions.", "#CB2929"),
	("Custom", "custom", "You describe the job in keywords; it works in Review only.", "#7575FF"),
]

# Trade signals (HLD-007 §9): seeded as Proposed; a person confirms what agents may collect.
SIGNALS = [
	("IMPORTS_CATEGORY", "Imports the product category", "Trade", "Text", "volza"),
	("CARRIES_CATEGORY", "Sells the product category", "Trade", "Text", "profile"),
	("BUYER_TYPE", "Buyer type", "Trade", "Text", "profile"),
	("COUNTRY", "Country", "Trade", "Text", "profile"),
	("SIZE_HINT", "Size hint", "Trade", "Text", "profile"),
	("SEASONAL_BUYER", "Buys for a season or festival", "Trade", "Text", "profile"),
	("WEBSITE_LANGUAGE", "Website language", "Profile", "Text", "profile"),
	("HAS_ONLINE_STORE", "Has an online store", "Profile", "Flag", "profile"),
	("DECISION_MAKER_TITLE", "Decision-maker title", "People", "Text", "contacts"),
	("CONTACT_FOUND", "Contact found", "People", "Flag", "contacts"),
	("PUBLISHED_INBOX", "Published inbox", "People", "Text", "contacts"),
	("EMAIL_STATUS", "Email status", "Verification", "Text", "verifier"),
	("EMAIL_PATTERN", "Email pattern", "Verification", "Text", "email_finder"),
]
PAID_SIGNALS = {"EMAIL_STATUS"}

# Outside services (HLD-007 §9). Seeded disabled and without keys.
TOOLS = [
	("osm", "OpenStreetMap (Overpass, Nominatim)", "read", "Free; local businesses by trade tag and city."),
	("serper", "Serper (Google search)", "paid", "About $0.001 a search."),
	("scavio", "Scavio (maps and search)", "paid", "Per page."),
	("dataforseo", "DataForSEO (listings, search, maps)", "paid", "Per task and item."),
	("tinyfish", "TinyFish search", "read", "Free tier."),
	("volza", "Volza import (customs data, CSV)", "read", "Files you export from Volza."),
	("web_reader", "Website profiler", "read", "Homepage, robots, sitemap, team pages; public addresses only."),
	("email_finder", "Email finder (MX, published addresses, patterns)", "read", "No cost."),
	("reoon", "Reoon verifier", "paid", "Per address."),
	("zerobounce", "ZeroBounce verifier", "paid", "Per address."),
	("hunter", "Hunter (domain search, verifier)", "paid", "Monthly quota."),
	("precleared", "Precleared consent check", "paid", "Per address, cached 30 days."),
	("apollo", "Apollo (people at a company, LinkedIn, work email)", "paid",
		"Search is free; about $0.04 a person found (1 credit). Search needs a master API key."),
	("prospeo", "Prospeo (people at a company, LinkedIn, verified email)", "paid",
		"About $0.025 a credit: a search page with results, or a verified email found. No match is free."),
	("mailbox_health", "Mailbox health (SPF, DKIM, DMARC, blocklists)", "read", "No cost."),
	("trulyinbox", "TrulyInbox warm-up status", "read", "Read only."),
]
