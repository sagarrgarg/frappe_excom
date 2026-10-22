"""Manual ad networks: no connector. Daily figures arrive through Ads Metric Import (imports.py).

Registered so that code asking an account for its provider gets a clear answer instead of an
error: every operation is declared unsupported with the reason. sync.run_job and the scheduler
skip Manual accounts before they ever get here.
"""

from excom.excom_ads.providers.base import INVALID, AdsProvider, AdsProviderError, Capability

REASON = "Manual account: figures come from CSV / XLSX imports (Ads Metric Import), not a connector."


class ManualProvider(AdsProvider):
	provider = "Manual"
	CAPABILITIES = {op: Capability(False, reason=REASON) for op in ("read_account", "list_campaigns", "list_ad_groups", "list_ads", "daily_metrics", "read_billing", "read_leads", "pause_entity", "change_budget")}

	def _refuse(self, *_a, **_k):
		raise AdsProviderError(REASON, INVALID)

	read_account = list_campaigns = list_ad_groups = list_ads = daily_metrics = read_billing = _refuse
