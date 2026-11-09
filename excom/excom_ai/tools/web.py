# Parts of this file are ported from Harvey (github.com/ethanplusai/harvey): the public-website boundary
# (harvey/public_http.py) and the published-address extraction (harvey/collectors/people.py).
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
"""The website profiler (HLD-007 §9): what a company's own site says about it, with no AI and no cost.

A profile reads the homepage and up to five likely pages (contact, about, team, imprint, and their
Spanish and Portuguese names), found from the homepage's links, then sitemap.xml, then a few guessed
paths. robots.txt is read first and its Disallow rules are kept for every page.

Every URL is fetched behind an SSRF guard, because the URL comes from a search result or a record
anyone could have typed:
- only http and https, only ports 80 and 443, no user:password in the URL;
- the host is resolved and every address must be public (no private, loopback, link-local,
  multicast, reserved or metadata address, IPv4 or IPv6, nor IPv6 forms that carry an IPv4 one);
- the connection is made to the address that was checked, so DNS cannot answer differently the
  second time (rebinding); TLS still checks the certificate against the host name;
- redirects are followed by hand, at most three, and each one is checked again;
- each response is capped in size and in time.

What comes back is untrusted text (§12.7): data for records and prompts, never instructions.
"""

from __future__ import annotations

import codecs
import html as html_lib
import ipaddress
import re
import socket
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import frappe
import requests
from frappe import _
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError
from urllib3.util.connection import create_connection

from excom.excom_ai.tools import Tool, ToolRefused, tool

ROBOTS_AGENT = "ExcomAI"
PORTS = {"http": 80, "https": 443}
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
TIMEOUT = (5, 10)  # connect, read (per socket operation)
FETCH_SECONDS = 15  # the whole of one response, however slowly it trickles in
PROFILE_SECONDS = 60
MAX_PAGES = 6  # the homepage and five more
TEXT_CAP = 6000
PAGE_TEXT_CAP = 400
SITEMAP_LOCS = 500


class UnsafeURL(ToolRefused):
	"""The URL is outside the public-website boundary. A ToolRefused, so a caller skips the company
	rather than recording a tool failure."""


def user_agent() -> str:
	"""Names the app and the site running it, so a site owner can tell who called (Nominatim and
	Overpass ask for this too). Never a person's address."""
	try:
		site = frappe.utils.get_url()
	except Exception:
		site = ""
	return f"{ROBOTS_AGENT}/1.0 (Excom business research for a Frappe ERP{'; +' + site if site else ''})"


# ── the boundary ─────────────────────────────────────────────────────────────

_TRANSLATION_NETS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def public_ip(value: str) -> str:
	"""The address as text when it is a public unicast one, else UnsafeURL.

	is_global already excludes private, loopback, link-local (169.254.169.254, the cloud metadata
	address, is link-local), shared (100.64/10) and documentation ranges. IPv6 forms that carry an
	IPv4 address (mapped, 6to4, Teredo, NAT64) are refused outright: the IPv4 inside could be anything.
	"""
	try:
		if "%" in value:
			raise ValueError("scoped address")
		ip = ipaddress.ip_address(value)
		if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback:
			raise ValueError("not public")
		if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip.is_site_local
				or any(ip in net for net in _TRANSLATION_NETS)):
			raise ValueError("IPv6 carrying IPv4")
	except ValueError:
		raise UnsafeURL(_("That address is not a public website.")) from None
	return str(ip)


def _resolve(host: str, port: int) -> list[str]:
	"""Every address the host resolves to (a seam for tests)."""
	return list(dict.fromkeys(a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))


def public_addresses(host: str, port: int) -> list[str]:
	"""The host's addresses, all of them public, IPv4 first; UnsafeURL if any one is not.

	One bad answer refuses the host: a name that resolves to a public and a private address is
	exactly what a rebinding attack looks like."""
	host = (host or "").strip("[]").rstrip(".").lower()
	if not host or host == "localhost" or host.endswith(".localhost"):
		raise UnsafeURL(_("That address is not a public website."))
	try:
		ipaddress.ip_address(host)
		return [public_ip(host)]
	except ValueError:
		pass
	try:
		answers = _resolve(host, port)
	except OSError:
		raise UnsafeURL(_("{0} does not resolve.").format(host)) from None
	if not answers:
		raise UnsafeURL(_("{0} does not resolve.").format(host))
	return sorted((public_ip(a) for a in answers), key=lambda a: ipaddress.ip_address(a).version)


