# Copyright (c) 2026, Aravind R and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ZATCAAccountGroup(Document):
	def validate(self):
		"""Average the linked accounts' tax rates onto the group.

		🔴 BLANK GRID ROWS ARE THE NORMAL CASE, NOT AN EDGE CASE. The desk appends
		an empty row the moment somebody clicks into the child table, and it posts as
		`{"account": "", "account_tax_rate": ""}`. Two things went wrong with that:

		1. `tax_rate_sum += row.account_tax_rate` raised
		   `TypeError: unsupported operand type(s) for +=: 'float' and 'str'`.
		2. Even coerced, an accountless row still counted toward `len()`, so one real
		   15% account plus one blank row averaged to 7.5 — saved cleanly, looked real.

		`flt()` rather than `float()`: the value arrives from a form as `""` or None,
		and `float("")` raises where `flt("")` is 0.

		🔴 THE "AT LEAST ONE LINKED ACCOUNT" THROW WAS REMOVED ON REQUEST — a group
		may now be saved with none. That is only safe because `get_account_group_map`
		SKIPS accountless groups. Do not remove that guard: every query in both reports
		drops its account filter when the list is empty (`if accounts:` at four call
		sites), so such a group would not report nothing, it would match EVERY tax row,
		journal entry and expense claim — a zero-rated group would swallow the ledger.
		"""
		rows = [row for row in (self.linked_accounts or []) if row.account]

		if len(rows) != len(self.linked_accounts or []):
			self.linked_accounts = rows
			for idx, row in enumerate(self.linked_accounts, start=1):
				row.idx = idx

		self.tax_rate = flt(sum(flt(row.account_tax_rate) for row in rows) / len(rows)) if rows else 0.0
