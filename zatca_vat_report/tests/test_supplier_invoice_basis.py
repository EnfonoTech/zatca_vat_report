"""The purchase base can be declared at what the supplier billed, rather than at the item lines.

    bench --site <site> run-tests --module zatca_vat_report.tests.test_supplier_invoice_basis

Why the setting exists: invoices migrated from ePromise carry the landed cost -- freight, customs
duty and clearance -- inside the item rate, so their lines total more than the supplier's own
invoice. Invoices raised after go-live put landed cost through a Landed Cost Voucher, so their lines
already equal the supplier value. The switch must therefore change the first kind and leave the
second untouched, which is what these tests assert against whatever real data the site holds.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from zatca_vat_report.zatca_vat_report.report.vat_report_detail import vat_report_detail as report


def _supplier_value(invoice):
	grand = flt(frappe.db.get_value("Purchase Invoice", invoice, "base_grand_total"))
	vat = flt(
		frappe.db.sql(
			"""select sum(coalesce(t.base_tax_amount_after_discount_amount, t.base_tax_amount))
			   from `tabPurchase Taxes and Charges` t
			   join `tabAccount` a on a.name = t.account_head
			   where t.parent = %s and a.account_type = 'Tax'""",
			invoice,
		)[0][0]
	)
	return grand - vat


def _base_map_for(invoice):
	"""The shape the report builds: one row per invoice, the item lines split across three buckets."""
	lines = flt(
		frappe.db.sql(
			"select sum(base_net_amount) from `tabPurchase Invoice Item` where parent = %s", invoice
		)[0][0]
	)
	return {invoice: frappe._dict({"purchase_base": lines, "expense_base": 0.0, "asset_base": 0.0})}


def _find(where="", shape=None):
	"""Invoices of a given shape, asked of SQL directly.

	Scanning the first N rows and hoping one matches is how a test silently skips itself on the very
	site it was written for: production holds 93 landed-cost invoices and a 40-row scan found none.
	`shape` is a HAVING clause over `gap` = item lines minus what the supplier billed.
	"""
	having = "having %s" % shape if shape else ""
	return frappe.db.sql(
		"""select pi.name, pi.base_grand_total,
		          round(sum(pii.base_net_amount), 3) line_total,
		          round(sum(pii.base_net_amount)
		                - (pi.base_grand_total - coalesce((
		                    select sum(coalesce(t.base_tax_amount_after_discount_amount, t.base_tax_amount))
		                    from `tabPurchase Taxes and Charges` t
		                    join `tabAccount` a on a.name = t.account_head
		                    where t.parent = pi.name and a.account_type = 'Tax'), 0)), 3) gap
		   from `tabPurchase Invoice` pi join `tabPurchase Invoice Item` pii on pii.parent = pi.name
		   where pi.docstatus = 1 {where}
		   group by pi.name, pi.base_grand_total {having} limit 5""".format(where=where, having=having),
		as_dict=True,
	)


class TestSupplierInvoiceBasis(FrappeTestCase):
	def setUp(self):
		self._real = report.declare_at_supplier_invoice_value

	def tearDown(self):
		report.declare_at_supplier_invoice_value = self._real

	def switch(self, on):
		report.declare_at_supplier_invoice_value = lambda: on

	def a_migrated_invoice(self):
		"""One whose item lines total more than the supplier billed."""
		rows = _find(shape="abs(gap) > 1")
		return rows[0].name if rows else None

	def a_post_go_live_invoice(self):
		"""One whose item lines already equal the supplier value."""
		rows = _find(shape="abs(gap) <= 0.005 and sum(pii.base_net_amount) <> 0")
		return rows[0].name if rows else None

	def test_the_switch_ships_switched_off(self):
		"""Assert the SHIPPED default, not the current site value.

		A site that has deliberately turned this on -- production has -- must not fail its own test
		suite for doing so. What matters is that installing or upgrading the app changes nobody's
		declared figures until a human opts in.
		"""
		field = frappe.get_meta("ZATCA VAT Report Settings").get_field(report.SUPPLIER_VALUE_FLAG)
		self.assertIsNotNone(field, "the setting is missing — has bench migrate / reload_doc run?")
		self.assertIn(str(field.default or "0"), ("0", "None"), "the setting must ship switched off")

	def test_the_report_reads_whatever_the_site_has_stored(self):
		stored = frappe.db.get_single_value("ZATCA VAT Report Settings", report.SUPPLIER_VALUE_FLAG)
		self.assertEqual(self._real(), bool(stored))

	def test_nothing_happens_while_the_switch_is_off(self):
		name = self.a_migrated_invoice()
		if not name:
			self.skipTest("no invoice on this site carries landed cost inside the item rate")
		self.switch(False)
		before = _base_map_for(name)[name].purchase_base
		after = report.apply_supplier_invoice_basis(_base_map_for(name))[name].purchase_base
		self.assertAlmostEqual(after, before, places=3)

	def test_a_migrated_invoice_is_declared_at_what_the_supplier_billed(self):
		name = self.a_migrated_invoice()
		if not name:
			self.skipTest("no invoice on this site carries landed cost inside the item rate")
		self.switch(True)
		out = report.apply_supplier_invoice_basis(_base_map_for(name))[name]
		total = out.purchase_base + out.expense_base + out.asset_base
		self.assertAlmostEqual(total, _supplier_value(name), places=2)

	def test_an_invoice_raised_after_go_live_is_left_exactly_as_it_is(self):
		"""The ratio is 1 for these, and the code must not nudge them by a rounding error."""
		name = self.a_post_go_live_invoice()
		if not name:
			self.skipTest("no invoice on this site has lines equal to the supplier value")
		self.switch(True)
		before = _base_map_for(name)[name].purchase_base
		after = report.apply_supplier_invoice_basis(_base_map_for(name))[name].purchase_base
		self.assertEqual(after, before, "an untouched invoice must come back byte-identical")

	def test_the_purchase_expense_asset_split_survives_the_scaling(self):
		name = self.a_migrated_invoice()
		if not name:
			self.skipTest("no invoice on this site carries landed cost inside the item rate")
		lines = _base_map_for(name)[name].purchase_base
		base_map = {name: frappe._dict({"purchase_base": lines * 0.6, "expense_base": lines * 0.3,
		                                "asset_base": lines * 0.1})}
		self.switch(True)
		out = report.apply_supplier_invoice_basis(base_map)[name]
		total = out.purchase_base + out.expense_base + out.asset_base
		self.assertAlmostEqual(total, _supplier_value(name), places=2)
		self.assertAlmostEqual(out.purchase_base / total, 0.6, places=4)
		self.assertAlmostEqual(out.expense_base / total, 0.3, places=4)
		self.assertAlmostEqual(out.asset_base / total, 0.1, places=4)

	def test_an_invoice_with_no_lines_is_skipped_rather_than_divided_by_zero(self):
		name = self.a_migrated_invoice() or "does-not-matter"
		self.switch(True)
		out = report.apply_supplier_invoice_basis(
			{name: frappe._dict({"purchase_base": 0.0, "expense_base": 0.0, "asset_base": 0.0})}
		)[name]
		self.assertEqual(out.purchase_base, 0.0)

	def test_a_credit_note_keeps_its_negative_sign(self):
		rows = _find("and pi.is_return = 1")
		name = next((r.name for r in rows if flt(r.line_total) < 0 and _supplier_value(r.name)), None)
		if not name:
			self.skipTest("no submitted purchase return on this site")
		self.switch(True)
		out = report.apply_supplier_invoice_basis(_base_map_for(name))[name]
		self.assertLess(out.purchase_base, 0, "a return must stay negative after scaling")

	def test_the_flag_reader_survives_a_site_that_has_not_migrated_yet(self):
		"""The field does not exist until bench migrate syncs it; the report must not blow up."""
		original = frappe.db.get_single_value
		frappe.db.get_single_value = lambda *a, **k: (_ for _ in ()).throw(Exception("no such field"))
		try:
			self.assertFalse(self._real())
		finally:
			frappe.db.get_single_value = original
