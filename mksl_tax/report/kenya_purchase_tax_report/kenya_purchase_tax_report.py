# Copyright (c) 2026, Mksl and contributors
# For license information, please see license.txt

from mksl_tax.report.mksl_purchase_tax_report.mksl_purchase_tax_report import execute


def execute_report(filters=None):
	return execute(filters)


def execute(filters=None):
	return execute_report(filters)
