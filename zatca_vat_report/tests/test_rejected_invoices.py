# apps/zatca_vat_report/zatca_vat_report/tests/test_rejected_invoices.py
"""Tests for excluding ZATCA-rejected Sales Invoices from the summary report.

Run:
    bench --site <site> run-tests --app zatca_vat_report \
        --module zatca_vat_report.tests.test_rejected_invoices
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from zatca_vat_report.zatca_vat_report.report.zatca_vat_report import zatca_vat_report as report

FILTERS = frappe._dict(company="_Test Company", from_date="2026-01-01", to_date="2026-01-31")


def _summary_sql(rejected, is_sales=True):
	"""Run get_taxable_summary with the DB stubbed; return (query, values) it sent."""
	captured = {}

	def fake_sql(query, values=None, as_dict=False):
		captured["query"], captured["values"] = query, values
		return []

	with (
		patch.object(report, "get_rejected_sales_invoices", return_value=rejected),
		patch.object(report.frappe.db, "sql", side_effect=fake_sql),
	):
		report.get_taxable_summary(
			"tabSales Invoice", "tabSales Taxes and Charges", FILTERS, ["VAT 15%"], 15, is_sales
		)
	return captured["query"], captured["values"]


class TestRejectedFilter(FrappeTestCase):
	"""Rejected invoices are passed as a literal list, never as a per-invoice subquery."""

	def test_rejected_names_become_a_literal_list(self):
		query, values = _summary_sql(["SINV-1", "SINV-2"])
		self.assertIn("inv.name NOT IN %(rejected_invoices)s", query)
		self.assertEqual(values["rejected_invoices"], ("SINV-1", "SINV-2"))

	def test_no_rejected_invoices_adds_no_filter(self):
		# An empty tuple would render as `NOT IN ()`, which is a SQL syntax error.
		query, values = _summary_sql([])
		self.assertNotIn("rejected_invoices", query)
		self.assertNotIn("rejected_invoices", values)

	def test_purchase_side_never_filters_rejected(self):
		query, _ = _summary_sql(["SINV-1"], is_sales=False)
		self.assertNotIn("rejected_invoices", query)

	def test_no_subquery_on_additional_fields(self):
		query, _ = _summary_sql(["SINV-1"])
		self.assertNotIn("Sales Invoice Additional Fields", query)
		self.assertNotIn("ZATCA Integration Log", query)


class TestGetRejectedSalesInvoices(FrappeTestCase):
	def test_returns_empty_when_no_zatca_app(self):
		with patch.object(report.frappe.db, "exists", return_value=False):
			self.assertEqual(report.get_rejected_sales_invoices(), [])

	def test_prefers_additional_fields_and_skips_null_names(self):
		with (
			patch.object(report.frappe.db, "exists", side_effect=lambda dt, name: name == "Sales Invoice Additional Fields"),
			patch.object(report.frappe.db, "sql_list", return_value=["SINV-9"]) as sql_list,
		):
			self.assertEqual(report.get_rejected_sales_invoices(), ["SINV-9"])
		query = sql_list.call_args[0][0]
		self.assertIn("Sales Invoice Additional Fields", query)
		# A NULL inside `NOT IN (...)` makes the whole predicate NULL and drops every invoice.
		self.assertIn("IS NOT NULL", query)
