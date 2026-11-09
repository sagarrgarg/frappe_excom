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

MODES = ("Off", "Shadow", "Review", "Auto")

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
}

SAFE_REPLY_INTENTS = "ooo, thanks, scheduling, public catalogue"

DEFAULT_POLICIES = {
	"sales": [
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
		("create_record", "Auto"),
		("write_draft", "Auto"),
		("first_email", "Review"),
		("follow_up_email", "Review"),
		("reply", "Review"),
		("prices_and_terms", "Review"),
		("submit_document", "Off"),
		("todo_note", "Auto"),
	],
	"accounts": [
		("write_draft", "Auto"),
		("payment_reminder", "Review"),
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
		("write_draft", "Auto"),
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
	("mailbox_health", "Mailbox health (SPF, DKIM, DMARC, blocklists)", "read", "No cost."),
	("trulyinbox", "TrulyInbox warm-up status", "read", "Read only."),
]