def check_url(url: str) -> str:
	"""The URL, if it may be fetched: http(s), port 80 or 443, no credentials, a public host."""
	try:
		parts = urlsplit((url or "").strip())
		port = parts.port
	except ValueError:
		raise UnsafeURL(_("That is not a website address.")) from None
	if parts.scheme not in PORTS:
		raise UnsafeURL(_("Only http and https websites can be read."))
	if parts.username or parts.password or not parts.hostname:
		raise UnsafeURL(_("That is not a website address."))
	if (port or PORTS[parts.scheme]) not in PORTS.values():
		raise UnsafeURL(_("Only ports 80 and 443 can be read."))
	public_addresses(parts.hostname, port or PORTS[parts.scheme])
	return parts.geturl() if parts.path else parts._replace(path="/").geturl()


class _Guarded:
	"""Connects to an address checked at connect time, not to whatever DNS says a moment later."""

	def _new_conn(self):
		if self.port not in PORTS.values():
			raise UnsafeURL(_("Only ports 80 and 443 can be read."))
		last = None
		for ip in public_addresses(self._dns_host, self.port):
			try:
				return create_connection((ip, self.port), self.timeout, source_address=self.source_address,
					socket_options=self.socket_options)
			except OSError as e:
				last = e
		raise NewConnectionError(self, f"Failed to establish a new connection: {last}")


class _HTTPConnection(_Guarded, HTTPConnection):
	pass


class _HTTPSConnection(_Guarded, HTTPSConnection):
	pass


class _HTTPPool(HTTPConnectionPool):
	ConnectionCls = _HTTPConnection


class _HTTPSPool(HTTPSConnectionPool):
	ConnectionCls = _HTTPSConnection


class _GuardedAdapter(HTTPAdapter):
	def init_poolmanager(self, *args, **kwargs):
		super().init_poolmanager(*args, **kwargs)
		self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


def session() -> requests.Session:
	"""A session for public websites: guarded connections, no proxies from the environment (they
	could resolve or reroute the target), and a polite User-Agent."""
	s = requests.Session()
	s.trust_env = False
	adapter = _GuardedAdapter(max_retries=0)
	s.mount("http://", adapter)
	s.mount("https://", adapter)
	s.headers.update({"User-Agent": user_agent(), "Accept-Language": "*",
		"Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,application/xml;q=0.8,*/*;q=0.5"})
	return s


@dataclass
class Page:
	url: str
	status: int
	content_type: str
	text: str

	@property
	def ok(self) -> bool:
		return 200 <= self.status < 300

	@property
	def is_html(self) -> bool:
		return not self.content_type or "html" in self.content_type


def _charset(r, head: bytes) -> str:
	"""The header's charset, else the page's <meta charset>, else UTF-8 (requests would guess Latin-1)."""
	m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type") or "", re.I)
	name = m.group(1) if m else ""
	if not name:
		m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", head[:4096], re.I)
		name = m.group(1).decode("ascii", "ignore") if m else "utf-8"
	try:
		codecs.lookup(name)
	except LookupError:
		name = "utf-8"
	return name


def fetch(url: str, s: requests.Session | None = None, max_bytes: int = MAX_BYTES) -> Page:
	"""GET one URL behind the guard. Redirects are followed by hand (at most MAX_REDIRECTS), each
	target checked again; the body is cut at max_bytes and the whole read at FETCH_SECONDS."""
	s = s or session()
	for _hop in range(MAX_REDIRECTS + 1):
		url = check_url(url)
		started = time.monotonic()
		r = s.get(url, allow_redirects=False, stream=True, timeout=TIMEOUT)
		try:
			if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
				url = urljoin(url, r.headers["Location"].strip())
				continue
			body, size = [], 0
			for chunk in r.iter_content(16384):
				body.append(chunk)
				size += len(chunk)
				if size >= max_bytes:
					break
				if time.monotonic() - started > FETCH_SECONDS:
					raise requests.Timeout(f"{url} took longer than {FETCH_SECONDS}s")
			raw = b"".join(body)[:max_bytes]
			ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
			return Page(url, r.status_code, ctype, raw.decode(_charset(r, raw), errors="replace"))
		finally:
			r.close()
	raise UnsafeURL(_("More than {0} redirects.").format(MAX_REDIRECTS))


