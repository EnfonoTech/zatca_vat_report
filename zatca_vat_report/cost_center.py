# apps/zatca_vat_report/zatca_vat_report/cost_center.py
"""Copy the parent Cost Center into Item and Tax rows that have none.

Disabled by default. Enable per site with the "Sync Cost Center to Item and Tax
Rows" check on ZATCA VAT Report Settings. That is a Single DocType, so its value
lives in the site's own `tabSingles` — ticking it on one site never affects
another, even though this app ships everywhere.

Why this exists
---------------
ERPNext appends tax rows from a Taxes and Charges Template *programmatically*
(`erpnext/controllers/accounts_controller.py::append_taxes_from_master` just
extends `taxes` with the template rows verbatim). Two consequences:

1. The client-side `taxes_add` grid event never fires for those rows, so no
   client script can reach them.
2. When the template row carries a blank `cost_center` and the Company has no
   Default Cost Center, the `:Company` default on
   `Sales/Purchase Taxes and Charges.cost_center` resolves blank too.

Result: the tax row saves and submits with an empty Cost Center, and the VAT GL
Entry posts with no Cost Center (verified on hatco.fateherp.com — 37 Purchase
Invoice tax rows). This module stamps the rows server-side on every save, before
GL is written, which is why `public/js/row_cost_center.js` is a convenience only
and never load-bearing.
"""

import frappe
from frappe.utils import cint

SETTINGS_DOCTYPE = "ZATCA VAT Report Settings"
ENABLE_FIELD = "sync_row_cost_center"

CHILD_TABLES = ("items", "taxes")

BACKFILL_TARGETS = (
	("Sales Invoice", "Sales Taxes and Charges", "Sales Invoice Item"),
	("Purchase Invoice", "Purchase Taxes and Charges", "Purchase Invoice Item"),
	("Sales Order", "Sales Taxes and Charges", "Sales Order Item"),
	("Purchase Order", "Purchase Taxes and Charges", "Purchase Order Item"),
	("Delivery Note", "Sales Taxes and Charges", "Delivery Note Item"),
	("Purchase Receipt", "Purchase Taxes and Charges", "Purchase Receipt Item"),
)


def is_enabled():
	"""True when the site opted in.

	`cache=True` memoises in `frappe.db.value_cache`, which is request-local, so a
	request that saves several invoices pays for one indexed `tabSingles` SELECT,
	not one per save. `set_single_value` drops that key, so a flip inside the same
	request is still seen.
	"""
	return bool(cint(frappe.db.get_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, cache=True)))


def set_missing_cost_center(doc, method=None):
	"""Copy the parent Cost Center into item/tax rows that have none.

	Registered on both `before_validate` and `validate` in hooks.py: rows can be
	appended *during* the controller's own validate, so a single pass before
	validate is not enough. Verified against erpnext 15.61.1 —
	`AccountsController.validate` (accounts_controller.py:210) reaches
	`set_taxes_and_charges` (:246 -> :1046) which calls `append_taxes_from_master`
	whenever the doc has a `taxes_and_charges` template and an empty `taxes` table.
	POS and mapped-document paths append in the same window via
	`set_pos_fields` -> `set_taxes` and `set_other_charges`.

	The function is idempotent — it only ever fills blanks, never overwrites a row
	the user set — so the second pass costs nothing when the first already ran.
	"""
	if not is_enabled():
		return

	parent_cc = doc.get("cost_center")
	if not parent_cc:
		return

	for table in CHILD_TABLES:
		if not doc.meta.get_field(table):
			continue

		for row in doc.get(table) or []:
			if not row.meta.get_field("cost_center"):
				break
			if not row.get("cost_center"):
				row.cost_center = parent_cc


def _blank_cost_center_rows(parent_dt, child_dt):
	"""Child rows with no Cost Center whose parent has one (docstatus 0 or 1)."""
	child = frappe.qb.DocType(child_dt)
	parent = frappe.qb.DocType(parent_dt)

	return (
		frappe.qb.from_(child)
		.inner_join(parent)
		.on(parent.name == child.parent)
		.select(child.name, child.parent, parent.cost_center)
		.where(
			(child.parenttype == parent_dt)
			& ((child.cost_center.isnull()) | (child.cost_center == ""))
			& (parent.cost_center.isnotnull())
			& (parent.cost_center != "")
			& (parent.docstatus < 2)
		)
		.orderby(child.parent)
		.run(as_dict=True)
	)


def backfill_blank_cost_centers(dry_run=True, limit=None):
	"""Fill blank child-row Cost Centers on existing documents.

	Not wired into patches.txt on purpose — this app is installed on many sites
	and a migrate must never silently rewrite historical rows. Run it deliberately
	from the bench console, per site, after taking a backup:

	    bench --site <site> console
	    >>> from zatca_vat_report.cost_center import backfill_blank_cost_centers
	    >>> backfill_blank_cost_centers(dry_run=True)          # report only
	    >>> backfill_blank_cost_centers(dry_run=False); frappe.db.commit()

	Ignores the settings check — running it is itself the opt-in.

	Only the child rows are touched. GL Entries already posted with a blank Cost
	Center are NOT repaired here — those need a Repost Accounting Ledger (or a
	cancel/amend) per voucher, which is a separate, reviewed operation.
	"""
	summary = {}

	for parent_dt, tax_dt, item_dt in BACKFILL_TARGETS:
		for child_dt in (tax_dt, item_dt):
			rows = _blank_cost_center_rows(parent_dt, child_dt)
			if limit:
				rows = rows[:limit]

			summary[f"{parent_dt} / {child_dt}"] = len(rows)

			if dry_run or not rows:
				continue

			for row in rows:
				frappe.db.set_value(
					child_dt, row.name, "cost_center", row.cost_center, update_modified=False
				)

	for key, count in summary.items():
		print(f"{key}: {count} row(s){' (dry run)' if dry_run else ' updated'}")

	return summary
