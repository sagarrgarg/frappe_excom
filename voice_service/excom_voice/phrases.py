"""The few lines the service says on its own (errors and limits)."""

from __future__ import annotations

APOLOGY = {
    "en": "Sorry, I'm having trouble. A colleague will call you back.",
    "es": "Lo siento, estoy teniendo problemas. Un compañero le devolverá la llamada.",
    "hi": "माफ़ कीजिए, मुझे कुछ दिक्कत हो रही है। हमारे सहयोगी आपको वापस कॉल करेंगे।",
    "fr": "Désolé, je rencontre un problème. Un collègue vous rappellera.",
    "de": "Entschuldigung, ich habe gerade Probleme. Ein Kollege ruft Sie zurück.",
}

GOODBYE = {
    "en": "We're out of time for this call. A colleague will follow up with you. Goodbye.",
    "es": "Se nos acabó el tiempo de esta llamada. Un compañero le dará seguimiento. Adiós.",
    "hi": "इस कॉल का समय समाप्त हो गया है। हमारे सहयोगी आपसे संपर्क करेंगे। धन्यवाद।",
    "fr": "Le temps de cet appel est écoulé. Un collègue vous recontactera. Au revoir.",
    "de": "Die Zeit für diesen Anruf ist um. Ein Kollege meldet sich bei Ihnen. Auf Wiederhören.",
}


def _pick(table: dict[str, str], language: str) -> str:
    return table.get((language or "en").split("-")[0].lower(), table["en"])


def apology(language: str) -> str:
    return _pick(APOLOGY, language)


def goodbye(language: str) -> str:
    return _pick(GOODBYE, language)