# ── reading a page ───────────────────────────────────────────────────────────


def normalize_domain(url_or_domain: str) -> str:
	"""A bare, lowercase, www-less host name: the key companies are matched on."""
	raw = (url_or_domain or "").strip().lower()
	if not raw:
		return ""
	if "://" not in raw:
		raw = "http://" + raw
	try:
		host = (urlsplit(raw).hostname or "").strip(".")
	except ValueError:
		return ""
	return host[4:] if host.startswith("www.") else host


def strip_code(html: str) -> str:
	"""Script, style and comments go before any text regex, or minified JS reads as prose."""
	html = re.sub(r"(?is)<(script|style|noscript|svg|template)\b.*?</\1\s*>", " ", html or "")
	return re.sub(r"(?s)<!--.*?-->", " ", html)


def plain_text(html: str) -> str:
	text = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>", "\n", strip_code(html))
	text = html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
	return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


def _attrs(tag: str) -> dict:
	return {k.lower(): html_lib.unescape(v if v is not None else w) for k, v, w in
		re.findall(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag)}


def _meta(html: str) -> dict:
	out = {}
	for tag in re.findall(r"(?is)<meta\b[^>]*>", html[:200_000]):
		a = _attrs(tag)
		key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
		if key and "content" in a and key not in out:
			out[key] = a["content"].strip()
	return out


def links(html: str, base: str) -> list[tuple[str, str]]:
	"""(absolute URL, anchor text) for every <a href> on the page, in order."""
	out = []
	for m in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a\s*>", strip_code(html)[:1_500_000]):
		href = (_attrs(m.group(1)).get("href") or "").strip()
		if not href or href.startswith(("#", "javascript:", "data:")):
			continue
		anchor = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(2)))).strip()
		out.append((urljoin(base, href), anchor[:80]))
	return out


# Published addresses (ported from Harvey's emails_on_page): mailto links, schema.org, Cloudflare's
# email protection, data-attribute pairs, "name [at] domain [dot] com" and plain text.
EMAIL_RE = re.compile(
	r"(?<![a-z0-9._%+\-])[a-z0-9][a-z0-9._%+\-]{0,63}@"
	r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?){0,6}\.[a-z]{2,24}"
	r"(?![a-z0-9\-])", re.I)
_JUNK_EMAIL_DOMAINS = ("example.com", "example.org", "domain.com", "email.com", "yourdomain.com", "sentry.io",
	"wixpress.com", "sentry-next.wixpress.com", "yoursite.com", "website.com", "company.com", "test.com",
	"dominio.com", "tudominio.com", "empresa.com", "correo.com")
_JUNK_TLDS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")
_AT_DOT = re.compile(
	r"(?<![a-z0-9._%+\-])([a-z0-9][a-z0-9._%+\-]{0,63})\s{0,3}"
	r"(?:\[\s{0,3}(?:at|arroba)\s{0,3}\]|\(\s{0,3}(?:at|arroba)\s{0,3}\)|\{\s{0,3}at\s{0,3}\})\s{0,3}"
	r"([a-z0-9\-]{1,63}(?:\s{0,3}(?:\[\s{0,3}(?:dot|punto)\s{0,3}\]|\(\s{0,3}(?:dot|punto)\s{0,3}\)"
	r"|\{\s{0,3}dot\s{0,3}\}|\.)\s{0,3}[a-z0-9\-]{1,63}){1,4})", re.I)


def _cf_decode(hexstr: str) -> str:
	try:
		key = int(hexstr[:2], 16)
		return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr) - 1, 2))
	except ValueError:
		return ""


def _clean_address(addr: str) -> str:
	addr = unquote(html_lib.unescape(addr or "")).strip().strip(".,;:<>()[]'\"").lower()
	addr = addr.removeprefix("mailto:").split("?")[0].strip()
	if not EMAIL_RE.fullmatch(addr):
		return ""
	domain = addr.rpartition("@")[2]
	if domain in _JUNK_EMAIL_DOMAINS or domain.endswith(_JUNK_TLDS) or re.search(r"@\d+x\.", addr):
		return ""
	return addr


