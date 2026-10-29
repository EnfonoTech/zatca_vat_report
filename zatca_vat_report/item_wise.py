# apps/zatca_vat_report/zatca_vat_report/item_wise.py
"""Split a single-tax-row invoice's taxable base by each line's own VAT rate.

Why this exists
---------------
A site can zero-rate per line instead of per tax account: one tax account
(e.g. ``VAT 15%``), one tax row on every invoice, and an Item Tax Template on
each line that sets that account to 0% or 15%. ERPNext then charges each line at
its own rate (``taxes_and_totals._get_tax_rate``: the line's ``item_tax_rate``
entry for the account, else the tax row's rate), so the invoice's single tax row
carries VAT on the 15% lines only.

The summary report treated a single-tax-row invoice as wholly taxed at the
account's rate (``inv.base_net_total``). On a line-wise site that put every
zero-rated line into Standard Rated sales: arkanarabia.enfonoerp.com reported
438,151.62 at 15% where 29,145 + 2 untemplated lines was the real 15% base.

Rules
-----
- Line rate = the line's ``item_tax_rate`` JSON entry for the tax account, else
  the tax row's ``rate``. Same rule as ERPNext, so the split matches the VAT
  actually charged.
- A line whose rate is ``N/A`` (template marks the tax not applicable) or whose
  ZATCA category is out of scope is not a VAT supply: it is left out of every
  bucket. That category is ``Services outside scope of tax / Not subject to VAT``
  in ksa_compliance (``standard_doctypes/tax_category.py`` maps it to code O).
- Rounding remainder (``base_net_total`` minus the sum of all line bases) goes to
  the tax row's own rate. An invoice without templates therefore reports exactly
  ``base_net_total`` at the row rate, which is what the report did before. Sites
  that never use Item Tax Templates see no change.

Exempt lines (category "Exempt from Tax") stay in the 0% bucket, as they did
before this module: separating them needs a group type the settings do not have.
"""

import json

import frappe
from frappe.utils import flt

NOT_APPLICABLE = "N/A"
OUT_OF_SCOPE_PREFIX = "Services outside scope of tax"
CATEGORY_FIELD = "custom_zatca_item_tax_category"
RATE_PRECISION = 2


def rate_key(value):
	"""A tax rate as a dict key. `round`, not `flt(value, precision)`: the latter
	reads the site's rounding method and silently returns 0 without a site."""
	return round(flt(value), RATE_PRECISION)


def is_out_of_scope(category):
	return bool(category) and str(category).strip().startswith(OUT_OF_SCOPE_PREFIX)


def line_rate(item_tax_rate, account, row_rate):
	"""Rate this line is taxed at on `account`, or None when the tax does not apply.

	`item_tax_rate` is the JSON string ERPNext stores on the line
	(`{"VAT 15% - AA": 0.0}`), or empty when the line has no template.
	"""
	try:
		item_map = json.loads(item_tax_rate) if item_tax_rate else {}
	except (TypeError, ValueError):
		item_map = {}

	if not isinstance(item_map, dict) or account not in item_map:
		return rate_key(row_rate)

	rate = item_map[account]
	if rate == NOT_APPLICABLE:
		return None

	return rate_key(rate)


def split_invoice_base(lines, account, row_rate, base_net_total):
	"""Return {rate: taxable base} for one invoice, as positive magnitudes.

	`lines` are dicts with `base_net_amount`, `item_tax_rate` and `category`.
	"""
	row_rate = rate_key(row_rate)
	buckets = {}
	lines_total = 0.0

	for line in lines:
		base = abs(flt(line.get("base_net_amount")))
		lines_total += base

		rate = line_rate(line.get("item_tax_rate"), account, row_rate)
		if rate is None or is_out_of_scope(line.get("category")):
			continue

		buckets[rate] = buckets.get(rate, 0.0) + base

	remainder = abs(flt(base_net_total)) - lines_total
	if remainder:
		buckets[row_rate] = max(buckets.get(row_rate, 0.0) + remainder, 0.0)

	return buckets


