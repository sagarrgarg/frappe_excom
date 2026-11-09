# Parts of this file are ported from Harvey (github.com/ethanplusai/harvey): the discovery providers, junk
# filters and business names (harvey/collectors/discover.py) and the CSV import (harvey/importer.py).
#
# MIT License
#
# Copyright (c) 2026 Ethan Rogers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Discovery (HLD-007 §9): who might buy, from maps, search engines and customs data.

Every source answers `search(query)` with company dicts in one shape, so the research task does not
care where a company came from:

    query = {keywords: [str], countries: [Frappe country], languages: [code], category: str,
             city: str | None, limit: int, buyer_types: [importer|distributor|wholesaler|retailer]}
    company = {name, domain, website, country, city, phone, source, source_url, external_id, industry,
               buyer_type, signals: {SIGNAL_CODE: value}}

- **osm** (free): Nominatim finds the city, Overpass the shops and offices tagged for the trade
  (toys → shop=toys …). Only trades in TAG_MAP: a source that cannot answer says so rather than
  returning every office in town (Harvey's lesson).
- **serper**, **scavio**, **dataforseo** (paid) and **tinyfish** (free with a key): Google-style
  searches built from keywords x countries in the market's language ("importador de juguetes
  México"). Marketplaces, directories, social networks and the press are dropped.
- **volza**: no API. `import_file(file_url)` reads a Volza export the user uploaded and returns one
  company per importer with what it imports; `import_rows(rows)` is the same for any CSV or JSON list
  (fair lists, directories), with column names normalised.

A search runs several queries; a paid one is charged per query, so callers pass
`cost=estimate(key, query)` to tools.use. Pacing for the free services (Nominatim one request a
second, Overpass and TinyFish a little slower) is kept here, per process.
"""

from __future__ import annotations

import base64
import csv
import io
import re
import time
import unicodedata
from collections import Counter

import frappe
import requests
from frappe import _
from frappe.utils import flt, getdate

from excom.excom_ai.tools import Tool, ToolRefused, tool
from excom.excom_ai.tools.web import normalize_domain, user_agent

TIMEOUT = (10, 45)
MAX_QUERIES = 12
BUYER_TYPES = ("Importer", "Distributor", "Wholesaler", "Retailer", "Online store", "Manufacturer", "Other")

# ── pacing ───────────────────────────────────────────────────────────────────

_last_call: dict[str, float] = {}
_sleep = time.sleep  # a seam for tests


def _pace(name: str, interval: float) -> None:
	wait = _last_call.get(name, 0.0) + interval - time.monotonic()
	if wait > 0:
		_sleep(wait)
	_last_call[name] = time.monotonic()


# ── the shape every source returns ───────────────────────────────────────────


def company(name: str, source: str, **kw) -> dict:
	website = kw.get("website") or None
	domain = normalize_domain(kw.get("domain") or website or "") or None
	out = {"name": (name or "").strip()[:140] or None, "domain": domain, "website": website, "country": kw.get("country"),
		"city": kw.get("city") or None, "phone": kw.get("phone") or None, "source": source,
		"source_url": kw.get("source_url") or None, "external_id": kw.get("external_id") or None,
		"industry": kw.get("industry") or None, "buyer_type": kw.get("buyer_type") or None, "signals": {}}
	for k in ("emails", "import_summary", "address"):
		if kw.get(k):
			out[k] = kw[k]
	if out["country"]:
		out["signals"]["COUNTRY"] = out["country"]
	if out["buyer_type"]:
		out["signals"]["BUYER_TYPE"] = out["buyer_type"]
	out["signals"].update({k: v for k, v in (kw.get("signals") or {}).items() if v not in (None, "")})
	return out


_BUYER_WORDS = (
	("Importer",
		r"import(?:er|ers|ador|adora|adores|ateur|ateurs|eur|atore|atori|ação|acao|aciones)?\b|importa(?:mos|ção)"),
	("Distributor",
		r"distribu(?:tor|tors|idor|idora|idores|teur|teurs|tore|tori|ción|cion|ição|icao)\b|distribuimos|vertrieb"),
	("Wholesaler",
		r"wholesal\w*|mayorist\w*|mayoreo|al por mayor|atacad\w*|grossist\w*|großhandel|grosshandel|ingrosso|groothandel"),
	("Manufacturer", r"manufactur\w*|fabricante\w*|fábrica|fabrica\b|factory|fabricant\w*|hersteller|produttore"),
	("Online store",
		r"online store|tienda (?:online|en línea|virtual)|loja (?:virtual|online)|e-?shop|boutique en ligne|onlineshop"),
	("Retailer",
		r"retail\w*|minorist\w*|tienda\w*|juguetería|jugueteria|loja\b|store\b|shop\b|magasin|negozio|einzelhandel"),
)


def buyer_type_of(text: str) -> str | None:
	"""The buyer type a title or description announces, most specific first."""
	low = (text or "").lower()
	return next((bt for bt, pattern in _BUYER_WORDS if re.search(r"\b(?:" + pattern + ")", low)), None)


# Junk (ported from Harvey, retuned for trade): marketplaces, directories, trade-data sites, social
# networks, reference and press. Anchored on a dot boundary: results come as m.facebook.com too.
JUNK_DOMAINS = tuple(re.compile(p, re.I) for p in (
	r"(^|\.)(amazon|ebay|alibaba|aliexpress|made-in-china|globalsources|dhgate|tradekey|ec21|tradeindia|indiamart)\.",
	r"(^|\.)(exportersindia|mercadolibre|mercadolivre|mercadolibre\.com|linio|olx|shopee|temu|wish|etsy|walmart)\.",
	r"(^|\.)(facebook|instagram|twitter|x|linkedin|youtube|tiktok|pinterest|whatsapp|threads|telegram)\.",
	r"(^|\.)(wikipedia|wikidata|reddit|quora|medium|substack|forbes|bloomberg|reuters)\.",
	r"(^|\.)(yelp|yellowpages|paginasamarillas|seccionamarilla|cylex|hotfrog|infoisinfo|empresite|einforma|axesor)\.",
	r"(^|\.)(kompass|europages|dnb|zoominfo|crunchbase|opencorporates|guiamais|econodata|apontador|cnpj|dun)\.",
	r"(^|\.)(panjiva|importgenius|volza|zauba|trademap|52wmb|importkey|tradeatlas|seair|exportgenius|eximpedia)\.",
	r"(^|\.)(indeed|glassdoor|computrabajo|occ|bumeran|infojobs|jooble)\.",
	r"(^|\.)(google|bing|yahoo|duckduckgo|apple|microsoft|wordpress|wix|blogspot|issuu|scribd|slideshare)\.",
	r"(directory|directorio|listings?|top\d+|best-?of)\.",
	r"\.(gov|gob|gouv|mil|edu)(\.[a-z]{2})?$",
	r"(news|noticias|magazine|revista|journal|periodico|diario)\.(com|net|org)(\.[a-z]{2})?$",
))
JUNK_NAMES = tuple(re.compile(p, re.I) for p in (
	r"^\s*(top|best|los|las)?\s*\d+\s+(best|mejores|principales)?",
	r"\b(directory|directorio|listings?|list of|lista de|ranking|reviews? of|compare)\b",
	r"\b(los|las) (mejores|principales)\b",
	r"\b(compan(y|ies)|empresas|firms?)\s+(list|lista)\b",
))


def is_junk(domain: str, name: str = "") -> bool:
	if domain and (any(p.search(domain) for p in JUNK_DOMAINS) or "." not in domain or re.fullmatch(r"[\d.]+", domain)):
		return True
	return bool(name) and any(p.search(name) for p in JUNK_NAMES)


# Business names from a search title (Harvey's business_name): the title segment that shares a
# distinctive word with the domain, else a name made from the domain.
_SEPARATORS = re.compile(r"\s+[|\-\u2013\u2014·•]\s+|\s*[|•·]\s*|:\s+")
_GENERIC = frozenset("""
home page inicio bienvenidos welcome official sitio site website web company companies co inc llc ltd corp sa de cv
srl sl sas ltda gmbh group grupo the and of in for best top leading toys toy juguetes juguete importer importers
importador importadora distributor distribuidor distribuidora wholesale mayorista mayoreo store tienda shop online
contact contacto about nosotros empresa productos products
""".split())


def _stem(domain: str) -> str:
	host = normalize_domain(domain)
	return re.sub(r"[^a-z0-9]", "", host.split(".")[0]) if host else ""


def _fold(text: str) -> str:
	return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def _matches(segment: str, stem: str) -> bool:
	words = re.findall(r"[a-z0-9]+", _fold(segment))
	if any(len(w) >= 3 and w not in _GENERIC and w in stem for w in words):
		return True
	initials = "".join(w[0] for w in words if w not in ("of", "the", "and", "de", "y", "la", "el"))
	return len(initials) >= 3 and stem.startswith(initials)


def name_from_domain(domain: str) -> str:
	stem = normalize_domain(domain).split(".")[0]
	return " ".join(w.upper() if len(w) <= 3 and not re.search(r"[aeiouy]", w) else w.capitalize()
		for w in re.split(r"[-_]+", stem) if w)


def business_name(title: str, domain: str) -> str:
	stem = _stem(domain)
	title = re.sub(r"\s*(\.\.\.|…)\s*$", "", (title or "").strip())
	if stem and title:
		segments = [s.strip(" ,.-") for s in _SEPARATORS.split(title)]
		hits = [s for s in segments if s and len(s) <= 60 and _matches(s, stem)]
		if hits:
			return hits[0]
	return name_from_domain(domain) if stem else title


# ── queries in the market's language ─────────────────────────────────────────

COUNTRY_LANG = {
	"es": ("Mexico", "Spain", "Argentina", "Chile", "Colombia", "Peru", "Ecuador", "Guatemala", "Costa Rica", "Panama",
		"Uruguay", "Paraguay", "Bolivia, Plurinational State of", "Venezuela, Bolivarian Republic of",
		"Dominican Republic", "Honduras", "El Salvador", "Nicaragua", "Cuba", "Puerto Rico"),
	"pt": ("Brazil", "Portugal", "Angola", "Mozambique"), "fr": ("France", "Belgium", "Morocco", "Senegal", "Ivory Coast"),
	"de": ("Germany", "Austria", "Switzerland"), "it": ("Italy",), "nl": ("Netherlands",)}
LANG_OF = {c: lang for lang, cs in COUNTRY_LANG.items() for c in cs}
LOCAL_NAMES = {"Mexico": "México", "Spain": "España", "Peru": "Perú", "Panama": "Panamá", "Brazil": "Brasil",
	"Dominican Republic": "República Dominicana", "Bolivia, Plurinational State of": "Bolivia",
	"Venezuela, Bolivarian Republic of": "Venezuela", "Germany": "Deutschland", "Austria": "Österreich",
	"Switzerland": "Schweiz", "Italy": "Italia", "Netherlands": "Nederland", "Belgium": "Belgique", "Morocco": "Maroc",
	"Korea, Republic of": "South Korea", "Iran, Islamic Republic of": "Iran", "Russian Federation": "Russia"}
ROLES = {
	"en": {"importer": "importer", "distributor": "distributor", "wholesaler": "wholesaler", "retailer": "store"},
	"es": {"importer": "importador", "distributor": "distribuidor", "wholesaler": "mayorista", "retailer": "tienda"},
	"pt": {"importer": "importador", "distributor": "distribuidor", "wholesaler": "atacadista", "retailer": "loja"},
	"fr": {"importer": "importateur", "distributor": "distributeur", "wholesaler": "grossiste", "retailer": "magasin"},
	"de": {"importer": "Importeur", "distributor": "Großhändler", "wholesaler": "Großhandel", "retailer": "Geschäft"},
	"it": {"importer": "importatore", "distributor": "distributore", "wholesaler": "grossista", "retailer": "negozio"},
	"nl": {"importer": "importeur", "distributor": "distributeur", "wholesaler": "groothandel", "retailer": "winkel"},
}
# A few trade words in the market's language; anything else is searched as written.
PRODUCTS = {
	"toys": {"es": "juguetes", "pt": "brinquedos", "fr": "jouets", "de": "Spielzeug", "it": "giocattoli",
		"nl": "speelgoed"},
	"water guns": {"es": "pistolas de agua", "pt": "pistolas de água", "fr": "pistolets à eau", "de": "Wasserpistolen",
		"it": "pistole ad acqua", "nl": "waterpistolen"},
	"party supplies": {"es": "artículos para fiestas", "pt": "artigos para festas", "fr": "articles de fête",
		"de": "Partyartikel", "it": "articoli per feste", "nl": "feestartikelen"},
	"gifts": {"es": "artículos de regalo", "pt": "presentes", "fr": "articles cadeaux", "de": "Geschenkartikel",
		"it": "articoli da regalo", "nl": "geschenkartikelen"},
	"stationery": {"es": "papelería", "pt": "papelaria", "fr": "papeterie", "de": "Schreibwaren",
		"it": "cartoleria", "nl": "kantoorartikelen"},
	"beach toys": {"es": "juguetes de playa", "pt": "brinquedos de praia", "fr": "jouets de plage",
		"de": "Strandspielzeug", "it": "giochi da spiaggia", "nl": "strandspeelgoed"},
	"seasonal products": {"es": "productos de temporada", "pt": "produtos sazonais", "fr": "produits saisonniers",
		"de": "Saisonartikel", "it": "prodotti stagionali", "nl": "seizoensartikelen"},
}


def _words(query: dict) -> list[str]:
	words = [w.strip() for w in (query.get("keywords") or []) if (w or "").strip()]
	words += [w.strip() for w in (query.get("category") or "").split(",") if w.strip() and w.strip() not in words]
	return list(dict.fromkeys(words))


def language_for(country: str, languages: list[str] | None) -> str:
	codes = [(x or "")[:2].lower() for x in languages or [] if x]
	own = LANG_OF.get(country, "en")
	return own if not codes or own in codes else codes[0]


def product_word(word: str, lang: str) -> str:
	return PRODUCTS.get(word.strip().lower(), {}).get(lang, word)


def phrase(word: str, role: str, country: str, lang: str) -> str:
	""""importador de juguetes México", "Spielzeug Großhändler Deutschland", "toys importer India"."""
	local, product = LOCAL_NAMES.get(country, country), product_word(word, lang)
	role = ROLES.get(lang, ROLES["en"]).get(role, role)
	if lang in ("es", "pt", "fr"):
		return f"{role} de {product} {local}"
	if lang == "it":
		return f"{role} di {product} {local}"
	return f"{product} {role} {local}"


def queries(query: dict, max_queries: int = MAX_QUERIES) -> list[dict]:
	"""keywords x countries x buyer roles, interleaved so the first few cover every country and
	keyword with the first role. Each: {q, country, lang, gl, keyword}."""
	words = _words(query)
	countries = [c for c in query.get("countries") or [] if c] or [None]
	roles = [r.lower() for r in query.get("buyer_types") or [] if r] or ["importer", "distributor"]
	out = []
	for role in roles:
		for word in words:
			for country in countries:
				first = ((query.get("languages") or ["en"])[0] or "en")[:2]
				lang = language_for(country, query.get("languages")) if country else first
				q = phrase(word, role, country or "", lang).strip()
				if query.get("city"):
					q = f"{q} {query['city']}".strip()
				out.append({"q": q, "country": country, "lang": lang, "gl": _gl(country), "keyword": word})
	return list({q["q"]: q for q in out}.values())[:max_queries]


def _gl(country: str | None) -> str:
	if not country:
		return ""
	return (frappe.db.get_value("Country", country, "code") or "").lower()


def _carries(text: str, query_row: dict) -> str | None:
	word = query_row.get("keyword") or ""
	low = _fold(text)
	for w in {word, product_word(word, query_row.get("lang") or "en")}:
		if w and _fold(w) in low:
			return word
	return None


def estimate(key: str, query: dict) -> float:
	"""Projected cost of one search(query) with this source, in USD, for use(..., cost=)."""
	n = len(queries(query))
	per = {"serper": 0.001, "scavio": 0.01, "dataforseo": 0.002, "tinyfish": 0.0, "osm": 0.0}.get(key, 0.0)
	extra = n * per if key in ("scavio", "dataforseo") and query.get("city") else 0.0  # their maps call
	return round(n * per + extra, 4)


def _organic(rows: list[dict], query_row: dict, source: str, seen: set, *, title="title", link="link",
		snippet="snippet") -> list[dict]:
	"""Search results → companies: junk dropped, one per domain, buyer type from title and snippet."""
	out = []
	for item in rows or []:
		if not isinstance(item, dict):
			continue
		url = str(item.get(link) or "")
		domain = normalize_domain(url)
		name = business_name(str(item.get(title) or ""), domain)
		if not domain or domain in seen or is_junk(domain, str(item.get(title) or "")):
			continue
		seen.add(domain)
		text = f"{item.get(title) or ''} {item.get(snippet) or ''}"
		out.append(company(name, source, website=url, domain=domain, country=query_row.get("country"),
			source_url=url, buyer_type=buyer_type_of(text), signals={"CARRIES_CATEGORY": _carries(text, query_row)}))
	return out


def _fake_companies(query: dict, source: str, prefix: str) -> list[dict]:
	"""Realistic canned results for tests and demos: two buyers per query country, plus the shape
	each source gives (an OSM id, a maps cid …)."""
	out = []
	for i, country in enumerate([c for c in query.get("countries") or [] if c] or ["Mexico"]):
		lang = language_for(country, query.get("languages"))
		word = product_word((_words(query) or ["toys"])[0], lang)
		tld = _gl(country) or "com"
		for j, (kind, bt) in enumerate((("importadora", "Importer"), ("mayorista", "Wholesaler"))):
			domain = f"{prefix}-{kind}-{i}{j}.{tld}"
			out.append(company(f"{kind.title()} {word.title()} {country} {j + 1}", source, website=f"https://www.{domain}/",
				country=country, city=query.get("city") or None, phone=f"+52 55 5555 {1000 + i * 10 + j}",
				source_url=f"https://www.{domain}/", external_id=f"{source.lower()}:fake/{i}{j}",
				industry=word, buyer_type=bt, signals={"CARRIES_CATEGORY": word}))
	return out[: int(query.get("limit") or 25)]


# ── OpenStreetMap ────────────────────────────────────────────────────────────

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
	"https://overpass.private.coffee/api/interpreter")
# (words in the category or keywords, OSM tags, the label recorded as CARRIES_CATEGORY). No catch-all.
TAG_MAP = (
	(("toy", "juguete", "brinquedo", "jouet", "spielzeug", "giocattol", "water gun", "pistola"), ("shop=toys",), "toys"),
	(("wholesale", "mayorista", "mayoreo", "atacad", "grossist", "großhandel", "ingrosso"),
		("shop=wholesale",), "wholesale"),
	(("gift", "regalo", "presente", "cadeau", "geschenk"), ("shop=gift",), "gifts"),
	(("party", "fiesta", "festa", "fête", "carnival", "holi", "costume", "disfraz"),
		("shop=party", "shop=costume"), "party"),
	(("stationery", "papeler", "papelaria", "papeterie", "schreibwaren", "cartoler"), ("shop=stationery",), "stationery"),
	(("baby", "bebé", "bebe"), ("shop=baby_goods",), "baby goods"),
	(("sport", "deporte", "esporte"), ("shop=sports",), "sports"),
	(("hobby", "craft", "manualidad", "model"), ("shop=hobby", "shop=craft"), "hobby"),
	(("variety", "dollar", "bazar", "99"), ("shop=variety_store",), "variety store"),
	(("department store", "departamental"), ("shop=department_store",), "department store"),
	(("beach", "playa", "praia", "pool", "piscina"), ("shop=swimming_pool", "shop=water_sports"), "beach and pool"),
	(("garden", "jardín", "jardin"), ("shop=garden_centre",), "garden"),
	(("electronic", "electrónic"), ("shop=electronics",), "electronics"),
	(("cosmetic", "cosmétic", "beauty", "belleza"), ("shop=cosmetics", "shop=beauty"), "cosmetics"),
	(("book", "libro", "librería"), ("shop=books",), "books"),
	(("importer", "importador", "import"), ("office=import_export", "office=company][company=import_export"), "import"),
)


def osm_tags(query: dict) -> list[tuple[str, str]]:
	"""[(tag, label)] for the trades the query names."""
	text = _fold(" ".join(_words(query)))
	out = []
	for needles, tags, label in TAG_MAP:
		if any(_fold(n) in text for n in needles):
			out += [(t, label) for t in tags if (t, label) not in out]
	return out


def geocode(place: str) -> dict | None:
	"""Nominatim, one place at a time and at most one request a second, as its usage policy asks.
	Kept 30 days in the cache, so a city is looked up once a month at most."""
	key = f"excom_ai_geo:{_fold(place)}"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return cached or None
	_pace("nominatim", 1.1)
	r = requests.get(NOMINATIM, params={"q": place, "format": "jsonv2", "limit": 1}, timeout=TIMEOUT,
		headers={"User-Agent": user_agent()})
	r.raise_for_status()
	hits = r.json()
	point = {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
		"name": hits[0].get("display_name")} if hits else {}
	frappe.cache.set_value(key, point, expires_in_sec=30 * 86400)
	return point or None


def overpass(body: str) -> list[dict]:
	"""Overpass is volunteer infrastructure: one query at a time, the mirrors in turn when one is busy,
	and an error (never an empty list) when all are, so a throttled run does not read as no buyers."""
	r = None
	for endpoint in OVERPASS:
		_pace("overpass", 2.0)
		try:
			r = requests.post(endpoint, data={"data": body}, timeout=(10, 90), headers={"User-Agent": user_agent()})
		except requests.RequestException:
			continue
		if r.status_code not in (429, 503, 504):
			break
	else:
		raise RuntimeError("Every public Overpass server is busy; try again in a few minutes.")
	if r is None:
		raise RuntimeError("No Overpass server answered.")
	r.raise_for_status()
	return r.json().get("elements") or []


def _area(country: str | None, city: str | None) -> str:
	"""The Overpass filter for the place: around a geocoded city, else the whole country by ISO code."""
	if city:
		point = geocode(", ".join(x for x in (city, LOCAL_NAMES.get(country or "", country or "")) if x))
		if not point:
			raise ToolRefused(_("OpenStreetMap does not know the place {0}.").format(city))
		return f"(around:25000,{point['lat']:.5f},{point['lon']:.5f})"
	code = _gl(country).upper()
	if not code:
		raise ToolRefused(_("OpenStreetMap needs a city or a country."))
	return "(area.a)"


def osm_search(query: dict) -> list[dict]:
	tags = osm_tags(query)
	if not tags:
		raise ToolRefused(_("OpenStreetMap has no map tag for {0}. It knows: {1}.").format(
			", ".join(_words(query)) or "-", ", ".join(sorted({t[2] for t in TAG_MAP}))))
	limit = int(query.get("limit") or 25)
	out, seen = [], set()
	for country in [c for c in query.get("countries") or [] if c] or [None]:
		where = _area(country, query.get("city"))
		head = f'area["ISO3166-1"="{_gl(country).upper()}"][admin_level=2]->.a;' if where == "(area.a)" else ""
		clauses = "".join(f"nwr[{tag}]{where};" for tag, _label in tags)
		labels = {tag.split("][")[0]: label for tag, label in tags}
		for el in overpass(f"[out:json][timeout:60];{head}({clauses});out center tags {limit};"):
			t = el.get("tags") or {}
			name = t.get("name") or t.get("brand")
			ident = f"osm:{el.get('type')}/{el.get('id')}"
			if not name or ident in seen:
				continue
			seen.add(ident)
			trade = next((f"{k}={t[k]}" for k in ("shop", "office", "craft") if t.get(k)), "")
			site = t.get("website") or t.get("contact:website") or t.get("url") or ""
			if site and "://" not in site:
				site = "https://" + site
			bt = "Wholesaler" if t.get("shop") == "wholesale" else "Retailer" if t.get("shop") else \
				"Importer" if "import" in f"{trade} {t.get('company') or ''}" else "Other"
			out.append(company(name, "OSM", website=site or None, country=country, city=t.get("addr:city") or query.get("city"),
				phone=t.get("phone") or t.get("contact:phone"),
					source_url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
				external_id=ident, industry=trade or None, buyer_type=bt,
				address=" ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x) or None,
				signals={"CARRIES_CATEGORY": labels.get(trade)}))
			if len(out) >= limit:
				return out
	return out


@tool("osm", tier="read", cost=0.0)
class OpenStreetMap(Tool):
	"""search(query) -> [company]. Free; only the trades in TAG_MAP; needs a city or a country."""

	def search(self, query: dict) -> list[dict]:
		return osm_search(query)

	def fake_search(self, query: dict) -> list[dict]:
		rows = _fake_companies(query, "OSM", "osm")
		for i, c in enumerate(rows):
			c.update({"external_id": f"osm:node/{1000 + i}", "source_url": f"https://www.openstreetmap.org/node/{1000 + i}",
				"industry": "shop=toys", "buyer_type": "Retailer"})
			c["signals"].update({"BUYER_TYPE": "Retailer", "CARRIES_CATEGORY": "toys"})
		return rows


# ── Serper (Google organic) ──────────────────────────────────────────────────


@tool("serper", tier="paid", cost=0.001, needs_key=True)
class Serper(Tool):
	"""search(query) -> [company]. Google organic results, one request per query phrase; at most ten
	results each (Serper's free accounts refuse more). Pass cost=estimate("serper", query)."""

	def search(self, query: dict) -> list[dict]:
		key, out, seen = self.secret(), [], set()
		limit = int(query.get("limit") or 25)
		for row in queries(query):
			body = {"q": row["q"], "num": 10}
			if row["gl"]:
				body["gl"] = row["gl"]
			if row["lang"]:
				body["hl"] = row["lang"]
			r = requests.post("https://google.serper.dev/search", json=body, timeout=TIMEOUT,
				headers={"X-API-KEY": key, "Content-Type": "application/json"})
			if r.status_code in (401, 403):
				raise RuntimeError("Serper refused the key.")
			r.raise_for_status()
			out += _organic(r.json().get("organic") or [], row, "Serper", seen)
			if len(out) >= limit:
				break
		return out[:limit]

	def fake_search(self, query: dict) -> list[dict]:
		return _fake_companies(query, "Serper", "serper")


# ── Scavio (Google search and Maps) ──────────────────────────────────────────

SCAVIO = "https://api.scavio.dev/api/v2/google"


@tool("scavio", tier="paid", cost=0.01, needs_key=True)
class Scavio(Tool):
	"""search(query) -> [company]. Google organic results through Scavio (one credit a page), and its
	Google Maps listings too when the query names a city. Scavio searches google.com in English unless
	the phrase names the place, so the country goes into the phrase."""

	def _post(self, path: str, body: dict) -> dict:
		_pace("scavio", 1.05)
		r = requests.post(f"{SCAVIO}{path}", json=body, timeout=TIMEOUT,
			headers={"Authorization": f"Bearer {self.secret()}", "Content-Type": "application/json"})
		reasons = {401: "invalid API key", 402: "no credits left", 403: "access refused", 429: "rate limit reached"}
		if r.status_code >= 400:
			raise RuntimeError(f"Scavio {r.status_code}: {reasons.get(r.status_code, 'request failed')}")
		data = r.json()
		if not isinstance(data, dict) or data.get("error"):
			raise RuntimeError("Scavio: an unexpected answer")
		return data

	def search(self, query: dict) -> list[dict]:
		out, seen, limit = [], set(), int(query.get("limit") or 25)
		for row in queries(query):
			out += _organic(self._post("", {"query": row["q"]}).get("organic_results") or [], row, "Scavio", seen)
			if query.get("city") and len(out) < limit:
				out += self._maps(row, query, seen)
			if len(out) >= limit:
				break
		return out[:limit]

	def _maps(self, row: dict, query: dict, seen: set) -> list[dict]:
		out = []
		for item in self._post("/maps/search", {"query": row["q"]}).get("local_results") or []:
			if not isinstance(item, dict) or not item.get("title"):
				continue
			site, place = item.get("website") or "", item.get("place_id") or ""
			domain = normalize_domain(site)
			if (domain and (domain in seen or is_junk(domain))) or (place and place in seen):
				continue
			seen.add(domain or place)
			out.append(company(item["title"], "Scavio", website=site or None, country=row.get("country"), city=query.get("city"),
				phone=item.get("phone"), address=item.get("address"), industry=item.get("type"),
				external_id=f"gmaps:{place}" if place else None,
				source_url=f"https://www.google.com/maps/place/?q=place_id:{place}" if place else site,
				buyer_type=buyer_type_of(f"{item['title']} {item.get('type') or ''}"),
				signals={"CARRIES_CATEGORY": _carries(f"{item['title']} {item.get('type') or ''}", row)}))
		return out

	def fake_search(self, query: dict) -> list[dict]:
		return _fake_companies(query, "Scavio", "scavio")


# ── DataForSEO (Google organic and Maps) ─────────────────────────────────────

DATAFORSEO = "https://api.dataforseo.com/v3"


def dfs_items(payload: dict) -> list[dict]:
	"""The items of a DataForSEO answer, with its error surfaced (40102 'no results' is an answer)."""
	if payload.get("status_code") not in (20000, None):
		raise RuntimeError(f"DataForSEO {payload.get('status_code')}: {payload.get('status_message')}")
	task = (payload.get("tasks") or [{}])[0] or {}
	if task.get("status_code") == 40102:
		return []
	if task.get("status_code") not in (20000, None):
		raise RuntimeError(f"DataForSEO task {task.get('status_code')}: {task.get('status_message')}")
	result = (task.get("result") or [None])[0]
	return (result or {}).get("items") or []


@tool("dataforseo", tier="paid", cost=0.002, needs_key=True)
class DataForSEO(Tool):
	"""search(query) -> [company]. Google organic (and Google Maps when the query names a city) through
	DataForSEO's live endpoints. The login is the tool's Account ID and the password its API key (or
	the API key as "login:password")."""

	def _auth(self) -> str:
		login = (self.record and self.record.get("account_id")) or ""
		secret = self.secret()
		if not login and ":" in secret:
			login, secret = secret.split(":", 1)
		return base64.b64encode(f"{login}:{secret}".encode()).decode()

	def _post(self, endpoint: str, task: dict) -> list[dict]:
		r = requests.post(f"{DATAFORSEO}/{endpoint}", json=[task], timeout=(10, 90),
			headers={"Authorization": f"Basic {self._auth()}", "Content-Type": "application/json"})
		if r.status_code in (401, 402, 403):
			raise RuntimeError(f"DataForSEO refused the call ({r.status_code}).")
		r.raise_for_status()
		return dfs_items(r.json())

	def search(self, query: dict) -> list[dict]:
		out, seen, limit = [], set(), int(query.get("limit") or 25)
		for row in queries(query):
			task = {"keyword": row["q"], "language_code": row["lang"], "depth": 20}
			if row.get("country"):
				task["location_name"] = row["country"].split(",")[0]
			items = self._post("serp/google/organic/live/advanced", task)
			out += _organic([i for i in items if i.get("type") == "organic"], row, "DataForSEO", seen, link="url",
				snippet="description")
			if query.get("city") and len(out) < limit:
				out += self._maps(self._post("serp/google/maps/live/advanced", task), row, query, seen)
			if len(out) >= limit:
				break
		return out[:limit]

	def _maps(self, items: list[dict], row: dict, query: dict, seen: set) -> list[dict]:
		out = []
		for item in items:
			if item.get("type") != "maps_search" or not item.get("title"):
				continue
			url = item.get("url") or ""
			domain = normalize_domain(item.get("domain") or url)
			ident = f"dataforseo:cid:{item['cid']}" if item.get("cid") else \
				(f"dataforseo:{item['place_id']}" if item.get("place_id") else "")
			if (domain and (domain in seen or is_junk(domain))) or (ident and ident in seen):
				continue
			seen.add(domain or ident)
			addr = item.get("address_info") or {}
			text = f"{item['title']} {item.get('category') or ''}"
			out.append(company(item["title"], "DataForSEO", website=url or None, domain=domain, country=row.get("country"),
				city=addr.get("city") or query.get("city"), phone=item.get("phone"), address=item.get("address"),
				industry=item.get("category"), external_id=ident or None, source_url=url or None,
				buyer_type=buyer_type_of(text), signals={"CARRIES_CATEGORY": _carries(text, row)}))
		return out

	def fake_search(self, query: dict) -> list[dict]:
		rows = _fake_companies(query, "DataForSEO", "dfs")
		for i, c in enumerate(rows):
			c["external_id"] = f"dataforseo:cid:{9000000 + i}"
		return rows


# ── TinyFish (web search, free with a key) ───────────────────────────────────

TINYFISH = "https://api.search.tinyfish.ai"


@tool("tinyfish", tier="read", cost=0.0, needs_key=True)
class TinyFish(Tool):
	"""search(query) -> [company]. One page (ten results) per phrase, paced to TinyFish's 30 searches a
	minute. Its ordering is its own, so no rank is recorded."""

	def search(self, query: dict) -> list[dict]:
		out, seen, limit = [], set(), int(query.get("limit") or 25)
		for row in queries(query):
			_pace("tinyfish", 2.1)
			r = requests.get(TINYFISH, params={"query": row["q"], "page": 0}, timeout=TIMEOUT,
				headers={"X-API-Key": self.secret(), "Accept": "application/json"})
			if r.status_code in (401, 402, 429):
				raise RuntimeError({401: "TinyFish refused the key.", 402: "TinyFish refused the search (402).",
					429: "TinyFish's rate limit is reached."}[r.status_code])
			r.raise_for_status()
			data = r.json()
			out += _organic((data.get("results") if isinstance(data, dict) else None) or [], row, "TinyFish", seen,
				link="url", snippet="snippet")
			if len(out) >= limit:
				break
		return out[:limit]

	def fake_search(self, query: dict) -> list[dict]:
		return _fake_companies(query, "TinyFish", "tinyfish")


# ── Volza and other imports ──────────────────────────────────────────────────

# Column names as they come (lower-cased, accents folded, punctuation to spaces) → our field.
COLUMNS = {
	"name": ("importer", "importer name", "consignee", "consignee name", "buyer", "buyer name", "company", "company name",
		"name", "importador", "nombre", "razon social", "empresa", "exhibitor", "exhibitor name", "organisation",
		"organization", "importer name english"),
	"country": ("importer country", "country", "destination country", "country of destination", "pais",
		"pais destino", "buyer country", "consignee country", "country of import"),
	"city": ("city", "importer city", "ciudad", "consignee city", "town", "buyer city"),
	"product": ("product description", "product", "description", "goods description", "item description", "producto",
		"descripcion", "product details", "commodity", "products"),
	"hs": ("hs code", "hs", "hscode", "h s code", "tariff code", "hs code 8 digit", "hs code 6 digit", "partida",
		"fraccion arancelaria"),
	"quantity": ("quantity", "qty", "std quantity", "cantidad", "std qty", "total quantity"),
	"value": ("value", "total value usd", "value usd", "value in usd", "fob value usd", "fob value", "cif value usd",
		"cif value", "total value", "valor", "total value in usd", "item value usd", "value fob usd", "value cif usd"),
	"date": ("date", "shipment date", "arrival date", "bill of entry date", "fecha", "import date", "be date"),
	"website": ("website", "web", "url", "sitio web", "site", "homepage", "web site"),
	"domain": ("domain", "dominio"),
	"email": ("email", "e mail", "correo", "mail", "contact email", "email address"),
	"phone": ("phone", "telephone", "tel", "telefono", "phone number", "mobile", "contact phone"),
	"industry": ("industry", "sector", "categoria", "category"),
	"buyer_type": ("buyer type", "business type", "tipo", "type"),
}
_ALIAS = {alias: field for field, aliases in COLUMNS.items() for alias in aliases}
FREEMAIL = ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "live.com", "icloud.com", "aol.com", "hotmail.es",
	"prodigy.net.mx", "yahoo.com.mx", "yahoo.es", "terra.com", "uol.com.br", "bol.com.br", "msn.com", "gmx.de", "web.de")
MAX_ROWS = 100_000


def _key(header) -> str:
	return re.sub(r"[^a-z0-9]+", " ", _fold(str(header or ""))).strip()


def normalise_row(row: dict) -> dict:
	"""A row with our field names; unknown columns are dropped, the first non-empty value wins."""
	out = {}
	for k, v in (row or {}).items():
		field = _ALIAS.get(_key(k)) or (k if k in COLUMNS else None)
		if field and v not in (None, "") and field not in out:
			out[field] = v.strip() if isinstance(v, str) else v
	return out


def _number(v) -> float:
	if isinstance(v, (int, float)):
		return float(v)
	s = re.sub(r"[^\d.,\-]", "", str(v or ""))
	if s.count(",") and s.count("."):
		s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
	elif s.count(",") == 1 and len(s.split(",")[1]) != 3:
		s = s.replace(",", ".")
	else:
		s = s.replace(",", "")
	return flt(s)


def _date(v) -> str | None:
	try:
		return str(getdate(v)) if v not in (None, "") else None
	except Exception:
		return None


def frappe_country(value) -> str | None:
	"""A country as the Frappe Country name, from a name, a local name or an ISO code."""
	v = str(value or "").strip()
	if not v:
		return None
	if frappe.db.exists("Country", v):
		return frappe.db.get_value("Country", v, "name")
	if len(v) == 2:
		return frappe.db.get_value("Country", {"code": v.lower()}, "name")
	local = {_fold(b): a for a, b in LOCAL_NAMES.items()}
	if local.get(_fold(v)):
		return local[_fold(v)]
	return frappe.db.get_value("Country", {"name": ["like", f"{v}%"]}, "name") if len(v) >= 4 else None


def import_rows(rows: list[dict], source: str = "Import") -> list[dict]:
	"""Rows from any CSV, spreadsheet or JSON list → company dicts, one per company.

	Rows are grouped by domain (from a website, else a non-freemail address), else by name and
	country. Customs columns (product, HS code, value, quantity, date) are summed per importer and
	become IMPORTS_CATEGORY ("HS 950300: water guns …") and SIZE_HINT; such a company is an Importer."""
	groups: dict = {}
	for raw in (rows or [])[:MAX_ROWS]:
		r = normalise_row(raw)
		name = str(r.get("name") or "").strip()
		if not name:
			continue
		country = frappe_country(r.get("country"))
		email = str(r.get("email") or "").strip().lower()
		mail_domain = email.rpartition("@")[2] if "@" in email else ""
		domain = normalize_domain(str(r.get("website") or r.get("domain") or "")) or \
			(mail_domain if mail_domain and mail_domain not in FREEMAIL else "")
		key = domain or (re.sub(r"[^a-z0-9]+", "", _fold(name)), country)
		g = groups.setdefault(key, {"name": name, "domain": domain, "website": r.get("website"), "country": country,
			"city": r.get("city"), "phone": r.get("phone"), "industry": r.get("industry"), "buyer_type": r.get("buyer_type"),
			"emails": [], "shipments": 0, "quantity": 0.0, "value": 0.0, "hs": Counter(), "products": Counter(),
			"first": None, "last": None})
		if email and email not in g["emails"]:
			g["emails"].append(email)
		for f in ("website", "city", "phone", "industry", "country"):
			g[f] = g[f] or (country if f == "country" else r.get(f))
		if any(r.get(f) not in (None, "") for f in ("product", "hs", "value", "quantity")):
			g["shipments"] += 1
			g["quantity"] += _number(r.get("quantity"))
			g["value"] += _number(r.get("value"))
			hs = re.sub(r"\D", "", str(r.get("hs") or ""))
			product = re.sub(r"\s+", " ", str(r.get("product") or "")).strip()[:80]
			if hs or product:
				g["hs"][hs[:6]] += 1
				g["products"][(hs[:6], product.lower())] += 1
			d = _date(r.get("date"))
			if d:
				g["first"] = min(g["first"] or d, d)
				g["last"] = max(g["last"] or d, d)
	out = []
	for g in groups.values():
		signals, summary, bt = {}, None, g["buyer_type"] if g["buyer_type"] in BUYER_TYPES else None
		if g["shipments"]:
			seen_hs, parts = set(), []
			for (hs, product), _n in g["products"].most_common():
				if hs in seen_hs and len(parts) >= 1:
					continue
				seen_hs.add(hs)
				parts.append(f"HS {hs}: {product}" if hs and product else (f"HS {hs}" if hs else product))
				if len(parts) == 3:
					break
			signals["IMPORTS_CATEGORY"] = "; ".join(parts)[:140]
			size = f"{g['shipments']} shipments" + (f", USD {g['value']:,.0f}" if g["value"] else "")
			signals["SIZE_HINT"] = size + (f" ({g['first']} to {g['last']})" if g["first"] else "")
			summary = {"shipments": g["shipments"], "quantity": g["quantity"], "value_usd": g["value"],
				"hs_codes": [h for h, _n in g["hs"].most_common(5) if h], "first_date": g["first"], "last_date": g["last"]}
			bt = "Importer"
		website = g["website"] or (f"https://{g['domain']}" if g["domain"] else None)
		if website and "://" not in str(website):
			website = f"https://{website}"
		slug = re.sub(r"[^a-z0-9]+", "-", _fold(g["name"])).strip("-")[:60]
		out.append(company(g["name"], source, website=website, domain=g["domain"] or None, country=g["country"],
			city=g["city"], phone=g["phone"], industry=g["industry"], buyer_type=bt or buyer_type_of(g["name"]),
			external_id=f"{source.lower()}:{slug}:{_gl(g['country']) or 'xx'}", emails=g["emails"], import_summary=summary,
			signals=signals))
	return out


def read_table(file_url: str) -> list[dict]:
	"""The rows of an uploaded CSV or XLSX File, as dicts keyed by the header row (found among the
	first 15 rows: Volza exports may have a title above it)."""
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		raise ToolRefused(_("No uploaded file at {0}.").format(file_url))
	f = frappe.get_doc("File", name)
	content = f.get_content()
	ext = (f.file_name or file_url).rsplit(".", 1)[-1].lower()
	if ext in ("xlsx", "xlsm"):
		from openpyxl import load_workbook

		wb = load_workbook(io.BytesIO(content if isinstance(content, bytes) else content.encode()), read_only=True,
			data_only=True)
		grid = [list(r) for _i, r in zip(range(MAX_ROWS + 15), wb.active.iter_rows(values_only=True), strict=False)]
		wb.close()
	elif ext in ("csv", "txt", "tsv"):
		text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
		try:
			dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
		except csv.Error:
			dialect = csv.excel
		grid = list(csv.reader(io.StringIO(text), dialect))[: MAX_ROWS + 15]
	else:
		raise ToolRefused(_("Upload the export as CSV or XLSX (not {0}).").format(ext))
	head = next((i for i, row in enumerate(grid[:15]) if sum(1 for c in row if _ALIAS.get(_key(c))) >= 2), None)
	if head is None:
		raise ToolRefused(_("No header row with an importer or company name was found."))
	headers = [str(c or "").strip() for c in grid[head]]
	return [dict(zip(headers, row, strict=False)) for row in grid[head + 1:] if any(c not in (None, "") for c in row)]


@tool("volza", tier="read", cost=0.0)
class Volza(Tool):
	"""import_file(file_url, source="Volza") -> [company] from a Volza export (CSV or XLSX) uploaded as a
	File; import_rows(rows, source="Import") -> [company] from any list of dicts (a fair list, a
	directory). Both read only local data, so their fakes are the real thing."""

	def import_file(self, file_url: str, source: str = "Volza") -> list[dict]:
		return import_rows(read_table(file_url), source)

	def import_rows(self, rows: list[dict], source: str = "Import") -> list[dict]:
		return import_rows(rows, source)

	fake_import_file = import_file
	fake_import_rows = import_rows