def emails_on_page(html: str) -> list[str]:
	html = (html or "")[:MAX_BYTES]
	found: list[str] = []

	def add(addr):
		addr = _clean_address(addr)
		if addr and addr not in found:
			found.append(addr)

	for m in re.finditer(r"""(?i)href\s*=\s*["']\s*mailto:([^"'>]+)["']""", html):
		for part in m.group(1).split(","):
			add(part)
	for raw in re.findall(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html):
		for m in re.finditer(r'"email"\s*:\s*"([^"]+)"', raw):
			add(m.group(1))
	for hexstr in re.findall(r"""(?i)data-cfemail\s*=\s*["']([0-9a-f]{4,})["']""", html):
		add(_cf_decode(hexstr))
	for hexstr in re.findall(r"(?i)/cdn-cgi/l/email-protection#([0-9a-f]{4,})", html):
		add(_cf_decode(hexstr))
	for tag in re.finditer(r"(?is)<a\b[^>]*\bdata-(?:user|front|mailbox)\s*=[^>]*>", html):
		a = _attrs(tag.group(0))
		local = a.get("data-user") or a.get("data-front") or a.get("data-mailbox") or ""
		domain = a.get("data-domain") or a.get("data-back") or ""
		if re.fullmatch(r"[a-z0-9._%+\-]+", local, re.I) and re.fullmatch(r"[a-z0-9\-]+(?:\.[a-z0-9\-]+)+", domain, re.I):
			add(f"{local}@{domain}")
	text = unquote(html_lib.unescape(strip_code(html)))
	for m in EMAIL_RE.finditer(text):
		add(m.group(0))
	plain = re.sub(r"(?s)<[^>]+>", " ", text)
	for m in _AT_DOT.finditer(plain):
		domain = re.sub(r"(?i)\s*(?:\[\s*(?:dot|punto)\s*\]|\(\s*(?:dot|punto)\s*\)|\{\s*dot\s*\})\s*", ".", m.group(2))
		add(f"{m.group(1)}@{domain.replace(' ', '')}")
	return found


_PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}[\s.\-()]{0,3}\d(?:[\s.\-()]{0,3}\d){6,13}(?!\d)")
_PHONE_LABEL = re.compile(r"(?i)\b(?:tel(?:[eé]fono)?|phone|fone|whats(?:app)?|m[oó]vil|cel(?:ular)?|fax)\b\.?\s*:?\s*"
	r"(\+?\d[\d\s.\-()]{6,20}\d)")


def phones_on_page(html: str, text: str) -> list[str]:
	"""tel: links, international numbers and numbers labelled as a phone. At most five."""
	found, seen = [], set()

	def add(raw):
		raw = re.sub(r"\s+", " ", unquote(raw)).strip(" .-")
		digits = re.sub(r"\D", "", raw)
		if 7 <= len(digits) <= 15 and digits not in seen:
			seen.add(digits)
			found.append(raw)

	for m in re.finditer(r"""(?i)href\s*=\s*["']\s*tel:([^"']+)["']""", html):
		add(m.group(1))
	for m in _PHONE_INTL.finditer(text):
		add(m.group(0))
	for m in _PHONE_LABEL.finditer(text):
		add(m.group(1))
	return found[:5]


_STORE = re.compile(r"(?i)cdn\.shopify\.com|myshopify\.com|woocommerce|wc-cart|add[-_]to[-_]cart|\bmagento\b|prestashop"
	r"|vtex(?:img|commerce|\.com)|tiendanube|nuvemshop|jumpseller|bigcommerce|ecwid|mercadoshops|wix-ecommerce"
	r"|a[ñn]adir al carrito|agregar al carrito|adicionar ao carrinho|ajouter au panier|in den warenkorb"
	r"""|href\s*=\s*["'][^"']*/(?:cart|carrito|carrinho|checkout|panier|warenkorb)(?:[/?"']|\.)""")
_PLATFORMS = (("Shopify", r"cdn\.shopify\.com|myshopify\.com"), ("WooCommerce", r"woocommerce"),
	("Magento", r"\bmagento\b|mage/cookies"), ("PrestaShop", r"prestashop"), ("VTEX", r"vtex"),
	("Tiendanube", r"tiendanube|nuvemshop"), ("Jumpseller", r"jumpseller"), ("BigCommerce", r"bigcommerce"),
	("Wix", r"static\.parastorage\.com|wix-ecommerce"), ("WordPress", r"wp-content/"))