def bucket_vat(buckets, rate, tax_amount):
	"""Share of the row's VAT that belongs to the `rate` bucket.

	VAT only comes from lines with a rate above 0, so each bucket's share is its
	base x rate over the sum of base x rate across all buckets.
	"""
	rate = rate_key(rate)
	if rate <= 0:
		return 0.0

	weight = sum(base * r for r, base in buckets.items() if r > 0)
	if not weight:
		return 0.0

	return flt(tax_amount) * (buckets.get(rate, 0.0) * rate) / weight


def summarise(rows, rate):
	"""(amount, adjustment) the `rate` bucket contributes; returns go to adjustment."""
	rate = rate_key(rate)
	amount = adjustment = 0.0

	for row in rows:
		base = row.buckets.get(rate, 0.0)
		if row.is_return:
			adjustment += base
		else:
			amount += base

	return amount, adjustment


def get_single_row_invoices(doctype, tax_table, where_clause, values, account_condition=""):
	"""Invoices in scope whose only Tax-type row is an "On Net Total" row.

	`where_clause` uses the alias `inv`; `account_condition` (alias `tax`) limits
	the row to the group's accounts. Charge types other than On Net Total keep the
	old path: `item_tax_rate` only drives On Net Total rows.
	"""
	return frappe.db.sql(
		f"""
		SELECT
			inv.name AS invoice,
			inv.is_return,
			inv.base_net_total,
			tax.account_head,
			tax.rate AS row_rate,
			COALESCE(tax.base_tax_amount_after_discount_amount, tax.base_tax_amount) AS base_tax_amount
		FROM `{tax_table}` tax
		INNER JOIN `{doctype}` inv ON tax.parent = inv.name AND tax.parenttype = %(item_wise_parenttype)s
		INNER JOIN `tabAccount` acc ON tax.account_head = acc.name
		WHERE
			acc.account_type = 'Tax'
			AND tax.charge_type = 'On Net Total'
			{account_condition}
			AND {where_clause}
			AND (
				SELECT COUNT(*)
				FROM `{tax_table}` t2
				INNER JOIN `tabAccount` a2 ON t2.account_head = a2.name
				WHERE t2.parent = inv.name AND t2.parenttype = %(item_wise_parenttype)s
					AND a2.account_type = 'Tax'
			) = 1
		""",
		{**values, "item_wise_parenttype": doctype.removeprefix("tab")},
		as_dict=True,
	)


def get_invoice_lines(item_doctype, invoices):
	"""{invoice: [line, ...]} for the given invoice names, in one query."""
	if not invoices:
		return {}

	has_line_category = frappe.get_meta(item_doctype).has_field(CATEGORY_FIELD)
	has_template_category = frappe.get_meta("Item Tax Template").has_field(CATEGORY_FIELD)

	line_category = f"item.`{CATEGORY_FIELD}`" if has_line_category else "NULL"
	template_category = f"itt.`{CATEGORY_FIELD}`" if has_template_category else "NULL"

	rows = frappe.db.sql(
		f"""
		SELECT
			item.parent AS invoice,
			item.base_net_amount,
			item.item_tax_rate,
			COALESCE(NULLIF({line_category}, ''), {template_category}) AS category
		FROM `tab{item_doctype}` item
		LEFT JOIN `tabItem Tax Template` itt ON itt.name = item.item_tax_template
		WHERE item.parent IN %(invoices)s
		""",
		{"invoices": tuple(invoices)},
		as_dict=True,
	)

	lines = {}
	for row in rows:
		lines.setdefault(row.invoice, []).append(row)

	return lines


def get_item_wise_rows(doctype, tax_table, item_doctype, where_clause, values, account_condition=""):
	"""Single-row invoices with their {rate: base} split attached as `buckets`."""
	invoices = get_single_row_invoices(doctype, tax_table, where_clause, values, account_condition)
	lines = get_invoice_lines(item_doctype, [row.invoice for row in invoices])

	for row in invoices:
		row.buckets = split_invoice_base(
			lines.get(row.invoice, []), row.account_head, row.row_rate, row.base_net_total
		)

	return invoices
