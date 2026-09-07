"""One Source list: every existing Lead Source (v15) / UTM Source (v16) without a matching Excom Source
gets one (type Manual, or Channel for the 'Organic …' rows), so Admin → Sources shows everything and the
attribution master becomes a mirror. Idempotent.

The work itself lives in excom.setup so a fresh install — which marks this patch completed without
running it — builds the same list from after_install."""


def execute():
	from excom.setup import mirror_attribution_sources

	print(f"sources unified: {mirror_attribution_sources()} created")
