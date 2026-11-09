"""Regions, languages and timezones the builder understands (HLD-007 §6.1).

ERPNext has no region tree to lean on (GGIL has only "India" and "Rest Of The World"), and every
customer's language is "en", so this map ships with the module. Country names are Frappe's own
(frappe/geo/country_info.json); currencies and the full timezone lists come from there too.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

import frappe

LATAM_ES = ["Mexico", "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Costa Rica", "Panama", "Colombia",
	"Venezuela, Bolivarian Republic of", "Ecuador", "Peru", "Bolivia, Plurinational State of", "Chile", "Argentina",
	"Uruguay", "Paraguay", "Cuba", "Dominican Republic", "Puerto Rico"]
EU = ["Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland", "France",
	"Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands",
	"Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"]

REGIONS: dict[str, list[str]] = {
	"latin america": ["Mexico", "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Costa Rica", "Panama", "Colombia",
		"Venezuela, Bolivarian Republic of", "Ecuador", "Peru", "Bolivia, Plurinational State of", "Chile", "Argentina",
		"Uruguay", "Paraguay", "Brazil"],
	"central america": ["Belize", "Guatemala", "Honduras", "El Salvador", "Nicaragua", "Costa Rica", "Panama"],
	"south america": ["Colombia", "Venezuela, Bolivarian Republic of", "Ecuador", "Peru", "Bolivia, Plurinational State of",
		"Chile", "Argentina", "Uruguay", "Paraguay", "Brazil", "Guyana", "Suriname"],
	"caribbean": ["Cuba", "Dominican Republic", "Puerto Rico", "Jamaica", "Haiti", "Trinidad and Tobago"],
	"eu": EU,
	"europe": [*EU, "United Kingdom", "Switzerland", "Norway"],
	"gcc": ["Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman"],
	"middle east": ["Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman", "Israel", "Jordan",
		"Lebanon", "Iraq", "Iran", "Türkiye", "Egypt"],
	"anz": ["Australia", "New Zealand"],
	"north america": ["United States", "Canada", "Mexico"],
	"usa": ["United States"],
	"uk": ["United Kingdom"],
	"asean": ["Singapore", "Malaysia", "Indonesia", "Thailand", "Vietnam", "Philippines"],
	"south asia": ["India", "Bangladesh", "Sri Lanka", "Nepal", "Pakistan"],
	"africa": ["South Africa", "Nigeria", "Kenya", "Egypt", "Morocco", "Angola", "Mozambique"],
}
REGION_SYNONYMS = {
	"latam": "latin america", "latinoamerica": "latin america", "latinoamérica": "latin america",
	"european union": "eu", "gulf": "gcc", "middle-east": "middle east", "australia and new zealand": "anz",
	"us": "usa", "united states": "usa", "america": "usa", "united kingdom": "uk", "britain": "uk",
	"south east asia": "asean", "southeast asia": "asean",
}

# Language code (Frappe's Language names) -> where it is spoken as the business language.
LANGUAGES: dict[str, dict] = {
	"es": {"name": "Spanish", "words": ("spanish", "español", "espanol", "castellano"),
		"countries": [*LATAM_ES, "Spain", "Equatorial Guinea"]},
	"pt": {"name": "Portuguese", "words": ("portuguese", "português", "portugues"),
		"countries": ["Brazil", "Portugal", "Angola", "Mozambique"]},
	"en": {"name": "English", "words": ("english",),
		"countries": ["United States", "United Kingdom", "Canada", "Australia", "New Zealand", "Ireland", "Singapore",
			"South Africa", "Nigeria", "Kenya", "India", "Jamaica", "Trinidad and Tobago", "Belize", "Guyana"]},
	"fr": {"name": "French", "words": ("french", "français", "francais"),
		"countries": ["France", "Belgium", "Switzerland", "Luxembourg", "Canada", "Morocco", "Haiti"]},
	"de": {"name": "German", "words": ("german", "deutsch"), "countries": ["Germany", "Austria", "Switzerland", "Luxembourg"]},
	"it": {"name": "Italian", "words": ("italian", "italiano"), "countries": ["Italy", "Switzerland", "Malta"]},
	"nl": {"name": "Dutch", "words": ("dutch", "nederlands"), "countries": ["Netherlands", "Belgium", "Suriname"]},
	"ar": {"name": "Arabic", "words": ("arabic", "عربي"),
		"countries": ["Saudi Arabia", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain", "Oman", "Egypt", "Jordan",
			"Lebanon", "Iraq", "Morocco"]},
	"hi": {"name": "Hindi", "words": ("hindi", "हिंदी", "हिन्दी"), "countries": ["India"]},
	"ru": {"name": "Russian", "words": ("russian", "русский"), "countries": ["Russian Federation"]},
	"zh": {"name": "Chinese", "words": ("chinese", "mandarin", "中文"), "countries": ["China", "Singapore"]},
	"ja": {"name": "Japanese", "words": ("japanese", "日本語"), "countries": ["Japan"]},
	"tr": {"name": "Turkish", "words": ("turkish", "türkçe"), "countries": ["Türkiye"]},
}

# The timezone a business day is planned in, where the country has several.
MAIN_TZ = {
	"Mexico": "America/Mexico_City", "Brazil": "America/Sao_Paulo", "United States": "America/New_York",
	"Canada": "America/Toronto", "Australia": "Australia/Sydney", "Argentina": "America/Argentina/Buenos_Aires",
	"Chile": "America/Santiago", "Ecuador": "America/Guayaquil", "Spain": "Europe/Madrid", "Portugal": "Europe/Lisbon",
	"Russian Federation": "Europe/Moscow", "Indonesia": "Asia/Jakarta", "New Zealand": "Pacific/Auckland",
	"Germany": "Europe/Berlin", "China": "Asia/Shanghai", "Kazakhstan": "Asia/Almaty", "Colombia": "America/Bogota",
	"Peru": "America/Lima", "Bolivia, Plurinational State of": "America/La_Paz", "Paraguay": "America/Asuncion",
	"Venezuela, Bolivarian Republic of": "America/Caracas", "United Kingdom": "Europe/London", "India": "Asia/Kolkata",
}


@lru_cache(maxsize=1)
def country_info() -> dict:
	with open(os.path.join(frappe.get_app_path("frappe"), "geo", "country_info.json")) as f:
		return json.load(f)


def main_timezone(country: str) -> str:
	if country in MAIN_TZ:
		return MAIN_TZ[country]
	zones = (country_info().get(country) or {}).get("timezones") or []
	return zones[0] if zones else ""


def currency_of(country: str) -> str:
	return (country_info().get(country) or {}).get("currency") or ""


def region(word: str) -> tuple[str, list[str]] | None:
	key = REGION_SYNONYMS.get(word.strip().lower(), word.strip().lower())
	return (key, REGIONS[key]) if key in REGIONS else None


def language(word: str) -> tuple[str, dict] | None:
	w = word.strip().lower()
	for code, lang in LANGUAGES.items():
		if w == code or w in lang["words"]:
			return code, lang
	return None


def languages_of(country: str) -> list[str]:
	return [code for code, lang in LANGUAGES.items() if country in lang["countries"]]
