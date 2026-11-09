"""From keywords to what they mean in this company's ERP (HLD-007 §6.1).

Each keyword is tried in this order; the first match wins, and every match is shown so the manager
can correct it:

1. the glossary (a person confirmed "water gun" means item group Pichkari);
2. built-in concepts (export, distributor, transporter…), regions (Latin America, GCC…) and
   languages (Spanish → es, its countries and timezones). A record with the same name (GGIL has
   an item group called Export) is shown as the alternative;
3. records whose name is the keyword, then records whose name contains it; a record that covers
   nothing (an empty item group) is passed over;
4. evidence from item names: "water gun" appears in 150 item names, 140 of them in the item group
   Pichkari, so the builder proposes that as a glossary entry for a person to confirm;
5. unmatched (the AI may propose a recipe, §6.4).

Everything here reads through frappe.get_list / get_all as the person building the agent; counts
are what that person may see.
"""

from __future__ import annotations

import re

import frappe
from frappe import _

from excom.excom_ai.builder import geo

# word -> concept key. A concept is a business situation, not a record; recipes give it meaning.
CONCEPTS: dict[str, dict] = {
	"export": {"words": ("export", "exports", "exporter", "overseas", "international", "foreign buyers"),
		"label": "Export: customers abroad, foreign-currency sales"},
	"import": {"words": ("import", "imports", "importing", "overseas suppliers"), "label": "Import: suppliers abroad"},
	"domestic": {"words": ("domestic", "india", "local", "national"), "label": "Domestic: customers at home"},
	"b2b": {"words": ("b2b", "wholesale", "wholesaler", "wholesalers", "trade buyers"), "label": "Business buyers"},
	"b2c": {"words": ("b2c", "consumers", "retail customers", "end customers"), "label": "Consumers"},
	"distributor": {"words": ("distributor", "distributors", "importer", "importers", "dealer", "dealers"),
		"label": "Distributors and importers"},
	"retailer": {"words": ("retailer", "retailers", "shops", "stores", "supermarkets", "chains"), "label": "Retailers"},
	"oem": {"words": ("oem", "private label", "white label", "custom branding"), "label": "OEM and private label"},
	"marketplace": {"words": ("marketplace", "marketplaces", "amazon", "flipkart", "ecommerce", "e-commerce"),
		"label": "Marketplaces"},
	"logistics": {"words": ("transporter", "transporters", "logistics", "freight", "forwarder", "forwarders", "shipping",
		"courier", "customs"), "label": "Transporters, forwarders and customs agents"},
	"receivables": {"words": ("receivables", "collections", "dues", "outstanding", "payment follow up", "overdue"),
		"label": "Money owed to us"},
	"payables": {"words": ("payables", "msme", "vendor payments", "supplier payments"), "label": "Money we owe"},
	"compliance": {"words": ("gst", "compliance", "e-invoice", "einvoice", "e-way bill", "eway bill", "tds", "lut"),
		"label": "Tax and compliance"},
	"purchase": {"words": ("raw material", "raw materials", "purchase", "procurement", "sourcing", "rfq"),
		"label": "Buying and raw material"},
}

# Records a keyword may name, with how many things each one covers.
VOCABULARY = [
	("Item Group", "item_group_name"), ("Brand", "brand"), ("Customer Group", "customer_group_name"),
	("Supplier Group", "supplier_group_name"), ("Territory", "territory_name"), ("Country", "country_name"),
	("Currency", "currency_name"), ("Price List", "price_list_name"), ("Excom Team", "team_name"),
	("Excom Channel Account", "account_name"),
]


def normal(word: str) -> str:
	w = re.sub(r"\s+", " ", (word or "").strip().lower())
	return w


def singular(word: str) -> str:
	w = normal(word)
	for suffix, repl in (("ies", "y"), ("ses", "s"), ("s", "")):
		if len(w) > 3 and w.endswith(suffix) and not w.endswith("ss"):
			return w[: -len(suffix)] + repl
	return w


def split(keywords: str | list) -> list[str]:
	items = keywords if isinstance(keywords, list) else (keywords or "").split(",")
	out = []
	for k in items:
		k = normal(k)
		if k and k not in out:
			out.append(k)
	return out


def resolve(keywords, pack: str = "sales") -> list[dict]:
	"""One row per keyword: {keyword, resolved_as, label, link_doctype, link_name, countries, language,
	concept, matches, detail, proposal}."""
	return [resolve_one(k, pack) for k in split(keywords)]


