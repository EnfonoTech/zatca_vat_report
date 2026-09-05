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


def _find(where):
	rows = frappe.db.sql(
		"""select pi.name, pi.base_grand_total, round(sum(pii.base_net_amount), 3) line_total
		   from `tabPurchase Invoice` pi join `tabPurchase Invoice Item` pii on pii.parent = pi.name
		   where pi.docstatus = 1 %s
		   group by pi.name, pi.base_grand_total limit 40""" % where,
		as_dict=True,
	)
	return rows


class TestSupplierInvoiceBasis(FrappeTestCase):
	def setUp(self):
		self._real = report.declare_at_supplier_invoice_value

	def tearDown(self):
		report.declare_at_supplier_invoice_value = self._real

	def switch(self, on):
		report.declare_at_supplier_invoice_value = lambda: on

	def a_migrated_invoice(self):
		"""One whose item lines total more than the supplier billed."""
		for r in _find(""):
			if abs(flt(r.line_total) - _supplier_value(r.name)) > 1:
				return r.name
		return None

	def a_post_go_live_invoice(self):
		"""One whose item lines already equal the supplier value."""
		for r in _find(""):
			if abs(flt(r.line_total) - _supplier_value(r.name)) <= 0.005 and flt(r.line_total):
				return r.name
		return None

	def test_the_switch_is_off_unless_somebody_turns_it_on(self):
		"""Read it the way the report does. `get_single_value` THROWS on a field the site has not
		migrated yet -- it does not return None -- which is exactly why the helper catches."""
		self.assertFalse(
			self._real(),
			"the setting must default to off, so no site changes its declared figures on upgrade",
		)

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
