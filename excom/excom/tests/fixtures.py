"""Shared helpers for tests that create users.

Creating a `User` is not a contained act. Other installed apps hook `User.after_insert` and write a
record named after the email — Drive does exactly this, without `ignore_if_duplicate` — and those
writes are not inside the test's transaction. `FrappeTestCase` rolls back per class, so the user
disappears at the end of a run and the satellite record does not.

The next run then creates the same user, the same hook fires, and the insert dies on a duplicate
primary key. That exception happens inside `setUpClass`, so it does not fail one test: it aborts the
whole class. Four Excom modules were failing this way, differently on each run, which is what a
leaked fixture looks like from the outside.

Call `purge_user()` before creating a test user, and the run starts from the same place every time.
"""

import frappe

#: Doctypes elsewhere in the bench that are named after a user and created by a hook on User.
#: Discovered rather than listed, because which apps are installed is not ours to know.
_PER_USER_CACHE: list[str] | None = None


def per_user_doctypes() -> list[str]:
	"""Every doctype whose primary key is a user's name."""
	global _PER_USER_CACHE
	if _PER_USER_CACHE is not None:
		return _PER_USER_CACHE

	found = []
	for name in frappe.get_all("DocType", filters={"issingle": 0}, pluck="name"):
		try:
			meta = frappe.get_meta(name)
		except Exception:
			continue
		if meta.autoname and "field:user" in str(meta.autoname):
			found.append(name)
	_PER_USER_CACHE = found
	return found


def purge_user(email: str) -> None:
	"""Remove a test user and everything named after them, so the next insert is clean."""
	if not email:
		return
	for doctype in per_user_doctypes():
		try:
			if frappe.db.exists(doctype, email):
				frappe.delete_doc(
					doctype, email, force=True, ignore_permissions=True, delete_permanently=True
				)
		except Exception:
			# A satellite we cannot remove is worth carrying on past: the insert may still succeed,
			# and failing here would hide whatever the test was actually about.
			continue
	try:
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	except Exception:
		pass


def purge_users(*emails: str) -> None:
	for email in emails:
		purge_user(email)
