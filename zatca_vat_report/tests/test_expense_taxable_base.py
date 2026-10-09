"""The Expense section of VAT Report Detail shows the base each journal VAT debit was charged on.

    bench --site <site> run-tests --module zatca_vat_report.tests.test_expense_taxable_base

A journal carries only the VAT leg, so the base is derived: VAT / (account rate / 100). These cover
the derivation on the actual figures reported (Bahrain, 10%, three decimals) and the column wiring.
"""

from unittest import mock

from frappe.tests.utils import FrappeTestCase

from zatca_vat_report.zatca_vat_report.report.vat_report_detail import vat_report_detail as report


class _Both:
	def __init__(self, *patches):
		self.patches = patches

	def __enter__(self):
		for p in self.patches:
			p.start()

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


class TestExpenseTaxableBase(FrappeTestCase):
	@staticmethod
	def _rows(rows):
		"""The query's rows, whatever the site holds, with no ePromise column asked for."""
		db = mock.MagicMock()
		db.sql.return_value = rows
		stack = mock.patch.object(report.frappe, "db", db)
		return _Both(stack, mock.patch.object(report, "_get_optional_field", return_value=None))

	def test_ten_percent_to_the_fils(self):
		# VAT figures straight from the Input VAT Expense Journal rows on production
		for vat, base in (
			(3.261, 32.610),
			(47.232, 472.320),
			(3.980, 39.800),
			(0.500, 5.000),
			(34.469, 344.690),
			(29.458, 294.580),
			(1.749, 17.490),
		):
			self.assertEqual(report.expense_taxable_base(vat, 10), base, vat)

	def test_other_rates(self):
		self.assertEqual(report.expense_taxable_base(15, 15), 100.0)
		self.assertEqual(report.expense_taxable_base(5, 5), 100.0)

	def test_no_rate_leaves_the_base_blank_not_guessed(self):
		for rate in (0, None, "", -1):
			self.assertIsNone(report.expense_taxable_base(3.261, rate), rate)

	def test_zero_vat_is_a_zero_base(self):
		self.assertEqual(report.expense_taxable_base(0, 10), 0.0)

	def test_the_column_sits_before_the_vat_it_came_from(self):
		with mock.patch.object(report, "_get_optional_field", return_value=None):
			columns = report._get_columns("Expense")
		fieldnames = [c["fieldname"] for c in columns]
		self.assertIn("base_amount", fieldnames)
		self.assertLess(fieldnames.index("base_amount"), fieldnames.index("vat_amount"))
		column = next(c for c in columns if c["fieldname"] == "base_amount")
		self.assertEqual(column["label"], "Taxable Base")
		self.assertEqual(column["fieldtype"], "Currency")

	def test_rows_use_the_account_rate_then_the_groups(self):
		rows = [
			{"vat_amount": 3.261, "account_tax_rate": 10},
			{"vat_amount": 3.261, "account_tax_rate": 0},
			{"vat_amount": 3.261, "account_tax_rate": None},
		]
		with self._rows(rows):
			out = report._get_expense_detail("2026-07-01", "2026-09-30", "Any Co", ["Input VAT"], fallback_rate=15)
		self.assertEqual([r["base_amount"] for r in out], [32.61, 21.74, 21.74])
		self.assertTrue(all("account_tax_rate" not in r for r in out))

	def test_rows_with_no_rate_anywhere_stay_blank(self):
		rows = [{"vat_amount": 3.261, "account_tax_rate": None}]
		with self._rows(rows):
			out = report._get_expense_detail("2026-07-01", "2026-09-30", "Any Co", ["Input VAT"], fallback_rate=0)
		self.assertIsNone(out[0]["base_amount"])
