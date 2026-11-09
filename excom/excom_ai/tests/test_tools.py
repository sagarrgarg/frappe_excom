"""Excom AI Phase 1C: the research tool adapters (HLD-007 §9). Discovery sources, the website profiler and
its SSRF guard, the email finder and its no-guess rule, the verifiers, Precleared, TrulyInbox, the Volza
import, and tools.use's limits. Nothing touches the network: requests, DNS and name resolution are
patched, or the fakes run."""

import base64
import json
from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, get_first_day, nowdate
from frappe.utils.password import remove_encrypted_password, set_encrypted_password
from requests.structures import CaseInsensitiveDict

from excom.excom_ai import tools
from excom.excom_ai.tools import discovery, web
from excom.excom_ai.tools import email as em

PUBLIC = "93.184.216.34"
DOMAIN = "acme-juguetes.mx"
QUERY = {"keywords": ["toys"], "countries": ["Mexico"], "languages": ["es"], "category": "toys", "city": None,
	"limit": 10}


class Resp:
	"""Just enough of requests.Response for the adapters."""

	def __init__(self, status=200, body=b"", headers=None, data=None, url=""):
		if data is not None:
			body = json.dumps(data).encode()
		self.status_code, self.url = status, url
		self.content = body if isinstance(body, bytes) else body.encode()
		kind = "application/json" if data is not None else "text/html"
		self.headers = CaseInsensitiveDict(headers or {"Content-Type": kind})
		self.ok = status < 400

	@property
	def text(self):
		return self.content.decode("utf-8", "replace")

	def json(self):
		return json.loads(self.content)

	def iter_content(self, n):
		for i in range(0, len(self.content), n):
			yield self.content[i:i + n]

	def close(self):
		pass

	def raise_for_status(self):
		if self.status_code >= 400:
			raise requests.HTTPError(f"HTTP {self.status_code}")


def keyed(cls, secret="test-key", **record):
	"""An adapter instance with a key, as tools.use would build it, without a tool record."""
	inst = cls(frappe._dict(record) if record else None)
	inst.secret = lambda field="api_key": secret
	return inst


class Site:
	"""A scripted website: URL → Resp, every request remembered, every host public."""

	def __init__(self, pages):
		self.pages, self.asked = pages, []

	def get(self, session, url, **kw):
		self.asked.append(url)
		return self.pages.get(url) or Resp(404, b"not found")

	def __enter__(self):
		self._p = [patch.object(requests.Session, "get", lambda s, url, **kw: self.get(s, url, **kw)),
			patch.object(web, "_resolve", lambda host, port: [PUBLIC])]
		for p in self._p:
			p.start()
		return self

	def __exit__(self, *a):
		for p in self._p:
			p.stop()


