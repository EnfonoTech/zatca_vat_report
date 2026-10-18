"""A journal that reverses VAT takes it back off the Expense section of the VAT Report.

    bench --site <site> run-tests --module zatca_vat_report.tests.test_journal_vat_reversal

Only a reversal may take VAT off the account. A plain credit on it is a settlement (the balance moved
to VAT payable at period end) and stays out. A journal is a reversal when it carries the built-in
`reversal_of`, or rows in `Reversal Of (Multiple Journals)` -- the field for one entry that reverses
several journals, which `reversal_of` (a single link) cannot hold.
"""

from unittest import mock

from frappe.tests.utils import FrappeTestCase

from zatca_vat_report.zatca_vat_report.report.vat_report import vat_report as summary
from zatca_vat_report.zatca_vat_report.report.vat_report_detail import vat_report_detail as report


class TestJournalReversalCondition(FrappeTestCase):
	def test_built_in_reversal_of_always_counts(self):
		with mock.patch.object(report, "_has_reversal_table", return_value=False):
			cond = report.journal_reversal_condition("je")
		self.assertIn("IFNULL(je.reversal_of, '') != ''", cond)
		self.assertNotIn(report.REVERSAL_TABLE, cond)

	def test_the_multiple_journal_table_counts_once_it_exists(self):
		with mock.patch.object(report, "_has_reversal_table", return_value=True):
			cond = report.journal_reversal_condition("je")
		self.assertIn("IFNULL(je.reversal_of, '') != ''", cond)
		self.assertIn(f"`tab{report.REVERSAL_TABLE}`", cond)
		self.assertIn("vrj.parent = je.name", cond)
		self.assertIn(f"vrj.parentfield = '{report.REVERSAL_FIELD}'", cond)

	def test_a_debit_always_counts_a_credit_only_for_a_reversal(self):
		with mock.patch.object(report, "_has_reversal_table", return_value=False):
			cond = report.journal_vat_row_condition("jea", "je")
		self.assertTrue(cond.startswith("(jea.debit > 0 OR (jea.credit > 0 AND ("))
		self.assertIn("je.reversal_of", cond)

	def test_the_report_survives_a_site_that_has_not_migrated(self):
		# the code lands before `bench migrate` creates the child table
		db = mock.MagicMock()
		db.table_exists.return_value = False
		with mock.patch.object(report.frappe, "db", db):
			cond = report.journal_reversal_condition("je")
			select = report._reversal_select("je")
		self.assertNotIn(report.REVERSAL_TABLE, cond)
		self.assertNotIn(report.REVERSAL_TABLE, select)
		self.assertIn("NULL AS reversal_journals", select)


class _Both:
	def __init__(self, *patches):
		self.patches = patches

	def __enter__(self):
		for p in self.patches:
			p.start()

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


class TestExpenseDetailShowsReversals(FrappeTestCase):
	@staticmethod
	def _rows(rows):
		db = mock.MagicMock()
		db.sql.return_value = rows
		return _Both(
			mock.patch.object(report.frappe, "db", db),
			mock.patch.object(report, "_get_optional_field", return_value=None),
		)

	def test_a_reversal_is_a_minus_row_naming_the_journals_it_reverses(self):
		rows = [
			{"vat_amount": 105, "account_tax_rate": 15, "reversal_of": None, "reversal_journals": None},
			{
				"vat_amount": -195,
				"account_tax_rate": 15,
				"reversal_of": None,
				"reversal_journals": "JV-A, JV-B, JV-C",
			},
			{"vat_amount": -15, "account_tax_rate": 15, "reversal_of": "JV-D", "reversal_journals": None},
		]
		with self._rows(rows):
			out = report._get_expense_detail("2026-07-01", "2026-07-31", "Any Co", ["Input VAT"], fallback_rate=15)
		self.assertEqual([r["vat_amount"] for r in out], [105, -195, -15])
		self.assertEqual([r["base_amount"] for r in out], [700.0, -1300.0, -100.0])
		self.assertEqual([r["reversal_of"] for r in out], ["", "JV-A, JV-B, JV-C", "JV-D"])
		self.assertTrue(all("reversal_journals" not in r for r in out))

	def test_a_journal_with_both_links_names_all_of_them(self):
		rows = [{"vat_amount": -10, "account_tax_rate": 15, "reversal_of": "JV-D", "reversal_journals": "JV-E"}]
		with self._rows(rows):
			out = report._get_expense_detail("2026-07-01", "2026-07-31", "Any Co", ["Input VAT"])
		self.assertEqual(out[0]["reversal_of"], "JV-D, JV-E")

	def test_the_column_sits_after_the_remark(self):
		with mock.patch.object(report, "_get_optional_field", return_value=None):
			fieldnames = [c["fieldname"] for c in report._get_columns("Expense")]
		self.assertEqual(fieldnames.index("reversal_of"), fieldnames.index("user_remark") + 1)


class TestSummaryNetsReversals(FrappeTestCase):
	def test_the_journal_total_is_debit_minus_credit_over_the_counted_rows(self):
		db = mock.MagicMock()
		db.sql.return_value = [{"net_amount": 4200}]
		filters = {"company": "Any Co", "from_date": "2026-07-01", "to_date": "2026-07-31"}
		with mock.patch.object(summary.frappe, "db", db), mock.patch.object(
			report, "_has_reversal_table", return_value=True
		):
			total = summary.get_expense_vat_from_journal_entries(filters, ["Input VAT"])
		self.assertEqual(total, 4200)
		query = db.sql.call_args[0][0]
		self.assertIn("SUM(jea.debit - jea.credit)", query)
		self.assertIn("jea.credit > 0 AND", query)
		self.assertNotIn("AND jea.debit > 0", query)
