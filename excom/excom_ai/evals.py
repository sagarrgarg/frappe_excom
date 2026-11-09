"""Evaluation sets for the Sales pack (HLD-007 §13.4).

"Excom Sales reply intents" is the golden set the responder's classification must keep passing
before a prompt, model or effort change goes live (ai_interface `evals.gate`). It starts with the
replies Harvey processed that are on this machine (its local database keeps the text of 9; the
other 32 of its 41 are in its mailbox and join in Phase 1I), and adds written cases so every
intent and the markets' languages are covered. Each case states the intent a person would give it.
"""

from __future__ import annotations

import json

import frappe

SET = "Excom Sales reply intents"

# (name, text, intent). The first group is Harvey's own processed replies, trimmed. (A Ukrainian reply
# is among them, so the Cyrillic letters are meant: noqa RUF001 below.)
CASES = [
	("harvey ooo ticket", "##- Please type your reply above this line -##\n\nHi Harish,\n\nThanks so much for reaching out! This "
		"auto-reply is just to let you know we received your email and will get back to you with a response as soon as possible. "
		"Responses may take up to 24 hours.", "ooo"),
	("harvey not interested 1", "Hello Harish,\n\nThank you for reaching out and for sharing information about your services.\n\n"
		"At this time, we are not interested, but we appreciate you considering us.\n\nWishing you all the best.", "not_interested"),
	("harvey not interested 2", "Hi Harish,\n\nThank you for the additional information. We appreciate you keeping us in mind, but "
		"we are not interested at this time.\n\nWishing you continued success.", "not_interested"),
	("harvey support request", "Your request (76982) has been received and is being reviewed by our support staff.\n\nTo add "
		"additional comments, reply to this email.", "ooo"),
	("harvey half term", "Thanks for your email, I am unavailable for a few days during half term but I am still monitoring my "
		"emails so if your query is urgent I will try and respond as soon as I can.", "ooo"),
	("harvey ukrainian partnership", "Доброго дня! Дякуємо за інтерес до TA•DA!GROUP та зацікавленість в співпраці. Пропозицію щодо "
		"партнерства можна залишити на сайті у розділі «Співпраця».", "ooo"),  # noqa: RUF001
	("harvey french auto", "Bonjour, and merci for writing to Bonjour Fête. Your note is with our team, and you will hear from a "
		"real person within two business days.", "ooo"),
	("harvey pool barn prices", "Hi Harish, thanks for getting in touch. Could you send me your price list for the water guns and "
		"balloons, and what the minimum order would be for shipping to Brisbane?", "negotiate"),
	# written cases
	("interested samples", "Hello, these look interesting for our summer range. Can you send two or three samples to our "
		"warehouse in Leeds?", "interested"),
	("interested call", "Yes, happy to talk. I'm available Thursday afternoon, does a call at 3pm UK time work?", "interested"),
	("question certification", "Are your water guns tested to EN71? We cannot list toys without it.", "question"),
	("question lead time", "How long does production take for a container, roughly?", "question"),
	("objection supplier", "We already buy from a supplier in China and we're happy with them.", "objection"),
	("objection timing", "Our summer buying is finished for this year, maybe come back in September.", "objection"),
	("negotiate discount", "Your prices are higher than what we pay now. Can you do 10% less if we take two containers?", "negotiate"),
	("unsubscribe plain", "Please remove me from your mailing list.", "unsubscribe"),
	("unsubscribe stop", "STOP", "unsubscribe"),
	("wrong person", "I'm not the right person for this, you should write to our buyer Sarah at purchasing.", "wrong_person"),
	("escalate spam", "This is spam. I never gave you my address and I will report you under GDPR.", "escalate"),
	("escalate bot", "Is this an automated email? Am I talking to a bot?", "escalate"),
	# Spanish and Portuguese, the Latin American desk's languages
	("es interesado", "Buenas tardes, nos interesan las pistolas de agua para la temporada. ¿Nos puede enviar el catálogo con "
		"fotos?", "interested"),
	("es precio", "Gracias. ¿Cuál es el precio por pieza y el pedido mínimo para enviar a Monterrey?", "negotiate"),
	("es no gracias", "Muchas gracias, por ahora no estamos interesados.", "not_interested"),
	("es baja", "Por favor denme de baja de su lista, no me escriban más.", "unsubscribe"),
	("es persona equivocada", "Yo no veo compras, escríbale a Juan Pérez del área de importaciones.", "wrong_person"),
	("es fuera de oficina", "Respuesta automática: estoy fuera de la oficina hasta el lunes 15. Para temas urgentes llame al "
		"conmutador.", "ooo"),
	("pt interessado", "Olá, temos interesse. Vocês exportam para o Brasil? Qual o prazo de entrega?", "question"),
]


def seed() -> None:
	"""Create the set once (ai_interface installed); cases a person changed are kept."""
	if "ai_interface" not in frappe.get_installed_apps() or not frappe.db.table_exists("AI Eval Set"):
		return
	if frappe.db.exists("AI Eval Set", SET):
		return
	doc = frappe.get_doc({
		"doctype": "AI Eval Set", "eval_name": SET, "calling_app": "excom", "action": "classify_reply",
		"target_type": "Callable", "target": "excom.excom_ai.responder.eval_classify", "pass_threshold": 90,
		"cases": [{"case_name": name, "enabled": 1, "input": json.dumps({"text": text}, ensure_ascii=False),
			"expected": json.dumps({"intent": intent})} for name, text, intent in CASES],
	})
	doc.insert(ignore_permissions=True)