SOCIAL = (("facebook", r"(?:^|\.)facebook\.com$|(?:^|\.)fb\.com$"), ("instagram", r"(?:^|\.)instagram\.com$"),
	("linkedin", r"(?:^|\.)linkedin\.com$"), ("twitter", r"(?:^|\.)(?:twitter|x)\.com$"),
	("youtube", r"(?:^|\.)youtube\.com$|^youtu\.be$"), ("tiktok", r"(?:^|\.)tiktok\.com$"),
	("whatsapp", r"^wa\.me$|(?:^|\.)whatsapp\.com$"), ("pinterest", r"(?:^|\.)pinterest\.[a-z.]+$"))
_SHARE = re.compile(r"(?i)sharer|/share|/intent/|/dialog/|/plugins/")

_STOPWORDS = {
	"es": "de la que el en y los las del para con por una nuestros nuestra productos empresa somos más",
	"pt": "de que não para com uma os as do da dos nossos nossa produtos empresa somos mais você",
	"en": "the and for with our you your are products company about from that this",
	"fr": "le les et des pour une nous nos produits entreprise avec est vous",
	"de": "und der die das für mit wir unsere produkte unternehmen ist sie von",
	"it": "il di che per con una nostri nostra prodotti azienda siamo della sono",
}
_STOPSETS = {k: set(v.split()) for k, v in _STOPWORDS.items()}


def guess_language(html: str, text: str) -> str | None:
	"""The page's language code: <html lang>, then Content-Language, then a stopword count."""
	m = re.search(r"""(?is)<html\b[^>]*\blang\s*=\s*["']?([a-zA-Z]{2,3})(?:[-_][a-zA-Z0-9]+)?""", html[:5000])
	if m:
		return m.group(1).lower()
	lang = (_meta(html).get("content-language") or "")[:2].lower()
	if re.fullmatch(r"[a-z]{2}", lang):
		return lang
	words = re.findall(r"[a-zà-ÿ]+", text[:20000].lower())
	if len(words) < 20:
		return None
	scores = {k: sum(1 for w in words if w in stop) for k, stop in _STOPSETS.items()}
	best = max(scores, key=scores.get)
	return best if scores[best] >= 5 else None


# ── which pages ──────────────────────────────────────────────────────────────

# (kind, words a path segment or anchor starts with). Contact pages first: that is where addresses are.
PAGE_KINDS = (
	("contact", ("contact", "contacto", "contactenos", "contactanos", "contato", "kontakt", "contatti", "contattaci")),
	("legal", ("imprint", "impressum", "aviso-legal", "legal-notice", "mentions-legales", "note-legali")),
	("about", ("about", "nosotros", "quienes-somos", "quem-somos", "sobre", "empresa", "company", "a-propos",
		"qui-sommes-nous", "chi-siamo", "ueber-uns", "uber-uns", "historia")),
	("team", ("team", "equipo", "equipe", "nuestro-equipo", "our-team", "staff", "leadership")),
)
GUESSES = {
	"es": ("/contacto", "/nosotros", "/quienes-somos", "/empresa", "/aviso-legal"),
	"pt": ("/contato", "/sobre", "/quem-somos", "/empresa"),
	"de": ("/kontakt", "/impressum", "/ueber-uns", "/team"),
	"": ("/contact", "/about", "/team", "/imprint", "/contacto", "/nosotros", "/quienes-somos", "/empresa", "/sobre"),
}


def _fold(text: str) -> str:
	return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def page_kind(url: str, anchor: str = "") -> str | None:
	path = _fold(unquote(urlsplit(url).path))
	if re.search(r"\.(pdf|jpe?g|png|gif|webp|zip|docx?|xlsx?|mp4)$", path):
		return None
	segments = [re.sub(r"\.(html?|php|aspx?)$", "", s) for s in path.split("/") if s]
	anchor = re.sub(r"[^a-z]+", "-", _fold(anchor)).strip("-")
	for kind, words in PAGE_KINDS:
		for w in words:
			if any(s == w or s.startswith((w + "-", w + "_")) for s in segments) or anchor == w or anchor.startswith(w + "-"):
				return kind
	return None


def same_site(url: str, domain: str) -> bool:
	host = normalize_domain(url)
	return bool(host) and (host == domain or host.endswith("." + domain) or domain.endswith("." + host))