class TestGuard(FrappeTestCase):
	def test_private_loopback_link_local_and_metadata_are_refused(self):
		for url in ("http://127.0.0.1/", "http://10.1.2.3/", "http://192.168.0.10/", "http://172.16.5.5/",
				"http://169.254.169.254/latest/meta-data/", "http://100.64.0.1/", "http://0.0.0.0/", "http://224.0.0.1/",
				"http://240.0.0.1/", "http://[::1]/", "http://[fe80::1]/", "http://[fd00::1]/", "http://[::ffff:127.0.0.1]/",
				"http://[::ffff:169.254.169.254]/", "http://[64:ff9b::a9fe:a9fe]/", "http://localhost/", "http://shop.localhost/"):
			with self.subTest(url=url), self.assertRaises(web.UnsafeURL):
				web.check_url(url)

	def test_bad_scheme_port_or_credentials_are_refused(self):
		for url in ("ftp://example.com/", "file:///etc/passwd", "gopher://example.com/", "http://example.com:8080/",
				"https://example.com:22/", "http://user:pw@example.com/", "http://:80/", "javascript:alert(1)"):
			with self.subTest(url=url), self.assertRaises(web.UnsafeURL):
				web.check_url(url)

	def test_a_name_resolving_to_a_private_address_is_refused(self):
		answers = {"intranet.example": ["10.0.0.5"], "metadata.example": ["169.254.169.254"], "v6.example": ["::1"],
			"mixed.example": [PUBLIC, "127.0.0.1"]}
		with patch.object(web, "_resolve", lambda host, port: answers[host]):
			for host in answers:
				with self.subTest(host=host), self.assertRaises(web.UnsafeURL):
					web.check_url(f"https://{host}/")

	def test_public_hosts_pass(self):
		with patch.object(web, "_resolve", lambda host, port: [PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"]):
			self.assertEqual(web.check_url("https://www.example.com/contacto"), "https://www.example.com/contacto")
			self.assertEqual(web.public_addresses("example.com", 443)[0], PUBLIC)  # IPv4 first
		self.assertEqual(web.check_url("http://8.8.8.8/"), "http://8.8.8.8/")

	def test_a_redirect_to_an_internal_address_is_not_followed(self):
		site = Site({"https://acme.example/": Resp(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})})
		with site, self.assertRaises(web.UnsafeURL):
			web.fetch("https://acme.example/")
		self.assertEqual(site.asked, ["https://acme.example/"])

	def test_a_redirect_to_another_port_is_not_followed_and_redirects_are_capped(self):
		site = Site({"https://acme.example/": Resp(301, headers={"Location": "https://acme.example:8443/"})})
		with site, self.assertRaises(web.UnsafeURL):
			web.fetch("https://acme.example/")
		loop = Site({f"https://acme.example/{i}": Resp(302, headers={"Location": f"/{i + 1}"}) for i in range(10)})
		with loop, self.assertRaises(web.UnsafeURL):
			web.fetch("https://acme.example/0")
		self.assertEqual(len(loop.asked), web.MAX_REDIRECTS + 1)

	def test_a_relative_redirect_is_followed(self):
		site = Site({"http://acme.example/": Resp(301, headers={"Location": "https://www.acme.example/"}),
			"https://www.acme.example/": Resp(200, b"<html>hola</html>")})
		with site:
			page = web.fetch("http://acme.example/")
		self.assertEqual((page.url, page.text), ("https://www.acme.example/", "<html>hola</html>"))

	def test_the_body_is_capped(self):
		with Site({"https://big.example/": Resp(200, b"x" * 50_000)}):
			page = web.fetch("https://big.example/", max_bytes=10_000)
		self.assertEqual(len(page.text), 10_000)

	def test_the_connection_goes_to_the_checked_address(self):
		"""DNS rebinding: the address is checked when the socket opens, and the socket opens to it."""
		conn = web._HTTPConnection("rebind.example", 80)
		with patch.object(web, "_resolve", lambda host, port: ["127.0.0.1"]), \
				patch.object(web, "create_connection") as connect, self.assertRaises(web.UnsafeURL):
			conn._new_conn()
		connect.assert_not_called()
		with patch.object(web, "_resolve", lambda host, port: [PUBLIC]), patch.object(web, "create_connection") as connect:
			web._HTTPSConnection("shop.example", 443)._new_conn()
		self.assertEqual(connect.call_args.args[0], (PUBLIC, 443))
		with self.assertRaises(web.UnsafeURL):
			web._HTTPConnection("shop.example", 8080)._new_conn()

	def test_the_session_uses_the_guarded_pools_and_no_proxies(self):
		s = web.session()
		self.assertFalse(s.trust_env)
		self.assertIn("ExcomAI", s.headers["User-Agent"])
		self.assertNotIn("@", s.headers["User-Agent"])
		pm = s.get_adapter("https://example.com/").poolmanager
		self.assertIs(pm.pool_classes_by_scheme["https"], web._HTTPSPool)
		self.assertIs(pm.pool_classes_by_scheme["http"], web._HTTPPool)


HOME = f"""<!doctype html><html lang="es-MX"><head><meta charset="utf-8">
<title>Acme Juguetes | Importador y mayorista</title>
<meta name="description" content="Importamos juguetes y artículos de temporada para todo México.">
<script>var x = "fake@{DOMAIN}";</script></head><body>
<nav><a href="/">Inicio</a><a href="/nosotros">Quiénes somos</a><a href="/contacto">Contacto</a>
<a href="/privacidad">Aviso de privacidad</a><a href="https://otro-sitio.mx/contacto">Socio</a>
<a href="/catalogo.pdf">Catálogo</a></nav>
<p>Bienvenidos. Somos importadores de pistolas de agua y juguetes de playa. Tel: (33) 3612-4455</p>
<a href="mailto:ventas@{DOMAIN}">ventas@{DOMAIN}</a> <a href="tel:+523336124455">Llámanos</a>
<p>Escríbenos: ventas.acme@gmail.com o juan.perez@gmail.com</p>
<button class="add-to-cart">Agregar al carrito</button>
<a href="https://www.facebook.com/acmejuguetes">Facebook</a>
<a href="https://www.facebook.com/sharer.php?u=x">Compartir</a>
<a href="https://wa.me/523312345678">WhatsApp</a></body></html>"""


class TestProfile(FrappeTestCase):
	def site(self, robots="User-agent: *\nDisallow: /contacto\n"):
		base = f"https://{DOMAIN}"
		return Site({
			f"{base}/robots.txt": Resp(200, robots.encode(), {"Content-Type": "text/plain"}),
			f"{base}/": Resp(200, HOME.encode()),
			f"{base}/nosotros": Resp(200, f"<html><p>Maria Lopez, Gerente de Compras: maria.lopez@{DOMAIN}</p></html>".encode()),
			f"{base}/contacto": Resp(200, f"<html><p>compras@{DOMAIN}</p></html>".encode()),
		})

	def test_profile_reads_the_site_and_keeps_robots_disallow(self):
		with self.site() as s:
			out = web.profile(f"https://{DOMAIN}/")
		self.assertIn(f"https://{DOMAIN}/nosotros", s.asked)
		self.assertNotIn(f"https://{DOMAIN}/contacto", s.asked)  # Disallow: /contacto
		self.assertNotIn("https://otro-sitio.mx/contacto", s.asked)
		self.assertNotIn(f"https://{DOMAIN}/catalogo.pdf", s.asked)
		self.assertEqual(out["title"], "Acme Juguetes | Importador y mayorista")
		self.assertTrue(out["description"].startswith("Importamos juguetes"))
		self.assertEqual(out["language"], "es")
		self.assertEqual(out["emails"], [f"ventas@{DOMAIN}", "ventas.acme@gmail.com", f"maria.lopez@{DOMAIN}"])
		self.assertNotIn(f"compras@{DOMAIN}", out["emails"])
		self.assertNotIn(f"fake@{DOMAIN}", out["emails"])  # inside a script
		self.assertIn("+523336124455", out["phones"])
		self.assertTrue(out["has_online_store"])
		self.assertEqual(out["links"], {"facebook": "https://www.facebook.com/acmejuguetes",
			"whatsapp": "https://wa.me/523312345678"})
		self.assertEqual(len(out["pages"]), 2)
		self.assertLessEqual(len(out["text"]), web.TEXT_CAP)
		self.assertIn("pistolas de agua", out["text"])

	def test_without_robots_the_contact_page_is_read(self):
		with self.site(robots="") as s:
			out = web.profile(DOMAIN)
		self.assertIn(f"https://{DOMAIN}/contacto", s.asked)
		self.assertIn(f"compras@{DOMAIN}", out["emails"])

	def test_a_homepage_disallowed_by_robots_is_not_fetched(self):
		with self.site(robots="User-agent: ExcomAI\nDisallow: /\n") as s:
			out = web.profile(f"https://{DOMAIN}/")
		self.assertTrue(out["blocked_by_robots"])
		self.assertEqual(s.asked, [f"https://{DOMAIN}/robots.txt"])

	def test_pages_are_capped_and_found_from_the_sitemap(self):
		base = f"https://{DOMAIN}"
		locs = "".join(f"<url><loc>{base}/{p}</loc></url>" for p in ("contacto", "quienes-somos", "equipo", "aviso-legal",
			"empresa", "contact", "about", "team", "producto/1"))
		pages = {f"{base}/robots.txt": Resp(404), f"{base}/": Resp(200, b"<html lang='es'><p>Hola</p></html>"),
			f"{base}/sitemap.xml": Resp(200, f"<urlset>{locs}</urlset>".encode(), {"Content-Type": "application/xml"})}
		pages.update({u: Resp(200, b"<html><p>x</p></html>") for u in (f"{base}/{p}" for p in ("contacto", "quienes-somos",
			"equipo", "aviso-legal", "empresa", "contact", "about", "team"))})
		with Site(pages) as s:
			out = web.profile(base)
		self.assertEqual(len(out["pages"]), web.MAX_PAGES)
		self.assertNotIn(f"{base}/producto/1", s.asked)

	def test_an_internal_url_is_refused_before_any_request(self):
		with Site({}) as s, patch.object(web, "_resolve", lambda host, port: ["10.0.0.7"]), self.assertRaises(web.UnsafeURL):
			web.profile("https://intranet.acme.example/")
		self.assertEqual(s.asked, [])


def mx(*answers, fail=False):
	def fake(name, rtype="TXT"):
		if fail:
			raise requests.ConnectionError("no DNS")
		return list(answers)
	return patch("excom.excom_ai.mail.dns_txt", side_effect=fake)


class TestEmailFinder(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("Excom AI Email Domain", {"domain": ["like", "%.qa-finder.example"]})

	def test_mx_type(self):
		for answers, expected in ((["10 aspmx.l.google.com.", "5 alt1.aspmx.l.google.com."], "Google"),
				(["0 acme-mx.mail.protection.outlook.com."], "Microsoft"), (["10 mx0a-001.pphosted.com."], "Gateway"),
				(["10 mail.acme.mx."], "Other"), ([], "None"), (["0 ."], "None")):
			with self.subTest(answers=answers), mx(*answers):
				self.assertEqual(em.find("acme.qa-finder.example")["mx_type"], expected)
		with mx(fail=True):
			self.assertIsNone(em.find("acme.qa-finder.example")["mx_type"])  # unknown, not "no MX"

	def test_role_inboxes(self):
		for e in ("info@acme.mx", "ventas@acme.mx", "VENTAS.MX@acme.mx", "compras2@acme.mx", "import-dept@acme.mx",
				"contacto@acme.mx", "sales+web@acme.mx", "atencionalcliente@acme.mx", "vendas@acme.com.br"):
			with self.subTest(e=e):
				self.assertTrue(em.is_role(e))
		for e in ("maria.lopez@acme.mx", "juan.ventas@acme.mx", "mlopez@acme.mx"):
			with self.subTest(e=e):
				self.assertFalse(em.is_role(e))

	def test_the_pattern_comes_from_a_published_named_address(self):
		d = "acme.qa-finder.example"
		people = [{"first_name": "María", "last_name": "López", "title": "Compras"},
			{"first_name": "Carlos", "last_name": "Ruiz Díaz", "title": "Director General"}]
		with mx("10 aspmx.l.google.com."):
			out = em.find(d, published=[f"maria.lopez@{d}", f"ventas@{d}", "otra.persona@gmail.com"], people=people)
		self.assertEqual((out["mx_type"], out["pattern"], out["pattern_source"], out["catch_all"]),
			("Google", "first.last", "Published", None))
		self.assertGreaterEqual(out["pattern_confidence"], 0.9)
		by = {a["email"]: a for a in out["addresses"]}
		self.assertEqual(set(by), {f"maria.lopez@{d}", f"ventas@{d}", f"carlos.ruiz@{d}"})
		self.assertEqual((by[f"ventas@{d}"]["is_inbox"], by[f"ventas@{d}"]["source"]), (True, "Published"))
		self.assertEqual((by[f"maria.lopez@{d}"]["first_name"], by[f"maria.lopez@{d}"]["status_hint"]), ("María",
			"Published"))
		guess = by[f"carlos.ruiz@{d}"]
		self.assertEqual((guess["source"], guess["status_hint"], guess["title"], guess["is_inbox"]),
			("Pattern", "Guess", "Director General", False))

	def test_no_pattern_no_guess(self):
		d = "acme.qa-finder.example"
		with mx("10 mail.acme.mx."):
			out = em.find(d, published=[f"info@{d}", f"ventas@{d}"], people=[{"first_name": "Carlos", "last_name": "Ruiz"}])
		self.assertIsNone(out["pattern"])
		self.assertEqual([a["source"] for a in out["addresses"]], ["Published", "Published"])
		self.assertTrue(all(a["is_inbox"] for a in out["addresses"]))

	def test_never_an_address_another_named_person_could_own(self):
		d = "acme.qa-finder.example"
		people = [{"first_name": "Maria", "last_name": "Lopez"}, {"first_name": "John", "last_name": "Smith"},
			{"first_name": "Jane", "last_name": "Smith"}, {"first_name": "Pedro", "last_name": "Alba"}]
		with mx("10 mail.acme.mx."):
			out = em.find(d, published=[f"mlopez@{d}", f"palba@{d}"], people=people)
		self.assertEqual(out["pattern"], "flast")
		emails = [a["email"] for a in out["addresses"]]
		self.assertNotIn(f"jsmith@{d}", emails)  # John's or Jane's: nobody's guess
		self.assertEqual([a for a in out["addresses"] if a["source"] == "Pattern"], [])
		self.assertEqual({a["email"]: a["first_name"] for a in out["addresses"]}, {f"mlopez@{d}": "Maria",
			f"palba@{d}": "Pedro"})

	def test_no_guess_for_a_generic_name_or_an_address_already_published(self):
		d = "acme.qa-finder.example"
		people = [{"first_name": "Ventas", "last_name": "México"}, {"first_name": "Ana", "last_name": ""},
			{"first_name": "Luis", "last_name": "Mora"}, {"first_name": "Rosa", "last_name": "Vega"}]
		with mx("10 mail.acme.mx."):
			out = em.find(d, published=[f"rosa.vega@{d}", f"luis.mora@{d}"], people=people[:3])
		self.assertEqual(out["pattern"], "first.last")  # from rosa.vega@, shape only (Rosa is not named here)
		patterned = [a["email"] for a in out["addresses"] if a["source"] == "Pattern"]
		self.assertEqual(patterned, [])  # Luis owns luis.mora@ (published); Ventas México and Ana are generic

	def test_named_addresses_from_hunter_count_as_evidence(self):
		d = "acme.qa-finder.example"
		known = [{"email": f"carlos.ramirez@{d}", "first_name": "Carlos", "last_name": "Ramírez", "source": "Hunter"}]
		with mx("10 mail.acme.mx."):
			out = em.find(d, published=[f"ventas@{d}"], people=[{"first_name": "Lucía", "last_name": "Núñez"}], known=known)
		self.assertEqual((out["pattern"], out["pattern_source"]), ("first.last", "Hunter"))
		by = {a["email"]: a["source"] for a in out["addresses"]}
		self.assertEqual(by, {f"ventas@{d}": "Published", f"carlos.ramirez@{d}": "Hunter", f"lucia.nunez@{d}": "Pattern"})

	def test_the_email_domain_record_is_upserted(self):
		d = "acme.qa-finder.example"
		em.save_domain({"domain": d, "mx_type": "Google", "pattern": "first.last", "pattern_source": "Published",
			"pattern_confidence": 0.9, "catch_all": None})
		rec = frappe.get_doc("Excom AI Email Domain", d)
		self.assertEqual((rec.mx_type, rec.pattern, rec.pattern_source, flt(rec.pattern_confidence), rec.catch_all),
			("Google", "first.last", "Published", 0.9, 0))
		self.assertTrue(rec.checked_on)
		em.save_domain({"domain": d, "mx_type": None, "pattern": "{f}", "pattern_source": "Published",
			"pattern_confidence": 0.6, "catch_all": True})
		rec.reload()
		self.assertEqual((rec.mx_type, rec.pattern, rec.catch_all), ("Google", "first.last", 1))  # weaker pattern kept out
		with mx("10 mail.acme.mx."):
			out = em.find(d, published=[], people=[{"first_name": "Ana", "last_name": "Ruiz"}])
		self.assertEqual([a["email"] for a in out["addresses"]], [f"ana.ruiz@{d}"])  # the stored, published pattern

	def test_the_fake_runs_through_tools_use(self):
		frappe.flags.excom_ai_fake_tools = True
		try:
			out = tools.use("email_finder", "find", domain=DOMAIN, published=[f"maria.lopez@{DOMAIN}"],
				people=[{"first_name": "Maria", "last_name": "Lopez"}, {"first_name": "Jose", "last_name": "Rios"}])
		finally:
			frappe.flags.excom_ai_fake_tools = False
		self.assertEqual(out["mx_type"], "Google")
		self.assertIn(f"jose.rios@{DOMAIN}", [a["email"] for a in out["addresses"]])


class TestVerifiers(FrappeTestCase):
	def call(self, cls, method, answer, status=200, **kwargs):
		with patch("requests.get", return_value=Resp(status, data=answer)) as get:
			out = getattr(keyed(cls), method)(**kwargs)
		return out, get

	def test_reoon(self):
		for answer, expected in (
			({"status": "safe", "is_catch_all": False}, ("Verified", False)),
			({"status": "catch_all", "is_catch_all": True}, ("Risky", True)),
			({"status": "invalid"}, ("Invalid", False)),
			({"status": "disposable"}, ("Invalid", False)),
			({"status": "role_account", "is_deliverable": True}, ("Verified", False)),
			({"status": "role_account", "is_deliverable": False}, ("Unknown", False)),
			({"status": "unknown"}, ("Unknown", False)),
		):
			with self.subTest(answer=answer):
				out, get = self.call(em.Reoon, "verify", answer, email="ventas@acme.mx")
				self.assertEqual((out["status"], out["catch_all"]), expected)
		self.assertEqual(get.call_args.args[0], "https://emailverifier.reoon.com/api/v1/verify")
		self.assertEqual(get.call_args.kwargs["params"], {"email": "ventas@acme.mx", "key": "test-key", "mode": "power"})
		with self.assertRaises(RuntimeError) as e:
			self.call(em.Reoon, "verify", {"status": "error"}, status=401, email="x@acme.mx")
		self.assertNotIn("test-key", str(e.exception))

	def test_zerobounce(self):
		for answer, expected in (({"status": "valid", "sub_status": ""}, ("Verified", False)),
				({"status": "catch-all", "sub_status": ""}, ("Risky", True)),
				({"status": "invalid", "sub_status": "mailbox_not_found"}, ("Invalid", False)),
				({"status": "unknown", "sub_status": "timeout_exceeded"}, ("Unknown", False)),
				({"status": "do_not_mail", "sub_status": "role_based"}, ("Risky", False)),
				({"status": "do_not_mail", "sub_status": "disposable"}, ("Invalid", False)),
				({"status": "spamtrap", "sub_status": ""}, ("Invalid", False))):
			with self.subTest(answer=answer):
				out, get = self.call(em.ZeroBounce, "verify", answer, email="ventas@acme.mx")
				self.assertEqual((out["status"], out["catch_all"]), expected)
		self.assertEqual(get.call_args.kwargs["params"], {"api_key": "test-key", "email": "ventas@acme.mx"})
		with self.assertRaises(RuntimeError):
			self.call(em.ZeroBounce, "verify", {"error": "Invalid API key or your account ran out of credits"},
				email="x@acme.mx")

	def test_hunter_verify_and_domain_search(self):
		for answer, expected in (({"status": "valid", "result": "deliverable"}, ("Verified", False)),
				({"status": "accept_all", "accept_all": True}, ("Risky", True)), ({"status": "invalid"}, ("Invalid", False)),
				({"status": "webmail", "result": "deliverable"}, ("Verified", False)), ({"status": "unknown"}, ("Unknown", False))):
			with self.subTest(answer=answer):
				out, get = self.call(em.Hunter, "verify", {"data": answer}, email="ventas@acme.mx")
				self.assertEqual((out["status"], out["catch_all"]), expected)
		self.assertEqual(get.call_args.args[0], "https://api.hunter.io/v2/email-verifier")
		search = {"data": {"domain": "acme.mx", "pattern": "{first}.{last}", "emails": [
			{"value": "Carlos.Ramirez@acme.mx", "type": "personal", "confidence": 94, "first_name": "Carlos",
				"last_name": "Ramírez", "position": "Gerente de Compras", "sources": [{"uri": "https://acme.mx/nosotros"}]},
			{"value": "compras@acme.mx", "type": "generic", "confidence": 90, "sources": []}]}}
		out, get = self.call(em.Hunter, "domain_search", search, domain="https://www.acme.mx/")
		self.assertEqual(get.call_args.kwargs["params"]["domain"], "acme.mx")
		self.assertEqual([(a["email"], a["source"], a["title"], a["is_inbox"], a["status_hint"],
			a["domain_pattern"]) for a in out],
			[("carlos.ramirez@acme.mx", "Hunter", "Gerente de Compras", False, "Published", "first.last"),
			("compras@acme.mx", "Hunter", None, True, "Guess", "first.last")])

	def test_the_key_never_comes_back_in_raw(self):
		out, _get = self.call(em.Reoon, "verify", {"status": "safe", "echo": "key=test-key"}, email="a@acme.mx")
		self.assertNotIn("test-key", json.dumps(out["raw"]))

	def test_precleared(self):
		answer = {"verdict": "consent_needed", "reasons": [{"text": "Prior consent needed for a named person.",
			"source_title": "UWG §7"}], "next_step": "Write to the published inbox.", "receipt": {"id": "rcpt_1"},
			"facts": {"recipient_country": "DE"}}
		with patch("requests.post", return_value=Resp(200, data=answer)) as post:
			out = keyed(em.Precleared).check(email="Hans.Muller@acme.de", country="Germany", sender_country="India",
				company="Acme  GmbH", name="Hans Müller")
		self.assertEqual((out["verdict"], out["receipt_id"], out["country"]), ("Consent needed", "rcpt_1", "DE"))
		self.assertIn("Prior consent", out["reason"])
		body, headers = post.call_args.kwargs["json"], post.call_args.kwargs["headers"]
		self.assertEqual((body["recipient_email"], body["recipient_country"], body["sender_country"], body["company_name"]),
			("hans.muller@acme.de", "DE", "IN", "Acme GmbH"))
		self.assertEqual(headers["Authorization"], "Bearer test-key")
		for verdict, mapped in (("ok_to_email", "OK to email"), ("blocked", "Blocked"), ("unknown", "Unknown")):
			with patch("requests.post", return_value=Resp(200, data={"verdict": verdict})):
				self.assertEqual(keyed(em.Precleared).check(email="a@acme.mx", country="Mexico")["verdict"], mapped)
		for status in (401, 402, 429, 500):
			with self.subTest(status=status), patch("requests.post", return_value=Resp(status, data={"error": "x"})), \
					self.assertRaises(RuntimeError):
				keyed(em.Precleared).check(email="a@acme.mx", country="Mexico")

	def test_trulyinbox(self):
		answers = {"/v2/workspaces": [{"id": 42}], "/v2/workspaces/42/accounts": [{"id": 7,
			"emailAddress": "Ventas@ours.example"}],
			"/v2/workspaces/42/accounts/7/warmup": {"warmupStatus": "active", "status": "ok",
				"warmupStartDate": "2026-10-01T00:00:00Z"},
			"/v2/workspaces/42/accounts/7/analytics/stats": {"deliverabilityRate": 96.0,
				"dailyBreakdown": [{"sent": 20, "spamDetected": 1}, {"sent": 25, "spamDetected": 0}]}}

		def get(url, **kw):
			return Resp(200, data={"success": True, "data": answers[url.replace(em.TRULYINBOX_API, "")]})

		with patch("requests.get", side_effect=get):
			out = keyed(em.TrulyInbox).status(mailbox_email="ventas@ours.example")
		self.assertEqual((out["found"], out["warming"], out["score"], out["sent"], out["spam"], out["started_on"]),
			(True, True, 96.0, 45, 1, "2026-10-01"))
		answers["/v2/workspaces/42/accounts/7/warmup"] = {"warmupStatus": "active", "status": "auth_failed"}
		with patch("requests.get", side_effect=get):
			out = keyed(em.TrulyInbox, account_id="42").status(mailbox_email="ventas@ours.example")
		self.assertEqual((out["warming"], out["login_ok"]), (False, False))
		with patch("requests.get", return_value=Resp(401, data={"success": False, "errorCode": "150001"})), \
				self.assertRaises(RuntimeError):
			keyed(em.TrulyInbox, account_id="42").status(mailbox_email="ventas@ours.example")

	def test_mailbox_health_calls_check_domain(self):
		with patch("excom.excom_ai.mail.check_domain", return_value={"ok": True}) as check:
			self.assertEqual(em.MailboxHealth(None).check(domain="https://www.ours.example/"), {"ok": True})
		check.assert_called_once_with("ours.example", None)


class TestDiscovery(FrappeTestCase):
	def setUp(self):
		patcher = patch.object(discovery, "_sleep", lambda s: None)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_queries_are_in_the_market_language(self):
		q = discovery.queries({"keywords": ["toys", "water guns"], "countries": ["Mexico", "Germany", "India"],
			"buyer_types": ["importer"], "languages": [], "limit": 5})
		self.assertEqual([r["q"] for r in q], ["importador de juguetes México", "Spielzeug Importeur Deutschland",
			"toys importer India", "importador de pistolas de agua México", "Wasserpistolen Importeur Deutschland",
			"water guns importer India"])
		self.assertEqual((q[0]["gl"], q[0]["lang"]), ("mx", "es"))
		self.assertEqual(len(discovery.queries({"keywords": [str(i) for i in range(20)], "countries": ["Mexico"]})),
			discovery.MAX_QUERIES)
		self.assertEqual(discovery.estimate("serper", QUERY), 0.002)  # importer and distributor

	def test_serper(self):
		organic = [
			{"title": "Juguetes en Amazon.com.mx", "link": "https://www.amazon.com.mx/juguetes", "position": 1},
			{"title": "Pistolas de agua | MercadoLibre", "link": "https://listado.mercadolibre.com.mx/pistolas", "position": 2},
			{"title": "Importadora — Wikipedia", "link": "https://es.wikipedia.org/wiki/Importadora", "position": 3},
			{"title": "Juguetes del Norte | Importador de juguetes en México", "snippet": "Importamos juguetes desde 1998.",
				"link": "https://www.juguetesdelnorte.com.mx/", "position": 4},
			{"title": "Los 10 mejores importadores de juguetes", "link": "https://blog-top.example/top", "position": 5},
			{"title": "Distribuidora Sol - Mayoreo de juguetes", "link": "https://dsol.mx/mayoreo", "position": 6},
		]
		with patch("requests.post", return_value=Resp(200, data={"organic": organic})) as post:
			out = keyed(discovery.Serper).search(query=dict(QUERY, limit=10))
		self.assertEqual([(c["name"], c["domain"], c["buyer_type"], c["source"]) for c in out],
			[("Juguetes del Norte", "juguetesdelnorte.com.mx", "Importer", "Serper"), ("Distribuidora Sol", "dsol.mx",
				"Distributor", "Serper")])
		self.assertEqual(out[0]["signals"], {"COUNTRY": "Mexico", "BUYER_TYPE": "Importer", "CARRIES_CATEGORY": "toys"})
		first = post.call_args_list[0]
		self.assertEqual(first.args[0], "https://google.serper.dev/search")
		self.assertEqual(first.kwargs["json"], {"q": "importador de juguetes México", "num": 10, "gl": "mx", "hl": "es"})
		self.assertEqual(first.kwargs["headers"]["X-API-KEY"], "test-key")

	def test_osm_in_a_city(self):
		frappe.cache.delete_value("excom_ai_geo:guadalajara, mexico")
		self.addCleanup(frappe.cache.delete_value, "excom_ai_geo:guadalajara, mexico")
		elements = [
			{"type": "node", "id": 123, "tags": {"name": "Juguetería Lupita", "shop": "toys", "website": "www.lupita.mx",
				"phone": "+52 33 1234 5678", "addr:city": "Guadalajara"}},
			{"type": "way", "id": 456, "center": {"lat": 1, "lon": 2}, "tags": {"name": "Mayoreo Jalisco", "shop": "wholesale"}},
			{"type": "node", "id": 789, "tags": {"shop": "toys"}},  # no name: not a company we can use
		]
		with patch("requests.get", return_value=Resp(200, data=[{"lat": "20.6767", "lon": "-103.3475",
			"display_name": "GDL"}])) as get, \
				patch("requests.post", return_value=Resp(200, data={"elements": elements})) as post:
			out = keyed(discovery.OpenStreetMap).search(query=dict(QUERY, keywords=["toys", "wholesale"], city="Guadalajara"))
		self.assertEqual(get.call_args.args[0], discovery.NOMINATIM)
		self.assertIn("ExcomAI", get.call_args.kwargs["headers"]["User-Agent"])
		body = post.call_args.kwargs["data"]["data"]
		self.assertIn("nwr[shop=toys](around:25000,20.67670,-103.34750);", body)
		self.assertIn("nwr[shop=wholesale](around:25000,20.67670,-103.34750);", body)
		self.assertEqual([(c["name"], c["external_id"], c["buyer_type"], c["website"], c["domain"]) for c in out],
			[("Juguetería Lupita", "osm:node/123", "Retailer", "https://www.lupita.mx", "lupita.mx"),
			("Mayoreo Jalisco", "osm:way/456", "Wholesaler", None, None)])
		self.assertEqual(out[0]["signals"]["CARRIES_CATEGORY"], "toys")
		self.assertEqual(out[0]["source_url"], "https://www.openstreetmap.org/node/123")

	def test_osm_in_a_country_and_an_unknown_trade(self):
		with patch("requests.get") as get, patch("requests.post", return_value=Resp(200, data={"elements": []})) as post:
			self.assertEqual(keyed(discovery.OpenStreetMap).search(query=QUERY), [])
		get.assert_not_called()  # no city: the country's area by ISO code, no geocoding
		self.assertIn('area["ISO3166-1"="MX"][admin_level=2]->.a;', post.call_args.kwargs["data"]["data"])
		with self.assertRaises(tools.ToolRefused):
			keyed(discovery.OpenStreetMap).search(query=dict(QUERY, keywords=["industrial valves"], category=""))

	def test_osm_busy_servers_raise(self):
		with patch("requests.post", return_value=Resp(429, data={})), self.assertRaises(RuntimeError):
			discovery.osm_search(QUERY)

	def test_dataforseo(self):
		organic = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": [
			{"type": "organic", "domain": "www.importadoraplaya.mx", "url": "https://www.importadoraplaya.mx/",
				"title": "Importadora Playa - Importador de juguetes de playa", "description": "Mayoreo y menudeo"},
			{"type": "paid", "domain": "ads.example", "url": "https://ads.example/"}]}]}]}
		maps = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": [
			{"type": "maps_search", "title": "Juguetes Lupita", "cid": "12345", "phone": "+52 33 1111 2222",
				"category": "Toy store", "address_info": {"city": "Zapopan"}}]}]}]}
		with patch("requests.post", side_effect=[Resp(200, data=organic), Resp(200, data=maps)]) as post:
			out = keyed(discovery.DataForSEO, "secret-pw", account_id="login@ours.example").search(
				query=dict(QUERY, city="Guadalajara", limit=2))
		self.assertEqual([(c["name"], c["domain"], c["external_id"], c["buyer_type"]) for c in out],
			[("Importadora Playa", "importadoraplaya.mx", None, "Importer"),
			("Juguetes Lupita", None, "dataforseo:cid:12345", "Retailer")])
		self.assertEqual(out[1]["city"], "Zapopan")
		auth = post.call_args_list[0].kwargs["headers"]["Authorization"]
		self.assertEqual(base64.b64decode(auth.split()[1]).decode(), "login@ours.example:secret-pw")
		task = post.call_args_list[0].kwargs["json"][0]
		self.assertEqual((task["location_name"], task["language_code"]), ("Mexico", "es"))
		with patch("requests.post", return_value=Resp(200, data={"status_code": 40100,
			"status_message": "Not authorized"})), \
				self.assertRaises(RuntimeError):
			keyed(discovery.DataForSEO, "a:b").search(query=QUERY)

	def test_scavio(self):
		def post(url, **kw):
			if url.endswith("/maps/search"):
				return Resp(200, data={"local_results": [{"title": "Mayorista El Sol", "website": "https://elsol.mx",
					"place_id": "ChIJ1", "type": "Wholesaler", "phone": "+52 1"}]})
			return Resp(200, data={"organic_results": [{"title": "Facebook", "link": "https://facebook.com/x"},
				{"title": "Importaciones Rio | Importador", "link": "https://importacionesrio.mx/"}]})

		with patch("requests.post", side_effect=post) as p:
			out = keyed(discovery.Scavio).search(query=dict(QUERY, city="Monterrey", limit=2))
		self.assertEqual([(c["name"], c["domain"], c["external_id"]) for c in out],
			[("Importaciones Rio", "importacionesrio.mx", None), ("Mayorista El Sol", "elsol.mx", "gmaps:ChIJ1")])
		self.assertEqual(p.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer test-key")
		with patch("requests.post", return_value=Resp(402, data={})), self.assertRaises(RuntimeError):
			keyed(discovery.Scavio).search(query=QUERY)

	def test_tinyfish(self):
		results = {"results": [{"url": "https://www.linkedin.com/company/x", "title": "X"},
			{"url": "https://distribuidoraluna.com.mx/", "title": "Distribuidora Luna", "snippet": "Distribuidor de juguetes"}]}
		with patch("requests.get", return_value=Resp(200, data=results)) as get:
			out = keyed(discovery.TinyFish).search(query=dict(QUERY, limit=1))
		self.assertEqual([(c["name"], c["buyer_type"], c["source"]) for c in out], [("Distribuidora Luna", "Distributor",
			"TinyFish")])
		self.assertEqual(get.call_args.kwargs["headers"]["X-API-Key"], "test-key")
		self.assertEqual(get.call_args.kwargs["params"], {"query": "importador de juguetes México", "page": 0})

	def test_volza_csv_is_aggregated_per_importer(self):
		content = "\n".join((
			"Volza export - HS 9503 - Mexico,,,,,,,",
			"Date,HS Code,Product Description,Importer Name,Importer Country,Quantity,Value (USD),Exporter Name",
			'2026-03-01,95030090,PLASTIC WATER GUN TOYS,JUGUETES DEL NORTE SA DE CV,Mexico,1200,"3,450.50",NINGBO TOYS',
			'2026-04-12,95030090,PLASTIC WATER GUN TOYS,JUGUETES DEL NORTE SA DE CV,Mexico,800,"2,100.00",NINGBO TOYS',
			"2026-05-02,95030010,BEACH TOYS,Juguetes del Norte SA de CV,MX,300,900,SHANTOU TOYS",
			"2026-05-20,95059000,HOLI COLOUR POWDER,FIESTAS ALEGRES SA,Mexico,50,400,OUR COMPANY",
			",,,,,,,",
		))
		f = frappe.get_doc({"doctype": "File", "file_name": "qa_volza_export.csv", "content": content,
			"is_private": 1}).insert(
			ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("File", f.name, force=True, ignore_permissions=True))
		frappe.flags.excom_ai_fake_tools = True
		try:
			out = tools.use("volza", "import_file", file_url=f.file_url)
		finally:
			frappe.flags.excom_ai_fake_tools = False
		by = {c["name"]: c for c in out}
		self.assertEqual(set(by), {"JUGUETES DEL NORTE SA DE CV", "FIESTAS ALEGRES SA"})
		norte = by["JUGUETES DEL NORTE SA DE CV"]
		self.assertEqual((norte["source"], norte["buyer_type"], norte["country"]), ("Volza", "Importer", "Mexico"))
		self.assertEqual(norte["signals"]["IMPORTS_CATEGORY"], "HS 950300: plastic water gun toys")
		self.assertEqual(norte["signals"]["SIZE_HINT"], "3 shipments, USD 6,450 (2026-03-01 to 2026-05-02)")
		self.assertEqual((norte["import_summary"]["shipments"], norte["import_summary"]["quantity"]), (3, 2300.0))
		self.assertEqual(norte["external_id"], "volza:juguetes-del-norte-sa-de-cv:mx")
		self.assertEqual(by["FIESTAS ALEGRES SA"]["signals"]["IMPORTS_CATEGORY"], "HS 950590: holi colour powder")

	def test_import_rows_normalises_columns(self):
		rows = [
			{"Company Name": "Distribuidora Sol", "Web Site": "www.dsol.com.co", "País": "Colombia",
				"E-mail": "ventas@dsol.com.co"},
			{"Exhibitor": "Toys Plus", "Country": "CL", "Email": "toysplus@gmail.com", "Booth": "A12"},
			{"Company Name": "Distribuidora Sol S.A.S.", "Website": "https://dsol.com.co/es", "Country": "Colombia",
				"Email": "compras@dsol.com.co"},
			{"Notes": "a row without a company"},
		]
		out = discovery.import_rows(rows)
		self.assertEqual([(c["name"], c["domain"], c["country"], c["source"], c["buyer_type"]) for c in out],
			[("Distribuidora Sol", "dsol.com.co", "Colombia", "Import", "Distributor"), ("Toys Plus", None, "Chile",
				"Import", None)])
		self.assertEqual(out[0]["emails"], ["ventas@dsol.com.co", "compras@dsol.com.co"])
		self.assertEqual(out[0]["website"], "https://www.dsol.com.co")

	def test_every_fake_has_the_company_shape(self):
		keys = {"name", "domain", "website", "country", "city", "phone", "source", "source_url", "external_id", "industry",
			"buyer_type", "signals"}
		frappe.flags.excom_ai_fake_tools = True
		try:
			for key, source in (("osm", "OSM"), ("serper", "Serper"), ("scavio", "Scavio"), ("dataforseo", "DataForSEO"),
					("tinyfish", "TinyFish")):
				with self.subTest(key=key):
					out = tools.use(key, "search", query=dict(QUERY, countries=["Mexico", "Colombia"]))
					self.assertEqual(len(out), 4)
					self.assertTrue(all(keys <= set(c) and c["source"] == source for c in out))
					self.assertTrue(all(c["buyer_type"] in discovery.BUYER_TYPES for c in out))
		finally:
			frappe.flags.excom_ai_fake_tools = False


class TestUse(FrappeTestCase):
	"""tools.use's limits, against the seeded Serper record (restored afterwards)."""

	KEY = "serper"

	def setUp(self):
		frappe.flags.excom_ai_fake_tools = False
		self.saved = frappe.db.get_value("Excom AI Tool", self.KEY, ["enabled", "monthly_budget", "used_this_month",
			"rate_limit", "last_error"], as_dict=True)
		frappe.cache.set_value(f"excom_ai_tool_month:{self.KEY}", str(get_first_day(nowdate())))
		self.addCleanup(self.restore)

	def restore(self):
		frappe.flags.excom_ai_fake_tools = False
		frappe.db.set_value("Excom AI Tool", self.KEY, dict(self.saved), update_modified=False)
		remove_encrypted_password("Excom AI Tool", self.KEY, "api_key")

	def arm(self, budget=1.0, used=0.0, key=True):
		frappe.db.set_value("Excom AI Tool", self.KEY, {"enabled": 1, "monthly_budget": budget, "used_this_month": used,
			"rate_limit": 0, "last_error": None}, update_modified=False)
		if key:
			set_encrypted_password("Excom AI Tool", self.KEY, "test-key", "api_key")

	def serp(self):
		return patch("requests.post", return_value=Resp(200, data={"organic": [{"title": "Juguetes del Norte",
			"link": "https://juguetesdelnorte.com.mx/"}]}))

	def test_a_disabled_tool_is_refused(self):
		frappe.db.set_value("Excom AI Tool", self.KEY, "enabled", 0, update_modified=False)
		with self.serp() as post, self.assertRaises(tools.ToolRefused):
			tools.use(self.KEY, "search", query=QUERY)
		post.assert_not_called()

	def test_a_paid_tool_without_a_key_is_refused(self):
		self.arm(key=False)
		with self.serp() as post, self.assertRaises(tools.ToolRefused):
			tools.use(self.KEY, "search", query=QUERY)
		post.assert_not_called()

	def test_the_budget_is_counted_and_kept(self):
		self.arm(budget=0.005, used=0.0)
		with self.serp():
			out = tools.use(self.KEY, "search", query=QUERY, cost=discovery.estimate(self.KEY, QUERY))
		self.assertEqual(out[0]["domain"], "juguetesdelnorte.com.mx")
		self.assertAlmostEqual(flt(frappe.db.get_value("Excom AI Tool", self.KEY, "used_this_month")), 0.002)
		with self.serp() as post, self.assertRaises(tools.ToolRefused):
			tools.use(self.KEY, "search", query=QUERY, cost=0.004)  # 0.002 + 0.004 > 0.005
		post.assert_not_called()
		self.assertAlmostEqual(flt(frappe.db.get_value("Excom AI Tool", self.KEY, "used_this_month")), 0.002)

	def test_an_error_is_recorded_on_the_tool(self):
		self.arm()
		with patch("requests.post", return_value=Resp(500, data={})), self.assertRaises(requests.HTTPError):
			tools.use(self.KEY, "search", query=QUERY)
		self.assertIn("HTTPError", frappe.db.get_value("Excom AI Tool", self.KEY, "last_error"))

	def test_fake_mode_needs_no_record_and_spends_nothing(self):
		frappe.db.set_value("Excom AI Tool", self.KEY, {"enabled": 0, "used_this_month": 0}, update_modified=False)
		frappe.flags.excom_ai_fake_tools = True
		with patch("requests.post") as post, patch("requests.get") as get:
			found = tools.use(self.KEY, "search", query=QUERY)
			self.assertEqual(tools.use("reoon", "verify", email="catchall@acme.mx")["status"], "Risky")
			self.assertEqual(tools.use("zerobounce", "verify", email="ventas@acme.mx")["status"], "Verified")
			self.assertEqual(tools.use("hunter", "verify", email="invalid@acme.mx")["status"], "Invalid")
			self.assertEqual(tools.use("hunter", "domain_search", domain="acme.mx")[0]["source"], "Hunter")
			self.assertEqual(tools.use("precleared", "check", email="ventas@acme.de", country="Germany")["verdict"],
				"OK to email")
			self.assertEqual(tools.use("precleared", "check", email="hans@acme.de", country="Germany")["verdict"],
				"Consent needed")
			self.assertTrue(tools.use("trulyinbox", "status", mailbox_email="ventas@ours.example")["warming"])
			self.assertTrue(tools.use("mailbox_health", "check", domain="ours.example")["ok"])
			self.assertEqual(tools.use("web_reader", "profile", url="https://acme.mx")["language"], "es")
		post.assert_not_called()
		get.assert_not_called()
		self.assertTrue(found and found[0]["source"] == "Serper")
		self.assertEqual(flt(frappe.db.get_value("Excom AI Tool", self.KEY, "used_this_month")), 0)

	def test_every_seeded_tool_has_an_adapter(self):
		from excom.excom_ai.constants import TOOLS

		tools.load()
		for key, _label, tier, _note in TOOLS:
			with self.subTest(key=key):
				self.assertIn(key, tools.TOOLS)
				self.assertEqual(tools.TOOLS[key].tier, tier)
