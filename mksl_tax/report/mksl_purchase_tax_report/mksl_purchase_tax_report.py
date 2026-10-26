# Copyright (c) 2022, Navari Limited and contributors
# For license information, please see license.txt

import csv
import os
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from typing import TypedDict

import frappe
from frappe import _
from frappe.utils import flt


class TaxReport(ABC):
	def __init__(self):
		self.registered_suppliers_total_purchases = 0.0
		self.registered_suppliers_total_vat = 0.0
		self.unregistered_suppliers_total_purchases = 0.0
		self.unregistered_suppliers_total_vat = 0.0
		self.currency = None

	@abstractmethod
	def get_columns(self):
		"""Return the report column definitions (names, labels, types)"""

	@abstractmethod
	def get_report_summary(self):
		"""Return the report summary"""

	def get_data(self, invoice_type):
		data = []
		invoice_data = self.get_invoice_data(invoice_type)
		if invoice_data:
			data = self.process_invoice_data(invoice_data, invoice_type)
			if self.filters.get("accounting_dimension") and data:
				data = self.group_by_dimension(data)
		return data

	def get_invoice_data(self, invoice_type):
		invoice_doctype = "Sales Invoice" if invoice_type == "sales" else "Purchase Invoice"
		item_doctype = f"{invoice_doctype} Item"
		INV = frappe.qb.DocType(invoice_doctype)
		INVI = frappe.qb.DocType(item_doctype)
		TD = frappe.qb.DocType("Item Wise Tax Detail")

		query = (
			frappe.qb.from_(TD)
			.inner_join(INV)
			.on(TD.parent == INV.name)
			.inner_join(INVI)
			.on((INVI.parent == INV.name) & (INVI.name == TD.item_row))
			.select(
				INV.supplier if invoice_type == "purchase" else INV.customer,
				INV.supplier_name if invoice_type == "purchase" else INV.customer_name,
				INV.tax_id,
				INV.name.as_("invoice_number"),
				INV.posting_date.as_("invoice_date"),
				INV.return_against,
				TD.item_row,
				TD.amount,
				TD.rate,
				TD.taxable_amount,
			)
			.where(INV.docstatus == 1)
		)

		if frappe.get_meta(invoice_doctype).has_field("etr_invoice_number"):
			query = query.select(INV.etr_invoice_number)

		if invoice_type == "purchase":
			query = query.select(
				INV.bill_no.as_("supplier_invoice_no"),
				INV.bill_date.as_("supplier_invoice_date"),
			)

		query = self._select_accounting_dimension(query, INV, invoice_doctype)

		if self.filters.get("company"):
			query = query.where(INV.company == self.filters.get("company"))

		if self.filters.get("from_date"):
			query = query.where(INV.posting_date >= self.filters.get("from_date"))

		if self.filters.get("to_date"):
			query = query.where(INV.posting_date <= self.filters.get("to_date"))

		if self.filters.get("item_tax_template"):
			query = query.where(INVI.item_tax_template == self.filters.get("item_tax_template"))

		if self.filters.get("taxes_and_charges_template"):
			query = query.where(INV.taxes_and_charges == self.filters.get("taxes_and_charges_template"))

		if self.filters.get("is_return"):
			query = query.where(INV.is_return == self.filters.get("is_return"))

		return query.run(as_dict=True)

	def _select_accounting_dimension(self, query, INV, invoice_doctype):
		accounting_dimension = self.filters.get("accounting_dimension")
		if not accounting_dimension:
			return query

		fieldname = frappe.scrub(accounting_dimension)
		if frappe.get_meta(invoice_doctype).has_field(fieldname):
			return query.select(getattr(INV, fieldname).as_("accounting_dimension_value"))

		return query

	def process_invoice_data(self, invoice_data, invoice_type):
		self.currency = frappe.db.get_value("Company", self.filters.get("company"), "default_currency")
		party_field = "supplier" if invoice_type == "purchase" else "customer"
		party_name_field = "supplier_name" if invoice_type == "purchase" else "customer_name"
		group_by_dimension = bool(self.filters.get("accounting_dimension"))

		invoice_map = {}
		seen_items = {}
		return_against_map = {}

		for row in invoice_data:
			invoice_number = row.get("invoice_number")
			item_row = row.get("item_row")

			if invoice_number not in invoice_map:
				invoice_map[invoice_number] = {
					"party": row.get(party_field),
					"party_name": row.get(party_name_field),
					"tax_id": row.get("tax_id"),
					"invoice_number": invoice_number,
					"invoice_date": row.get("invoice_date"),
					"return_against": row.get("return_against"),
					"supplier_invoice_no": row.get("supplier_invoice_no"),
					"supplier_invoice_date": row.get("supplier_invoice_date"),
					"etr_invoice_number": row.get("etr_invoice_number"),
					"taxable_amount": 0.0,
					"vat_amount": 0.0,
					"currency": self.currency,
				}

				if row.get("return_against"):
					return_against_map[row.get("return_against")] = {
						"invoice_number": row.get("return_against"),
					}

				if group_by_dimension:
					invoice_map[invoice_number]["accounting_dimension_value"] = row.get(
						"accounting_dimension_value"
					)
				seen_items[invoice_number] = set()

			invoice_map[invoice_number]["vat_amount"] += flt(row.get("amount", 0))

			if party_field == "supplier":
				if row.get("tax_id"):
					self.registered_suppliers_total_vat += flt(row.get("amount", 0))
				else:
					self.unregistered_suppliers_total_vat += flt(row.get("amount", 0))

			if item_row not in seen_items[invoice_number]:
				invoice_map[invoice_number]["taxable_amount"] += flt(row.get("taxable_amount", 0))
				seen_items[invoice_number].add(item_row)

				if party_field == "supplier":
					if row.get("tax_id"):
						self.registered_suppliers_total_purchases += flt(row.get("taxable_amount", 0))
					else:
						self.unregistered_suppliers_total_purchases += flt(row.get("taxable_amount", 0))

		if invoice_type == "purchase":
			INV = frappe.qb.DocType("Purchase Invoice")
			if return_against_map:
				query = frappe.qb.from_(INV).select(INV.name, INV.bill_date, INV.posting_date)
				if frappe.get_meta("Purchase Invoice").has_field("etr_invoice_number"):
					query = query.select(INV.etr_invoice_number).where(INV.etr_invoice_number.isnotnull())
				query = query.where(INV.name.isin(list(return_against_map.keys())))
				data = query.run(as_dict=True)
				for row in data:
					return_against_map[row.name] = {
						"original_cu_invoice_number": row.etr_invoice_number,
						"original_cu_invoice_date": row.get("bill_date", row.get("posting_date")),
					}

			if invoice_map and return_against_map:
				for __, invoice_data in invoice_map.items():
					if (
						invoice_data.get("return_against")
						and return_against_map.get(invoice_data.get("return_against"))
						and return_against_map.get(invoice_data.get("return_against")).get(
							"original_cu_invoice_number"
						)
					):
						invoice_data["original_cu_invoice_number"] = return_against_map[
							invoice_data.get("return_against")
						].get("original_cu_invoice_number")
						invoice_data["original_cu_invoice_date"] = return_against_map[
							invoice_data.get("return_against")
						].get("original_cu_invoice_date")

		return list(invoice_map.values())

	def group_by_dimension(self, data):
		grouped_data = defaultdict(list)
		for row in data:
			dimension_value = row.get("accounting_dimension_value") or _("No Dimension")
			grouped_data[dimension_value].append(row)

		final_data = []
		for dimension_value in sorted(grouped_data.keys()):
			group_rows = grouped_data[dimension_value]
			final_data.append(
				{
					"accounting_dimension_value": dimension_value,
					"party_name": None,
					"taxable_amount": sum(flt(row.get("taxable_amount")) for row in group_rows),
					"vat_amount": sum(flt(row.get("vat_amount")) for row in group_rows),
					"currency": self.currency,
					"is_group_header": True,
				}
			)
			for row in group_rows:
				row["indent"] = 1
				final_data.append(row)

		return final_data