def pick_pages(candidates: list[tuple[str, str]], domain: str, limit: int) -> list[str]:
	"""Up to `limit` same-site pages, one of each kind before a second of any."""
	by_kind: dict[str, list[str]] = {k: [] for k, _w in PAGE_KINDS}
	seen = set()
	for url, anchor in candidates:
		url = url.split("#")[0]
		key = url.rstrip("/").lower().replace("://www.", "://")
		kind = page_kind(url, anchor)
		if not kind or key in seen or not same_site(url, domain) or urlsplit(url).scheme not in PORTS:
			continue
		seen.add(key)
		by_kind[kind].append(url)
	out = []
	while len(out) < limit and any(by_kind.values()):
		for k in by_kind:
			if by_kind[k] and len(out) < limit:
				out.append(by_kind[k].pop(0))
	return out


def sitemap_urls(xml: str) -> list[str]:
	return [html_lib.unescape(u.strip()) for u in re.findall(r"(?is)<loc>\s*(.*?)\s*</loc>", xml or "")[:SITEMAP_LOCS]]


# ── the profile ──────────────────────────────────────────────────────────────


def _robots(s, origin: str):
	"""The site's robots rules, or None when there are none to keep. Per RFC 9309 a missing file
	(4xx) allows everything and a server error (5xx) allows nothing."""
	try:
		page = fetch(f"{origin}/robots.txt", s, max_bytes=500_000)
	except UnsafeURL:
		raise
	except Exception:
		return None
	rp = RobotFileParser()
	if page.status >= 500:
		rp.disallow_all = True
		rp.modified()
		return rp
	if not page.ok:
		return None
	rp.parse(page.text.splitlines())
	return rp


def _allowed(rp, url: str) -> bool:
	return rp is None or rp.can_fetch(ROBOTS_AGENT, url)


def _emails_for(found: list[str], domains: set[str]) -> list[str]:
	"""Addresses on the site's own domain, plus role inboxes elsewhere (ventas.acme@gmail.com is how
	many small importers publish theirs). A personal address on another domain is not the company's."""
	from excom.excom_ai.tools.email import is_role

	out = []
	for e in found:
		d = e.rpartition("@")[2]
		if any(d == x or d.endswith("." + x) or x.endswith("." + d) for x in domains) or is_role(e):
			out.append(e)
	return out


def profile(url: str, s: requests.Session | None = None) -> dict:
	"""Read a company's website. See the module docstring for the pages and the guard."""
	s = s or session()
	started = time.monotonic()
	url = (url or "").strip()
	bare = "://" not in url
	tries = [f"https://{url}", f"http://{url}"] if bare else [url]
	given = normalize_domain(tries[0])
	out = {"url": url, "final_url": None, "domain": given, "title": None, "description": None, "language": None,
		"emails": [], "phones": [], "has_online_store": False, "platform": None, "pages": {}, "text": "",
		"links": {}, "blocked_by_robots": False, "error": None}
	home = rp = None
	for i, attempt in enumerate(tries):
		parts = urlsplit(attempt)
		rp = _robots(s, f"{parts.scheme}://{parts.netloc}")
		if not _allowed(rp, attempt):
			out["blocked_by_robots"] = True
			return out
		try:
			home = fetch(attempt, s)
			break
		except requests.ConnectionError:
			# A bare domain with no TLS answer is tried once over http.
			if i == len(tries) - 1:
				raise
	if not home.ok or not home.is_html:
		out["error"] = f"HTTP {home.status}" if not home.ok else f"not a page ({home.content_type})"
		return out
	final = home.url
	domain = normalize_domain(final) or given
	if domain != given:
		# Redirected to another host (acme.mx → acme.com.mx): its robots.txt is the one that applies.
		fp = urlsplit(final)
		rp = _robots(s, f"{fp.scheme}://{fp.netloc}")
	out.update({"final_url": final, "domain": domain})

	meta = _meta(home.text)
	title = re.search(r"(?is)<title[^>]*>(.*?)</title>", home.text[:200_000])
	out["title"] = (re.sub(r"\s+", " ", html_lib.unescape(title.group(1))).strip()[:200] or None) if title else None
	out["description"] = (meta.get("description") or meta.get("og:description") or "").strip()[:500] or None

	candidates = links(home.text, final)
	targets = pick_pages(candidates, domain, MAX_PAGES - 1)
	if len(targets) < 2:
		targets = _from_sitemap(s, rp, final, domain, targets)
	home_text = plain_text(home.text)
	language = guess_language(home.text, home_text)
	if not targets:
		base = f"{urlsplit(final).scheme}://{urlsplit(final).netloc}"
		targets = [base + p for p in GUESSES.get(language or "", GUESSES[""])][: MAX_PAGES - 1]

	pages = [home]
	for t in targets:
		if len(pages) >= MAX_PAGES or time.monotonic() - started > PROFILE_SECONDS:
			break
		if not _allowed(rp, t):
			continue
		try:
			p = fetch(t, s)
		except UnsafeURL:
			continue
		except requests.RequestException:
			continue
		if p.ok and p.is_html and same_site(p.url, domain):
			pages.append(p)

	emails, phones, socials, texts = [], [], {}, []
	for p in pages:
		text = home_text if p is home else plain_text(p.text)
		texts.append(text)
		out["pages"][p.url] = text[:PAGE_TEXT_CAP]
		emails += [e for e in emails_on_page(p.text) if e not in emails]
		phones += [n for n in phones_on_page(p.text, text) if n not in phones]
		for href, _anchor in links(p.text, p.url):
			host = normalize_domain(href)
			for name, pattern in SOCIAL:
				if name not in socials and re.search(pattern, host) and not _SHARE.search(href):
					socials[name] = href[:300]
	raw = " ".join(p.text[:300_000] for p in pages)
	out.update({
		"language": language,
		"emails": _emails_for(emails, {domain, given})[:20],
		"phones": phones[:5],
		"has_online_store": bool(_STORE.search(raw)),
		"platform": next((name for name, pattern in _PLATFORMS if re.search(pattern, raw, re.I)), None),
		"text": "\n".join(texts)[:TEXT_CAP],
		"links": socials,
	})
	return out


