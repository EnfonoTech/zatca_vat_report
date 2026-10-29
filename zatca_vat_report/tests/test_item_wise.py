# apps/zatca_vat_report/zatca_vat_report/tests/test_item_wise.py
"""Tests for the line-by-line split of single-tax-row invoices.

Pure functions, no site needed:
    cd apps/zatca_vat_report && ../../env/bin/python -m unittest zatca_vat_report.tests.test_item_wise

The shapes are taken from arkanarabia.enfonoerp.com (2026-10-29): one account
`VAT 15% - AA`, one tax row per invoice, and Item Tax Templates storing
`{"VAT 15% - AA": 0.0}` or `{"VAT 15% - AA": 15.0}` on each line.
"""

import unittest

import frappe

from zatca_vat_report import item_wise

VAT = "VAT 15% - AA"
ZERO = '{"VAT 15% - AA": 0.0}'
STANDARD = '{"VAT 15% - AA": 15.0}'
OUT_OF_SCOPE = "Services outside scope of tax / Not subject to VAT || {manual entry}"


def line(base, item_tax_rate="{}", category=None):
	return {"base_net_amount": base, "item_tax_rate": item_tax_rate, "category": category}


class TestLineRate(unittest.TestCase):
	def test_template_entry_wins_over_row_rate(self):
		self.assertEqual(item_wise.line_rate(ZERO, VAT, 15), 0.0)
		self.assertEqual(item_wise.line_rate(STANDARD, VAT, 0), 15.0)

	def test_no_template_falls_back_to_row_rate(self):
		self.assertEqual(item_wise.line_rate("{}", VAT, 15), 15.0)
		self.assertEqual(item_wise.line_rate("", VAT, 15), 15.0)
		self.assertEqual(item_wise.line_rate(None, VAT, 15), 15.0)

	def test_entry_for_another_account_is_ignored(self):
		self.assertEqual(item_wise.line_rate('{"VAT 5% - X": 5.0}', VAT, 15), 15.0)

	def test_not_applicable_is_none(self):
		self.assertIsNone(item_wise.line_rate('{"VAT 15% - AA": "N/A"}', VAT, 15))

	def test_bad_json_falls_back_to_row_rate(self):
		self.assertEqual(item_wise.line_rate("{not json", VAT, 15), 15.0)
		self.assertEqual(item_wise.line_rate("[1, 2]", VAT, 15), 15.0)


class TestSplitInvoiceBase(unittest.TestCase):
	def test_untemplated_invoice_reports_base_net_total_at_row_rate(self):
		"""The old behaviour, to the cent, for sites that never use templates."""
		buckets = item_wise.split_invoice_base([line(100.0), line(200.005)], VAT, 15, 300.01)
		self.assertEqual(buckets, {15.0: 300.01})

	def test_mixed_invoice_splits_by_line_rate(self):
		buckets = item_wise.split_invoice_base(
			[line(1000.0, ZERO), line(500.0, STANDARD), line(250.0, ZERO)], VAT, 15, 1750.0
		)
		self.assertEqual(buckets, {0.0: 1250.0, 15.0: 500.0})

	def test_all_zero_rated_invoice_has_no_standard_base(self):
		"""A single 15% row whose every line is 0% (26090008 on arkanarabia)."""
		buckets = item_wise.split_invoice_base([line(82806.59, ZERO)], VAT, 15, 82806.59)
		self.assertEqual(buckets, {0.0: 82806.59})

	def test_row_rate_zero_with_standard_lines(self):
		"""Purchase invoices there carry rate 0 on the row; the lines decide."""
		buckets = item_wise.split_invoice_base([line(100.0, STANDARD), line(50.0, ZERO)], VAT, 0, 150.0)
		self.assertEqual(buckets, {15.0: 100.0, 0.0: 50.0})

	def test_out_of_scope_and_not_applicable_lines_are_left_out(self):
		buckets = item_wise.split_invoice_base(
			[
				line(400.0, ZERO, category=OUT_OF_SCOPE),
				line(300.0, '{"VAT 15% - AA": "N/A"}'),
				line(200.0, ZERO),
				line(100.0, STANDARD),
			],
			VAT,
			15,
			1000.0,
		)
		self.assertEqual(buckets, {0.0: 200.0, 15.0: 100.0})

	def test_return_uses_magnitudes(self):
		buckets = item_wise.split_invoice_base(
			[line(-6000.0, ZERO), line(-1066.5, STANDARD)], VAT, 15, -7066.5
		)
		self.assertEqual(buckets, {0.0: 6000.0, 15.0: 1066.5})

	def test_rounding_remainder_goes_to_row_rate(self):
		buckets = item_wise.split_invoice_base([line(100.0, ZERO), line(33.33, STANDARD)], VAT, 15, 133.34)
		self.assertAlmostEqual(buckets[15.0], 33.34, places=6)
		self.assertEqual(buckets[0.0], 100.0)

	def test_invoice_without_lines_keeps_its_total(self):
		self.assertEqual(item_wise.split_invoice_base([], VAT, 15, 50.0), {15.0: 50.0})


class TestBucketVat(unittest.TestCase):
	def test_zero_bucket_has_no_vat(self):
		self.assertEqual(item_wise.bucket_vat({0.0: 1000.0, 15.0: 500.0}, 0, 75.0), 0.0)

	def test_single_positive_rate_takes_all_the_row_vat(self):
		self.assertEqual(item_wise.bucket_vat({0.0: 1000.0, 15.0: 500.0}, 15, 75.0), 75.0)

	def test_two_positive_rates_share_by_base_times_rate(self):
		vat = item_wise.bucket_vat({15.0: 100.0, 5.0: 100.0}, 5, 20.0)
		self.assertAlmostEqual(vat, 5.0, places=6)

	def test_no_taxed_lines(self):
		self.assertEqual(item_wise.bucket_vat({0.0: 10.0}, 15, 0.0), 0.0)


class TestSummarise(unittest.TestCase):
	def test_returns_go_to_adjustment(self):
		rows = [
			frappe._dict(is_return=0, buckets={0.0: 1000.0, 15.0: 500.0}),
			frappe._dict(is_return=1, buckets={0.0: 200.0}),
			frappe._dict(is_return=0, buckets={15.0: 50.0}),
		]
		self.assertEqual(item_wise.summarise(rows, 0), (1000.0, 200.0))
		self.assertEqual(item_wise.summarise(rows, 15), (550.0, 0.0))
		self.assertEqual(item_wise.summarise([], 15), (0.0, 0.0))


class TestOutOfScope(unittest.TestCase):
	def test_categories(self):
		self.assertTrue(item_wise.is_out_of_scope(OUT_OF_SCOPE))
		self.assertFalse(item_wise.is_out_of_scope("Zero rated goods || Export of goods"))
		self.assertFalse(item_wise.is_out_of_scope("Standard rate"))
		self.assertFalse(item_wise.is_out_of_scope(None))
		self.assertFalse(item_wise.is_out_of_scope(""))


if __name__ == "__main__":
	unittest.main()
