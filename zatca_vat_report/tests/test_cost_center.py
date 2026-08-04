# apps/zatca_vat_report/zatca_vat_report/tests/test_cost_center.py
"""Tests for the row Cost Center sync hook.

Run:
    bench --site <site> run-tests --app zatca_vat_report \
        --module zatca_vat_report.tests.test_cost_center
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from zatca_vat_report.cost_center import (
	ENABLE_FIELD,
	SETTINGS_DOCTYPE,
	is_enabled,
	set_missing_cost_center,
)


class _Row(frappe._dict):
	"""Minimal stand-in for a child doc: needs .get() and .meta.get_field()."""

	def __init__(self, has_cost_center=True, **kwargs):
		super().__init__(**kwargs)
		self.meta = frappe._dict(
			get_field=lambda fieldname: True if (fieldname == "cost_center" and has_cost_center) else None
		)


class _Doc(frappe._dict):
	"""Minimal stand-in for a parent doc with items/taxes tables."""

	def __init__(self, tables=("items", "taxes"), **kwargs):
		super().__init__(**kwargs)
		self.meta = frappe._dict(get_field=lambda fieldname: True if fieldname in tables else None)


class _SettingMixin:
	"""Set the opt-in flag for the duration of a test, then put it back."""

	def _set_flag(self, value):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, value)

	def setUp(self):
		super().setUp()
		self._original_flag = frappe.db.get_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, cache=False)
		self._set_flag(1)

	def tearDown(self):
		self._set_flag(self._original_flag or 0)
		super().tearDown()


class TestCostCenterGate(FrappeTestCase):
	"""The feature ships off. Nothing may happen until a site opts in."""

	def setUp(self):
		super().setUp()
		self._original_flag = frappe.db.get_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, cache=False)

	def tearDown(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, self._original_flag or 0)
		super().tearDown()

	def test_disabled_setting_is_a_noop(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, 0)
		doc = _Doc(cost_center="Main - X", items=[_Row(cost_center="")], taxes=[_Row(cost_center="")])

		set_missing_cost_center(doc)

		self.assertEqual(doc.get("items")[0].cost_center, "")
		self.assertEqual(doc.taxes[0].cost_center, "")

	def test_is_enabled_tracks_the_setting(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, 0)
		self.assertFalse(is_enabled())

		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, 1)
		self.assertTrue(is_enabled())

	def test_unsynced_field_does_not_raise(self):
		"""The git-pull-before-bench-migrate window.

		`frappe.db.get_single_value` throws when the fieldname is not in meta, so
		without the guard in is_enabled() every save of a hooked DocType would fail
		between deploy and migrate.
		"""
		frappe.db.set_single_value(SETTINGS_DOCTYPE, ENABLE_FIELD, 1)
		doc = _Doc(cost_center="Main - X", items=[_Row(cost_center="")], taxes=[_Row(cost_center="")])

		with patch.object(frappe, "get_meta", return_value=frappe._dict(has_field=lambda f: False)):
			self.assertFalse(is_enabled())
			set_missing_cost_center(doc)  # must not raise

		self.assertEqual(doc.get("items")[0].cost_center, "")


class TestCostCenterAutofill(_SettingMixin, FrappeTestCase):
	def test_blank_tax_rows_get_parent_cost_center(self):
		doc = _Doc(
			cost_center="Main - X",
			items=[_Row(cost_center="Main - X")],
			taxes=[_Row(cost_center=""), _Row(cost_center=None)],
		)

		set_missing_cost_center(doc)

		self.assertEqual([t.cost_center for t in doc.taxes], ["Main - X"] * 2)

	def test_row_set_by_user_is_not_overwritten(self):
		doc = _Doc(
			cost_center="Main - X",
			items=[_Row(cost_center="Branch A - X")],
			taxes=[_Row(cost_center="Branch B - X")],
		)

		set_missing_cost_center(doc)

		self.assertEqual(doc.get("items")[0].cost_center, "Branch A - X")
		self.assertEqual(doc.taxes[0].cost_center, "Branch B - X")

	def test_no_parent_cost_center_is_a_noop(self):
		doc = _Doc(cost_center="", items=[_Row(cost_center="")], taxes=[_Row(cost_center="")])

		set_missing_cost_center(doc)

		self.assertEqual(doc.taxes[0].cost_center, "")

	def test_child_without_cost_center_field_is_skipped(self):
		doc = _Doc(
			cost_center="Main - X",
			items=[],
			taxes=[_Row(has_cost_center=False)],
		)

		set_missing_cost_center(doc)

		self.assertIsNone(doc.taxes[0].get("cost_center"))

	def test_parent_without_the_table_is_skipped(self):
		doc = _Doc(tables=("items",), cost_center="Main - X", items=[_Row(cost_center="")])

		set_missing_cost_center(doc)

		self.assertEqual(doc.get("items")[0].cost_center, "Main - X")

	def test_is_idempotent_across_before_validate_and_validate(self):
		doc = _Doc(cost_center="Main - X", items=[], taxes=[_Row(cost_center="")])

		set_missing_cost_center(doc, "before_validate")
		doc.taxes.append(_Row(cost_center=""))  # ERPNext appends from the tax template
		set_missing_cost_center(doc, "validate")

		self.assertEqual([t.cost_center for t in doc.taxes], ["Main - X"] * 2)


class TestPurchaseInvoiceCostCenter(_SettingMixin, FrappeTestCase):
	"""End-to-end check against a real Purchase Invoice with a tax template."""

	def test_tax_row_from_template_gets_cost_center_on_save(self):
		company = frappe.db.get_value("Company", {"name": ("like", "%")}, "name")
		cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")
		template = frappe.db.get_value("Purchase Taxes and Charges Template", {"company": company}, "name")
		supplier = frappe.db.get_value("Supplier", {}, "name")
		item = frappe.db.get_value("Item", {"is_stock_item": 0}, "name") or frappe.db.get_value("Item", {}, "name")

		if not all([company, cost_center, template, supplier, item]):
			self.skipTest("site has no company/cost center/tax template/supplier/item to test with")

		pi = frappe.get_doc(
			{
				"doctype": "Purchase Invoice",
				"company": company,
				"supplier": supplier,
				"cost_center": cost_center,
				"taxes_and_charges": template,
				"update_stock": 0,
				"items": [{"item_code": item, "qty": 1, "rate": 100}],
			}
		)
		pi.insert(ignore_permissions=True)

		self.assertTrue(pi.taxes, "tax template produced no rows — nothing to assert")
		for tax in pi.taxes:
			self.assertEqual(tax.cost_center, cost_center)
		for row in pi.items:
			self.assertEqual(row.cost_center, cost_center)
