# apps/zatca_vat_report/zatca_vat_report/boot.py
"""Expose settings the desk client needs, without a per-form round trip."""

import frappe

from zatca_vat_report.cost_center import is_enabled


def extend_bootinfo(bootinfo):
	"""Called as `hook(bootinfo=bootinfo)` from `frappe/sessions.py:171`.

	`frappe.boot` is built once per session, so a freshly ticked checkbox reaches
	the client only after a desk reload. That is deliberate and harmless: the
	client script is cosmetic, and `cost_center.set_missing_cost_center` re-reads
	the flag server-side on every save.
	"""
	bootinfo.zatca_vat_report = frappe._dict(sync_row_cost_center=1 if is_enabled() else 0)
