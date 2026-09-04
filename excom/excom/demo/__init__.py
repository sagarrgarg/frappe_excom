"""A demo sales organisation for walking the assignment and visibility flow end to end.

Everything these modules create is prefixed "Demo " or uses an @example.com address, and org.teardown()
removes all of it. Nothing here sends a message. Not imported by the app at runtime.

	bench --site <site> execute excom.excom.demo.org.build
	bench --site <site> execute excom.excom.demo.flow.stage1
	bench --site <site> execute excom.excom.demo.org.teardown
"""