class KenyaPurchaseTaxReportFilters(TypedDict):
	company: str | None
	from_date: str | None
	to_date: str | None
	is_return: int | None
	item_tax_template: str | None
	taxes_and_charges_template: str | None
	accounting_dimension: str | None


def execute(filters: KenyaPurchaseTaxReportFilters | None = None):
	report = KenyaPurchaseTaxReport(filters)
	return report.run()


class KenyaPurchaseTaxReport(TaxReport):
	def __init__(self, filters: KenyaPurchaseTaxReportFilters | None = None):
		super().__init__()
		self.filters = filters or {}
		self.company = self.filters.get("company")
		self.from_date = self.filters.get("from_date")
		self.to_date = self.filters.get("to_date")
		self.item_tax_template = self.filters.get("item_tax_template")
		self.taxes_and_charges_template = self.filters.get("taxes_and_charges_template")

	def run(self):
		columns = self.get_columns()
		data = self.get_data("purchase")

		return columns, data, None, None, self.get_report_summary()

	def get_columns(self):
		columns = [
			{
				"label": _("Supplier PIN"),
				"fieldname": "tax_id",
				"fieldtype": "Data",
				"width": 150,
			},
			{
				"label": _("Supplier Name"),
				"fieldname": "party_name",
				"fieldtype": "Data",
				"width": 200,
			},
			{
				"label": _("Invoice Number"),
				"fieldname": "invoice_number",
				"fieldtype": "Link",
				"options": "Purchase Invoice",
				"width": 200,
			},
			{
				"label": _("Invoice Date"),
				"fieldname": "invoice_date",
				"fieldtype": "Date",
				"width": 200,
			},
			{
				"label": _("ETR Invoice Number"),
				"fieldname": "etr_invoice_number",
				"fieldtype": "Data",
				"width": 200,
			},
			{
				"label": _("Supplier Invoice No"),
				"fieldname": "supplier_invoice_no",
				"fieldtype": "Data",
				"width": 200,
			},
			{
				"label": _("Supplier Invoice Date"),
				"fieldname": "supplier_invoice_date",
				"fieldtype": "Date",
				"width": 200,
			},
			{
				"label": _("Taxable Amount"),
				"fieldname": "taxable_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 200,
			},
			{
				"label": _("VAT Amount"),
				"fieldname": "vat_amount",
				"fieldtype": "Currency",
				"options": "currency",
				"width": 200,
			},
			{
				"label": _("Currency"),
				"fieldname": "currency",
				"fieldtype": "Data",
				"width": 200,
				"hidden": 1,
			},
			{
				"label": _("Original CU Invoice Number"),
				"fieldname": "original_cu_invoice_number",
				"fieldtype": "Data",
				"width": 200,
			},
			{
				"label": _("Original CU Invoice Date"),
				"fieldname": "original_cu_invoice_date",
				"fieldtype": "Date",
				"width": 200,
			},
			{
				"label": _("Return Against"),
				"fieldname": "return_against",
				"fieldtype": "Link",
				"options": "Purchase Invoice",
				"width": 200,
			},
		]

		if self.filters.get("accounting_dimension"):
			dimension = self.filters.get("accounting_dimension")
			columns.insert(
				0,
				{
					"label": _(dimension),
					"fieldname": "accounting_dimension_value",
					"fieldtype": "Link",
					"options": dimension,
					"width": 200,
				},
			)

		return columns

	def get_report_summary(self):
		return [
			{
				"value": self.registered_suppliers_total_purchases,
				"indicator": "Green",
				"label": _("Registered Suppliers Total Purchases"),
				"datatype": "Currency",
				"currency": self.currency,
			},
			{
				"value": self.registered_suppliers_total_vat,
				"indicator": "Green",
				"label": _("Registered Suppliers Total VAT"),
				"datatype": "Currency",
				"currency": self.currency,
			},
			{
				"value": self.unregistered_suppliers_total_purchases,
				"indicator": "Green",
				"label": _("Unregistered Suppliers Total Purchases"),
				"datatype": "Currency",
				"currency": self.currency,
			},
			{
				"value": self.unregistered_suppliers_total_vat,
				"indicator": "Green",
				"label": _("Unregistered Suppliers Total VAT"),
				"datatype": "Currency",
				"currency": self.currency,
			},
		]


