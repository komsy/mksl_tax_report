import frappe


def execute():
	reports = [
		{
			"name": "Mksl Sales Tax Report",
			"ref_doctype": "Sales Invoice",
			"report_type": "Script Report",
			"module": "Mksl Tax",
			"report_name": "Mksl Sales Tax Report",
			"label": "Mksl Sales Tax Report",
		},
		{
			"name": "Mksl Purchase Tax Report",
			"ref_doctype": "Purchase Invoice",
			"report_type": "Script Report",
			"module": "Mksl Tax",
			"report_name": "Mksl Purchase Tax Report",
			"label": "Mksl Purchase Tax Report",
		},
	]

	for report in reports:
		doc = frappe.db.exists("Report", report["name"]) and frappe.get_doc("Report", report["name"]) or frappe.new_doc("Report")
		doc.update({
			"name": report["name"],
			"report_name": report["report_name"],
			"ref_doctype": report["ref_doctype"],
			"report_type": report["report_type"],
			"module": report["module"],
			"is_standard": "Yes",
			"add_total_row": 0,
			"disable_prepared_report": 0,
			"prepared_report": 0,
			"disabled": 0,
			"label": report["label"],
		})
		doc.insert(ignore_permissions=True) if not frappe.db.exists("Report", report["name"]) else doc.save(ignore_permissions=True)

	workspace_name = "Mksl Tax Reports"
	if not frappe.db.exists("Workspace", workspace_name):
		workspace = frappe.get_doc({
			"doctype": "Workspace",
			"name": workspace_name,
			"title": workspace_name,
			"label": workspace_name,
			"module": "Mksl Tax",
			"icon": "file",
			"public": 1,
			"is_hidden": 0,
			"links": [
				{
					"type": "Link",
					"label": "Mksl Sales Tax Report",
					"link_type": "Report",
					"link_to": "Mksl Sales Tax Report",
					"is_query_report": 1,
				},
				{
					"type": "Link",
					"label": "Mksl Purchase Tax Report",
					"link_type": "Report",
					"link_to": "Mksl Purchase Tax Report",
					"is_query_report": 1,
				},
			],
		})
		workspace.insert(ignore_permissions=True)