def _from_sitemap(s, rp, final: str, domain: str, have: list[str]) -> list[str]:
	"""sitemap.xml is read only to find the contact, about, team and imprint pages."""
	base = f"{urlsplit(final).scheme}://{urlsplit(final).netloc}"
	maps = [m for m in (rp.site_maps() or [] if rp else []) if same_site(m, domain)][:1] or [base + "/sitemap.xml"]
	locs: list[str] = []
	for _round in range(2):
		if not maps or not _allowed(rp, maps[0]):
			break
		try:
			page = fetch(maps.pop(0), s)
		except (UnsafeURL, requests.RequestException):
			break
		if not page.ok:
			break
		found = sitemap_urls(page.text)
		children = [u for u in found if u.lower().split("?")[0].endswith(".xml")]
		if children and len(children) == len(found):
			# A sitemap index: the pages sitemap, else the first.
			maps = sorted(children, key=lambda u: "page" not in u.lower())[:1]
			continue
		locs = found
		break
	return have + [u for u in pick_pages([(u, "") for u in locs], domain, MAX_PAGES - 1) if u not in have]


@tool("web_reader", tier="read", cost=0.0)
class WebReader(Tool):
	"""profile(url) -> {title, description, language, emails, phones, has_online_store, platform, pages, text,
	links, final_url, domain, blocked_by_robots, error}. Raises UnsafeURL (a ToolRefused) for a URL outside
	the public-website boundary, and requests' errors when the site cannot be reached."""

	def profile(self, url: str) -> dict:
		return profile(url)

	def fake_profile(self, url: str) -> dict:
		domain = normalize_domain(url) or "juguetes-ejemplo.mx"
		home = f"https://www.{domain}/"
		return {"url": url, "final_url": home, "domain": domain, "title": "Juguetes Ejemplo | Importador y mayorista",
			"description": "Importamos y distribuimos juguetes y artículos de temporada para tiendas en todo México.",
			"language": "es", "emails": [f"ventas@{domain}", f"compras@{domain}", f"maria.lopez@{domain}"],
			"phones": ["+52 55 1234 5678"], "has_online_store": False, "platform": "WordPress",
			"pages": {home: "Juguetes Ejemplo. Importador y mayorista de juguetes desde 1998.",
				f"{home}contacto": "Contacto. Ventas: ventas@" + domain},
			"text": "Juguetes Ejemplo. Importador y mayorista de juguetes desde 1998. Atendemos a más de 400 tiendas."
				" Pistolas de agua, juguetes de playa y temporada de verano.",
			"links": {"facebook": "https://www.facebook.com/juguetesejemplo", "whatsapp": "https://wa.me/525512345678"},
			"blocked_by_robots": False, "error": None}