def _normalize_companies(company):
	if not company:
		return frappe.get_all("Company", pluck="name")

	if isinstance(company, str):
		company = company.strip()
		if not company:
			return frappe.get_all("Company", pluck="name")

		if company.startswith("["):
			try:
				parsed_companies = frappe.parse_json(company)
			except Exception:
				parsed_companies = None

			if isinstance(parsed_companies, list):
				return [item for item in parsed_companies if item]

		if "," in company:
			return [item.strip() for item in company.split(",") if item.strip()]

		return [company]

	if isinstance(company, (list, tuple, set)):
		return [item for item in company if item]

	return [company]


def _get_tax_templates_from_report_data(company, from_date=None, to_date=None):
	purchase_invoice_ = frappe.qb.DocType("Purchase Invoice")
	purchase_invoice_item_ = frappe.qb.DocType("Purchase Invoice Item")

	tax_templates_query = (
		frappe.qb.from_(purchase_invoice_item_)
		.inner_join(purchase_invoice_)
		.on(purchase_invoice_item_.parent == purchase_invoice_.name)
		.select(purchase_invoice_item_.item_tax_template)
		.distinct()
		.where(purchase_invoice_.docstatus == 1)
		.where(purchase_invoice_.company == company)
	)

	if from_date:
		tax_templates_query = tax_templates_query.where(purchase_invoice_.posting_date >= from_date)
	if to_date:
		tax_templates_query = tax_templates_query.where(purchase_invoice_.posting_date <= to_date)

	tax_template_rows = tax_templates_query.run(as_dict=True)
	return sorted({row.get("item_tax_template") for row in tax_template_rows if row.get("item_tax_template")})


