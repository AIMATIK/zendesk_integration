"""
zendesk_integration/zendesk_integration/api.py

Whitelisted endpoints callable from Frappe desk / browser console / Postman.
"""

import frappe
from frappe import _
from zendesk_integration.sync import (
    sync_zendesk_tickets,
    already_synced,
    get_config,
    zd_get,
)


@frappe.whitelist()
def manual_sync(status="all", max_pages=10, with_comments=True):
    """
    Manually trigger a full sync from Zendesk to Frappe HD.

    Usage from browser console (F12 on any Frappe desk page):
        frappe.call({
            method: "zendesk_integration.zendesk_integration.api.manual_sync",
            args: { status: "all", max_pages: 5 },
            callback: r => console.log(r.message)
        });
    """
    if not frappe.has_permission("HD Ticket", "create"):
        frappe.throw(_("Not permitted — you need HD Ticket create permission."),
                     frappe.PermissionError)

    return sync_zendesk_tickets(
        status        = status,
        max_pages     = int(max_pages),
        with_comments = frappe.utils.cint(with_comments),
    )


@frappe.whitelist()
def sync_status():
    """
    Return count of HD Tickets that came from Zendesk.

    frappe.call({ method: "zendesk_integration.zendesk_integration.api.sync_status",
                  callback: r => console.log(r.message) });
    """
    count = frappe.db.count(
        "HD Ticket",
        {"custom_zendesk_id": ["not in", ["", None]]}
    )
    return {
        "synced_tickets": count,
        "message": "{} Zendesk tickets synced into Frappe HD".format(count),
    }


@frappe.whitelist()
def check_ticket(zendesk_id):
    """
    Check whether a specific Zendesk ticket ID is already in Frappe HD.

    frappe.call({ method: "..api.check_ticket", args: {zendesk_id: 12345},
                  callback: r => console.log(r.message) });
    """
    hd_name = already_synced(str(zendesk_id))
    return {
        "zendesk_id": zendesk_id,
        "synced":     bool(hd_name),
        "hd_ticket":  hd_name or None,
    }


@frappe.whitelist()
def test_zendesk_connection():
    """
    Verify that the Zendesk credentials work.
    Returns the authenticated user info from Zendesk.

    frappe.call({ method: "..api.test_zendesk_connection",
                  callback: r => console.log(r.message) });
    """
    data = zd_get("/users/me.json")
    if not data:
        frappe.throw("Could not connect to Zendesk. Check site_config credentials.")

    user = data.get("user", {})
    return {
        "status":    "ok",
        "name":      user.get("name"),
        "email":     user.get("email"),
        "role":      user.get("role"),
        "subdomain": get_config()["base_url"],
    }


@frappe.whitelist()
def get_zendesk_ticket(zendesk_id):
    """
    Fetch a single Zendesk ticket by ID (for testing / debugging).

    frappe.call({ method: "..api.get_zendesk_ticket", args: {zendesk_id: 12345},
                  callback: r => console.log(r.message) });
    """
    data = zd_get("/tickets/{}.json".format(zendesk_id))
    if not data:
        frappe.throw("Zendesk ticket {} not found or API error.".format(zendesk_id))
    return data.get("ticket", {})
    
    
@frappe.whitelist()
def auto_sync():
    """Scheduler safe sync (no user interaction)"""
    try:
        return sync_zendesk_tickets(
            status="all",
            max_pages=2,
            with_comments=True
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Zendesk Auto Sync Failed")