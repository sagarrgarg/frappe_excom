"""Deterministic names for Ads records.

Every mirror and fact row is named from its natural key, so a re-sync or a retried job lands on the
same row instead of creating a second one (acceptance test 13: re-sync never duplicates).
"""

import hashlib


def account_name(provider: str, external_id: str) -> str:
	return f"{provider}-{external_id}"


def mirror_name(account: str, external_id: str) -> str:
	return f"{account}-{external_id}"


def snapshot_name(account: str, level: str, entity_id: str, date, breakdown_key: str = "none", breakdown_value: str = "", attribution_key: str = "default") -> str:
	key = "|".join([account, level, str(entity_id), str(date), breakdown_key or "none", breakdown_value or "", attribution_key or "default"])
	return hashlib.sha1(key.encode()).hexdigest()


def touch_name(touch_source: str, observation_id: str) -> str:
	"""Ads Attribution Touch: one row per observation (intake log key, WhatsApp message ID, order)."""
	return hashlib.sha1(f"{touch_source}|{observation_id}".encode()).hexdigest()
