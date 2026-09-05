# Copyright (c) 2026, Aravind R and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ZATCAAccountGroup(Document):
	def validate(self):
		"""Average the linked accounts' tax rates onto the group.

		🔴 BLANK GRID ROWS ARE THE NORMAL CASE, NOT AN EDGE CASE. The desk appends
		an empty row the moment somebody clicks into the child table, and it posts as
		`{"account": "", "account_tax_rate": ""}`. Two things went wrong with that:

		1. `tax_rate_sum += row.account_tax_rate` raised
		   `TypeError: unsupported operand type(s) for +=: 'float' and 'str'`, so the
		   document could not be saved at all — hit on ZATCA Account Group "Zero Rated
		   Sales", yht-khobhar.enfonoerp.com, 2026-09-05.
		2. Even coerced, an accountless row still counted toward `len()`, so one real
		   15% account plus one blank row averaged to 7.5. A silently halved rate is
		   worse than the crash, because the document saves and the number looks real.

		`flt()` rather than `float()` — the value arrives from a form as `""` or None,
		and `float("")` raises where `flt("")` is 0 (Frappe's own rule: never coerce
		user input with the builtins).

		Rows with no account are dropped rather than kept, because a linked-account row
		that links to nothing has no meaning and would otherwise reach the report as an
		empty string inside `account_head IN (...)`.
		"""
		rows = [row for row in (self.linked_accounts or []) if row.account]

		if len(rows) != len(self.linked_accounts or []):
			self.linked_accounts = rows
			for idx, row in enumerate(self.linked_accounts, start=1):
				row.idx = idx

		if not rows:
			frappe.throw(_("At least one linked account is required in the account group."))

		self.tax_rate = flt(sum(flt(row.account_tax_rate) for row in rows) / len(rows))