def resolve_one(keyword: str, pack: str = "sales") -> dict:
	row = frappe._dict(keyword=keyword, resolved_as="Unmatched", label="", link_doctype=None, link_name=None,
		concept=None, countries=[], language=None, matches=0, detail="", proposal=None, alternative=None, recipe=None)
	hit = _glossary(keyword)
	if hit:
		return _record(row, *hit, how="Vocabulary", via=_("glossary"))
	same_name = _exact_record(keyword)
	alternative = {"link_doctype": same_name[0], "link_name": same_name[1]} if same_name else None
	concept = _concept(keyword)
	if concept:
		row.update(resolved_as="Concept", concept=concept, label=CONCEPTS[concept]["label"], alternative=alternative,
			detail=_("also a {0} named {1}").format(*same_name) if same_name else "")
		return row
	reg = geo.region(keyword)
	if reg:
		row.update(resolved_as="Region", label=reg[0].title(), countries=reg[1], matches=len(reg[1]),
			detail=_("{0} countries").format(len(reg[1])), alternative=alternative)
		return row
	lang = geo.language(keyword)
	if lang:
		code, info = lang
		row.update(resolved_as="Language", language=code, label=info["name"], countries=info["countries"],
			matches=len(info["countries"]), detail=_("{0}, spoken in {1} countries").format(code, len(info["countries"])),
			alternative=alternative)
		return row
	if same_name:
		return _record(row, *same_name, how="Vocabulary")
	recipe = frappe.db.get_value("Excom AI Recipe", {"kind": "Query spec", "enabled": 1,
		"keywords": ["like", f"%{keyword}%"]}, ["name", "label"], as_dict=True)
	if recipe:
		row.update(resolved_as="Concept", concept=None, recipe=recipe.name, label=recipe.label,
			detail=_("a saved recipe"))
		return row
	hit = _contains_record(keyword)
	proposal = _item_evidence(keyword)
	# The items' own names outweigh a small group that merely has the words in its name
	# (GGIL: "Water Gun SF", 9 items, against Pichkari, where most water guns are, 189 items).
	if proposal and (not hit or (proposal["item_group"] != hit[1] and proposal["group_items"] > count_for(*hit))):
		row.update(resolved_as="AI proposal", proposal=proposal, label=proposal["label"], matches=proposal["group_items"],
			link_doctype="Item Group", link_name=proposal["item_group"],
			alternative={"link_doctype": hit[0], "link_name": hit[1]} if hit else None,
			detail=_("{0} of the {1} items named like '{2}' are in {3} ({4} items): confirm it once for the glossary").format(
				proposal["in_group"], proposal["items"], keyword, proposal["item_group"], proposal["group_items"]))
		return row
	if hit:
		return _record(row, *hit, how="Vocabulary", via=_("name contains"))
	return row


def _record(row, doctype: str, name: str, how: str, via: str = ""):
	n = count_for(doctype, name)
	row.update(resolved_as=how, link_doctype=doctype, link_name=name, label=f"{doctype}: {name}", matches=n,
		detail=(f"{via}: " if via else "") + describe_count(doctype, n))
	if doctype == "Country":
		row.countries = [name]
	return row


def _glossary(keyword: str):
	for term in {keyword, singular(keyword)}:
		g = frappe.db.get_value("Excom AI Glossary", {"term": term, "confirmed_by": ["is", "set"]},
			["link_doctype", "link_name"], as_dict=True)
		if g:
			return g.link_doctype, g.link_name
	return None


def _concept(keyword: str) -> str | None:
	for key, c in CONCEPTS.items():
		if keyword in c["words"] or singular(keyword) in c["words"]:
			return key
	return None


def _exact_record(keyword: str):
	for doctype, field in VOCABULARY:
		if not frappe.db.table_exists(doctype):
			continue
		for term in {keyword, singular(keyword)}:
			name = frappe.db.get_value(doctype, {"name": term}) or (
				frappe.db.get_value(doctype, {field: term}) if frappe.get_meta(doctype).has_field(field) else None)
			if name and count_for(doctype, name):
				return doctype, name
	return None


def _contains_record(keyword: str):
	best = None
	for doctype, _field in VOCABULARY:
		if doctype in ("Country", "Currency") or not frappe.db.table_exists(doctype):
			continue
		for name in frappe.get_all(doctype, {"name": ["like", f"%{singular(keyword)}%"]}, pluck="name", limit=5):
			n = count_for(doctype, name)
			if n and (not best or n > best[2]):
				best = (doctype, name, n)
	return best[:2] if best else None


def _item_evidence(keyword: str) -> dict | None:
	"""Which item group the items named like the keyword sit in (a glossary proposal)."""
	words = [w for w in singular(keyword).split() if len(w) > 2]
	if not words:
		return None
	filters = [["Item", "disabled", "=", 0]] + [["Item", "item_name", "like", f"%{w}%"] for w in words]
	rows = frappe.get_all("Item", filters=filters, fields=["item_group", {"COUNT": "*", "as": "n"}],
		group_by="item_group", order_by="n desc", limit=5)
	total = sum(r.n for r in rows)
	if not total or rows[0].n / total < 0.6:
		return None
	return {"item_group": rows[0].item_group, "items": total, "in_group": rows[0].n,
		"group_items": count_for("Item Group", rows[0].item_group),
		"label": f"Item Group: {rows[0].item_group}?", "term": singular(keyword)}