@frappe.whitelist()
def download_custom_csv_format(company: str, from_date: str | None = None, to_date: str | None = None):
	if not from_date:
		frappe.throw(_("From Date is required"))
	if not to_date:
		frappe.throw(_("To Date is required"))

	from_date_str = from_date.strftime("%y-%m-%d") if isinstance(from_date, datetime) else from_date
	to_date_str = to_date.strftime("%y-%m-%d") if isinstance(to_date, datetime) else to_date

	private_path = frappe.utils.get_site_path("private", "files")
	os.makedirs(private_path, exist_ok=True)

	companies = _normalize_companies(company)
	if not companies:
		frappe.throw(_("At least one company is required"))

	csv_files = {}

	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

	for company_name in companies:
		tax_templates = _get_tax_templates_from_report_data(company_name, from_date, to_date)
		if not tax_templates:
			continue

		company_abbr = frappe.db.get_value("Company", company_name, "abbr") or ""

		for template_name in tax_templates:
			sanitized_template_name = re.sub(r"[^\w]+", "_", template_name).lower()

			csv_file_name = f"purchase_{sanitized_template_name[:20]}_{company_abbr}_{from_date_str}_to_{to_date_str}_{timestamp}.csv".strip(
				"_"
			)

			full_file_path = os.path.join(private_path, csv_file_name)
			file_url = f"/private/files/{csv_file_name}"

			purchase_invoices = KenyaPurchaseTaxReport(
				{
					"company": company_name,
					"from_date": from_date,
					"to_date": to_date,
					"item_tax_template": template_name,
				}
			).get_data("purchase")

			if not purchase_invoices:
				continue

			with open(full_file_path, "w", newline="") as csvfile:
				writer = csv.writer(csvfile)

				for invoice in purchase_invoices:
					if invoice.get("tax_id"):
						writer.writerow(
							[
								"Local",
								invoice.get("tax_id", ""),
								invoice.get("party_name", ""),
								(
									invoice.get("invoice_date").strftime("%d/%m/%Y")
									if invoice.get("invoice_date")
									else ""
								),
								f"|{(invoice.get('etr_invoice_number', ''))}",
								invoice.get("invoice_number", ""),
								"",
								invoice.get("taxable_amount", ""),
								"",
								f"{'|' + invoice.get('original_cu_invoice_number', '') if invoice.get('return_against') else ''}",
								(
									invoice.get("original_cu_invoice_date").strftime("%d/%m/%Y")
									if invoice.get("return_against")
									and invoice.get("original_cu_invoice_date")
									else ""
								),
							]
						)

			file_record = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": csv_file_name,
					"file_url": file_url,
					"attached_to_name": company_name,
					"attached_to_doctype": "Purchase Invoice",
					"file_size": os.path.getsize(full_file_path),
					"is_private": 1,
					"file_type": "CSV",
				}
			)
			file_record.insert()

			display_key = f"{company_name} - {template_name}" if len(companies) > 1 else template_name
			csv_files[display_key] = file_url

	return csv_files
