"""Every `from excom… import name` in the app resolves.

A function deleted by mistake is only noticed when its caller runs — imports inside functions are
not checked at load time. (HLD-005 Phase 0 lost the media upload functions this way.)
"""

import glob
import importlib
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_IMPORT = re.compile(r"from (excom\.[\w.]+) import \(?([\w ,\n]+?)\)?\n")


class TestImportTargets(unittest.TestCase):
	def test_every_excom_import_resolves(self):
		missing = []
		for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
			with open(path) as f:
				source = f.read()
			for module, names in _IMPORT.findall(source):
				mod = importlib.import_module(module)
				for name in [n.strip().split(" as ")[0] for n in names.replace("\n", " ").split(",") if n.strip()]:
					if hasattr(mod, name):
						continue
					try:
						importlib.import_module(f"{module}.{name}")
					except ImportError:
						missing.append(f"{os.path.relpath(path, ROOT)}: {module}.{name}")
		self.assertEqual(missing, [])