def count_for(doctype: str, name: str) -> int:
	"""How many things a record covers: items in a group, customers in a group or country…"""
	try:
		if doctype == "Item Group":
			groups = item_group_tree(name)
			return frappe.db.count("Item", {"item_group": ["in", groups], "disabled": 0})
		if doctype == "Brand":
			return frappe.db.count("Item", {"brand": name, "disabled": 0})
		if doctype == "Customer Group":
			return frappe.db.count("Customer", {"customer_group": name, "disabled": 0})
		if doctype == "Supplier Group":
			return frappe.db.count("Supplier", {"supplier_group": name, "disabled": 0})
		if doctype == "Territory":
			return frappe.db.count("Customer", {"territory": name, "disabled": 0})
		if doctype == "Country":
			return len(customers_in_countries([name]))
		if doctype == "Price List":
			return frappe.db.count("Item Price", {"price_list": name})
		if doctype == "Currency":
			return frappe.db.count("Customer", {"default_currency": name, "disabled": 0})
		if doctype == "Excom Team":
			return frappe.db.count("Excom Team Member", {"parent": name, "parenttype": "Excom Team"})
	except Exception:
		return 0
	return 1


def describe_count(doctype: str, n: int) -> str:
	what = {"Item Group": "items", "Brand": "items", "Customer Group": "customers", "Supplier Group": "suppliers",
		"Territory": "customers", "Country": "customers", "Price List": "prices", "Currency": "customers",
		"Excom Team": "members"}.get(doctype, "")
	return f"{n} {what}".strip() if what else ""


def item_group_tree(group: str) -> list[str]:
	lft, rgt = frappe.db.get_value("Item Group", group, ["lft", "rgt"]) or (None, None)
	if lft is None:
		return [group]
	return frappe.get_all("Item Group", {"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name")


def customers_in_countries(countries: list[str]) -> list[str]:
	"""Customers with an address in these countries (through Dynamic Link)."""
	if not countries:
		return []
	rows = frappe.db.sql(
		"""SELECT DISTINCT dl.link_name FROM `tabAddress` a JOIN `tabDynamic Link` dl
		ON dl.parent = a.name AND dl.parenttype = 'Address' AND dl.link_doctype = 'Customer'
		WHERE a.country IN %(c)s AND IFNULL(a.disabled, 0) = 0""", {"c": tuple(countries)})
	return [r[0] for r in rows]


def scope_from(rows: list[dict]) -> frappe._dict:
	"""What the included keywords add up to, for the recipes."""
	s = frappe._dict(concepts=[], item_groups=[], brands=[], item_codes=[], customer_groups=[], supplier_groups=[],
		territories=[], countries=[], languages=[], price_lists=[], currencies=[], teams=[], accounts=[], recipes=[])
	key = {"Item Group": "item_groups", "Brand": "brands", "Item": "item_codes", "Customer Group": "customer_groups",
		"Supplier Group": "supplier_groups", "Territory": "territories", "Country": "countries",
		"Price List": "price_lists", "Currency": "currencies", "Excom Team": "teams", "Excom Channel Account": "accounts"}
	for r in rows:
		r = frappe._dict(r)
		if not r.get("included", 1):
			continue
		if r.resolved_as == "Concept":
			if r.get("concept"):
				s.concepts.append(r.concept)
			if r.get("recipe"):
				s.recipes.append(r.recipe)
		elif r.resolved_as in ("Region", "Language"):
			s.countries += [c for c in r.countries if c not in s.countries]
			if r.language:
				s.languages.append(r.language)
		elif r.link_doctype in key and r.resolved_as in ("Vocabulary",):
			s[key[r.link_doctype]].append(r.link_name)
			if r.link_doctype in ("Item Group", "Brand", "Item"):
				same = normal(r.link_name) in (r.keyword, singular(r.keyword))
				s.setdefault("product_words", []).append(r.link_name if same else f"{r.keyword} ({r.link_name})")
	# A region and a language together mean the countries in both (Latin America + Spanish → not Brazil).
	regions = [r for r in rows if r.get("resolved_as") == "Region" and r.get("included", 1)]
	langs = [r for r in rows if r.get("resolved_as") == "Language" and r.get("included", 1)]
	if regions and langs:
		in_region = {c for r in regions for c in r["countries"]}
		spoken = {c for r in langs for c in r["countries"]}
		s.countries = [c for c in s.countries if c in in_region and c in spoken]
		s.left_out = sorted(in_region - spoken)  # e.g. Brazil: Portuguese
	return s
