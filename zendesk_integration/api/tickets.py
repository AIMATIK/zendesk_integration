import frappe
import requests


@frappe.whitelist()
def sync_zendesk_tickets():
    settings = frappe.get_single("Zendesk Settings")

    subdomain = settings.subdomain
    email = settings.email
    api_token = settings.get_password("api_token")

    base_url = f"https://{subdomain}.zendesk.com/api/v2/tickets.json"
    auth = (f"{email}/token", api_token)

    url = base_url
    created = 0

    while url:
        res = requests.get(url, auth=auth)

        if res.status_code != 200:
            frappe.log_error(
                title="Zendesk API Error",
                message=f"Status: {res.status_code}\nResponse: {res.text}"
            )
            break

        data = res.json()

        for t in data.get("tickets", []):

            zendesk_id = str(t.get("id"))

            # 🔁 Prevent duplicate import
            if frappe.db.exists("HD Ticket", {"custom_zendesk_id": zendesk_id}):
                continue

            # 📄 Create Ticket
            doc = frappe.get_doc({
                "doctype": "HD Ticket",
                "subject": t.get("subject") or f"Zendesk Ticket #{zendesk_id}",
                "description": t.get("description") or "",
                "status": map_status(t.get("status")),
                "priority": map_priority(t.get("priority")),
                "custom_zendesk_id": zendesk_id
            })

            doc.insert(ignore_permissions=True)
            created += 1

            # 💬 Fetch & Attach Comments
            comments = fetch_comments(zendesk_id, auth, subdomain)

            for c in comments:
                body = c.get("body")
                if body:
                    doc.add_comment("Comment", body)

        # 🔄 Pagination
        url = data.get("next_page")

    return f"{created} tickets imported successfully"


# ─────────────────────────────────────────────
# 🔹 STATUS MAPPING
# ─────────────────────────────────────────────
def map_status(status):
    return {
        "new": "Open",
        "open": "Open",
        "pending": "Pending",
        "hold": "Pending",
        "solved": "Closed",
        "closed": "Closed"
    }.get(status, "Open")


# ─────────────────────────────────────────────
# 🔹 PRIORITY MAPPING
# ─────────────────────────────────────────────
def map_priority(priority):
    return {
        "low": "Low",
        "normal": "Medium",
        "high": "High",
        "urgent": "High"
    }.get(priority, "Medium")


# ─────────────────────────────────────────────
# 🔹 FETCH COMMENTS
# ─────────────────────────────────────────────
def fetch_comments(ticket_id, auth, subdomain):
    url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}/comments.json"

    res = requests.get(url, auth=auth)

    if res.status_code != 200:
        frappe.log_error(
            title="Zendesk Comments Error",
            message=f"Ticket ID: {ticket_id}\nStatus: {res.status_code}\nResponse: {res.text}"
        )
        return []

    return res.json().get("comments", [])