# apps/zatca_vat_report/zatca_vat_report/tests/test_detail_empty_rows.py
"""Drill-down must not list invoices that contribute nothing to the group.

Run:
    bench --site <site> run-tests --app zatca_vat_report \
        --module zatca_vat_report.tests.test_detail_empty_rows
"""

from frappe.tests.utils import FrappeTestCase

from zatca_vat_report.zatca_vat_report.report.zatca_vat_report_detail.zatca_vat_report_detail import (
	_drop_empty_rows,
)


class TestDropEmptyRows(FrappeTestCase):
	def test_standard_invoice_with_extra_zero_row_is_dropped(self):
		# 1125.00 net, 168.75 VAT on the 15% row: zero-rated base = 1125 - 168.75/0.15
		zero_base = max(0, 1125.0 - 168.75 / 0.15)
		rows = [{"invoice": "SI-1", "base_amount": zero_base, "vat_amount": 0.0}]
		self.assertEqual(_drop_empty_rows(rows), [])

	def test_real_zero_rated_invoice_is_kept(self):
		rows = [{"invoice": "SI-2", "base_amount": 500.0, "vat_amount": 0.0}]
		self.assertEqual(len(_drop_empty_rows(rows)), 1)

	def test_return_with_negative_base_is_kept(self):
		rows = [{"invoice": "SI-3", "base_amount": -500.0, "vat_amount": 0.0}]
		self.assertEqual(len(_drop_empty_rows(rows)), 1)

	def test_vat_only_row_is_kept(self):
		rows = [{"invoice": "SI-4", "base_amount": 0.0, "vat_amount": 75.0}]
		self.assertEqual(len(_drop_empty_rows(rows)), 1)
