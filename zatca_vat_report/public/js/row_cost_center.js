// apps/zatca_vat_report/zatca_vat_report/public/js/row_cost_center.js
//
// Cosmetic only. The authoritative stamping is server-side in
// zatca_vat_report.cost_center.set_missing_cost_center (before_validate + validate),
// so a stale frappe.boot flag can never produce a wrong GL Entry — it only delays
// the visual fill until the next desk reload.
//
// Registered under all six DocTypes in hooks.py `doctype_js`. The load guard below
// matters because the tax child DocTypes are shared: without it, opening a Sales
// Invoice and then a Delivery Note in one session would register the
// 'Sales Taxes and Charges' handler twice.

if (!window.__zvr_row_cost_center_loaded) {
	window.__zvr_row_cost_center_loaded = true;

	(() => {
		const PARENTS = [
			'Sales Invoice',
			'Purchase Invoice',
			'Sales Order',
			'Purchase Order',
			'Delivery Note',
			'Purchase Receipt',
		];

		const ITEM_CHILDREN = [
			'Sales Invoice Item',
			'Purchase Invoice Item',
			'Sales Order Item',
			'Purchase Order Item',
			'Delivery Note Item',
			'Purchase Receipt Item',
		];

		const TAX_CHILDREN = ['Sales Taxes and Charges', 'Purchase Taxes and Charges'];

		const enabled = () => cint(frappe.boot?.zatca_vat_report?.sync_row_cost_center);

		// The tax child DocTypes are shared with Quotation, Supplier Quotation and
		// others. Keep the client scope identical to the server hook's scope.
		const in_scope = (frm) => enabled() && PARENTS.includes(frm.doc.doctype) && !!frm.doc.cost_center;

		const stamp_rows = (frm, blanks_only) => {
			['items', 'taxes'].forEach((table) => {
				(frm.doc[table] || []).forEach((row) => {
					if (blanks_only && row.cost_center) {
						return;
					}
					frappe.model.set_value(row.doctype, row.name, 'cost_center', frm.doc.cost_center);
				});
				frm.refresh_field(table);
			});
		};

		PARENTS.forEach((doctype) => {
			frappe.ui.form.on(doctype, {
				cost_center(frm) {
					// Explicit user action on the parent field — re-apply to every row,
					// including rows that already carry a different Cost Center.
					if (in_scope(frm)) {
						stamp_rows(frm, false);
					}
				},
				onload(frm) {
					// Only on a brand-new form. On a saved or submitted document the server
					// hook already fills blanks on save, and writing to rows here would mark
					// a clean document dirty -> spurious "Not Saved" state and Update button.
					if (frm.is_new() && in_scope(frm)) {
						stamp_rows(frm, true);
					}
				},
			});
		});

		// grid.js fires `<table_fieldname>_add` against the CHILD DocType
		// (frappe/public/js/frappe/form/grid.js: trigger(fieldname + "_add", d.doctype, d.name)).
		ITEM_CHILDREN.forEach((child) => {
			frappe.ui.form.on(child, {
				items_add(frm, cdt, cdn) {
					if (in_scope(frm)) {
						frappe.model.set_value(cdt, cdn, 'cost_center', frm.doc.cost_center);
					}
				},
			});
		});

		TAX_CHILDREN.forEach((child) => {
			frappe.ui.form.on(child, {
				taxes_add(frm, cdt, cdn) {
					if (in_scope(frm)) {
						frappe.model.set_value(cdt, cdn, 'cost_center', frm.doc.cost_center);
					}
				},
			});
		});
	})();
}
