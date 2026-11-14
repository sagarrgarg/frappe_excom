"""The few lines the service says on its own (errors, limits, silence).

Every table covers the same languages. A line is only ever spoken in a language the TTS has a
voice for: the call picks its speech language first (CallSession._speech_language), so an
English voice never reads Hindi or Portuguese text; it reads the English line instead.
"""

from __future__ import annotations

APOLOGY = {
    "en": "Sorry, I'm having trouble. A colleague will call you back.",
    "es": "Lo siento, estoy teniendo problemas. Un compañero le devolverá la llamada.",
    "pt": "Desculpe, estou com um problema. Um colega vai retornar a sua ligação.",
    "hi": "माफ़ कीजिए, मुझे कुछ दिक्कत हो रही है। हमारे सहयोगी आपको वापस कॉल करेंगे।",
    "fr": "Désolé, je rencontre un problème. Un collègue vous rappellera.",
    "de": "Entschuldigung, ich habe gerade Probleme. Ein Kollege ruft Sie zurück.",
    "it": "Mi scusi, ho un problema. Un collega la richiamerà.",
    "nl": "Excuses, ik heb een probleem. Een collega belt u terug.",
    "ja": "申し訳ありません、問題が発生しました。担当者から折り返しお電話いたします。",
}

GOODBYE = {
    "en": "We're out of time for this call. A colleague will follow up with you. Goodbye.",
    "es": "Se nos acabó el tiempo de esta llamada. Un compañero le dará seguimiento. Adiós.",
    "pt": "O tempo desta ligação acabou. Um colega vai entrar em contato com você. Até logo.",
    "hi": "इस कॉल का समय समाप्त हो गया है। हमारे सहयोगी आपसे संपर्क करेंगे। धन्यवाद।",
    "fr": "Le temps de cet appel est écoulé. Un collègue vous recontactera. Au revoir.",
    "de": "Die Zeit für diesen Anruf ist um. Ein Kollege meldet sich bei Ihnen. Auf Wiederhören.",
    "it": "Il tempo per questa chiamata è terminato. Un collega la ricontatterà. Arrivederci.",
    "nl": "De tijd voor dit gesprek is om. Een collega neemt contact met u op. Tot ziens.",
    "ja": "通話時間の上限に達しました。担当者から改めてご連絡いたします。失礼いたします。",
}

STILL_THERE = {
    "en": "Are you still there?",
    "es": "¿Sigue ahí?",
    "pt": "Você ainda está aí?",
    "hi": "क्या आप अभी भी लाइन पर हैं?",
    "fr": "Êtes-vous toujours là ?",
    "de": "Sind Sie noch da?",
    "it": "È ancora in linea?",
    "nl": "Bent u er nog?",
    "ja": "もしもし、お電話聞こえていますか？",  # noqa: RUF001 (Japanese punctuation)
}

IDLE_GOODBYE = {
    "en": "I can't hear you, so I'll end the call now. A colleague will call you back. Goodbye.",
    "es": ("No le escucho, así que voy a terminar la llamada. Un compañero le devolverá la llamada. Adiós."),
    "pt": (
        "Não estou ouvindo você, então vou encerrar a ligação. Um colega vai retornar a sua ligação. "
        "Até logo."
    ),
    "hi": ("आपकी आवाज़ नहीं आ रही है, इसलिए यह कॉल समाप्त की जा रही है। हमारे सहयोगी आपको वापस कॉल करेंगे। धन्यवाद।"),
    "fr": "Je ne vous entends plus, je vais donc raccrocher. Un collègue vous rappellera. Au revoir.",
    "de": (
        "Ich höre Sie leider nicht mehr und beende den Anruf. Ein Kollege ruft Sie zurück. Auf Wiederhören."
    ),
    "it": "Non la sento più, quindi chiudo la chiamata. Un collega la richiamerà. Arrivederci.",
    "nl": "Ik hoor u niet meer, dus ik beëindig het gesprek. Een collega belt u terug. Tot ziens.",
    "ja": (
        "お声が聞こえないため、お電話を終了いたします。担当者から折り返しお電話いたします。失礼いたします。"
    ),
}


def base_language(language: str) -> str:
    return (language or "en").split("-")[0].lower()


def _pick(table: dict[str, str], language: str) -> str:
    return table.get(base_language(language), table["en"])


def apology(language: str) -> str:
    return _pick(APOLOGY, language)


def goodbye(language: str) -> str:
    return _pick(GOODBYE, language)


def still_there(language: str) -> str:
    return _pick(STILL_THERE, language)


def idle_goodbye(language: str) -> str:
    return _pick(IDLE_GOODBYE, language)
