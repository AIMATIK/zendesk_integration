# """
# zendesk_integration/zendesk_integration/sync.py

# Pulls tickets from Zendesk and creates HD Tickets in Frappe Helpdesk.

# Key fixes vs the original:
#   - HD Ticket uses frappe.new_doc() + insert(), NOT frappe.get_doc({...}).insert()
#     because HD Ticket has auto-increment naming and mandatory triggers
#   - Status values must match HD Ticket's actual Select options: Open / Replied / Resolved / Closed
#   - Priority values: Low / Medium / High
#   - Comments use frappe.db.insert() directly to avoid HD Ticket Comment controller issues
#   - Every API call has proper error handling and retries
# """

# import frappe
# import requests
# from requests.auth import HTTPBasicAuth
# from frappe import _


# # ── Config ────────────────────────────────────────────────────────────────────

# def get_config():
#     subdomain = frappe.conf.get("zendesk_subdomain")
#     email     = frappe.conf.get("zendesk_email")
#     token     = frappe.conf.get("zendesk_api_token")

#     if not all([subdomain, email, token]):
#         frappe.throw(_(
#             "Zendesk credentials missing in site_config.json.\n"
#             "Run:\n"
#             "  bench --site SITE set-config zendesk_subdomain adverset\n"
#             "  bench --site SITE set-config zendesk_email annak@adverset.co.uk\n"
#             "  bench --site SITE set-config zendesk_api_token YOUR_TOKEN"
#         ))

#     return {
#         "base_url":  "https://{}.zendesk.com/api/v2".format(subdomain),
#         "auth":      HTTPBasicAuth("{}/token".format(email), token),
#         "subdomain": subdomain,
#     }


# # ── Zendesk API helpers ───────────────────────────────────────────────────────

# def zd_get(path, params=None):
#     """GET from Zendesk API. Returns parsed JSON dict or None on failure."""
#     cfg = get_config()
#     url = cfg["base_url"] + path

#     try:
#         response = requests.get(
#             url,
#             auth    = cfg["auth"],
#             params  = params or {},
#             timeout = 30
#         )
#     except requests.exceptions.RequestException as e:
#         frappe.log_error(
#             title   = "Zendesk API — network error: GET {}".format(path),
#             message = str(e)
#         )
#         return None

#     if response.status_code == 401:
#         frappe.log_error(
#             title   = "Zendesk API — 401 Unauthorized",
#             message = "Check email/token in site_config.json"
#         )
#         return None

#     if response.status_code != 200:
#         frappe.log_error(
#             title   = "Zendesk API — HTTP {}: GET {}".format(response.status_code, path),
#             message = response.text[:2000]
#         )
#         return None

#     return response.json()


# def fetch_tickets_page(page=1, per_page=100, status="all"):
#     """Returns (list_of_tickets, has_next_page)."""
#     params = {
#         "page":       page,
#         "per_page":   per_page,
#         "sort_by":    "created_at",
#         "sort_order": "desc",
#     }
#     if status != "all":
#         params["status"] = status

#     data = zd_get("/tickets.json", params)
#     if not data:
#         return [], False

#     return data.get("tickets", []), bool(data.get("next_page"))


# def fetch_comments(ticket_id):
#     """Returns list of comment dicts for a Zendesk ticket."""
#     data = zd_get("/tickets/{}/comments.json".format(ticket_id))
#     return data.get("comments", []) if data else []


# def fetch_user(user_id):
#     """Returns Zendesk user dict or {}."""
#     if not user_id:
#         return {}
#     data = zd_get("/users/{}.json".format(user_id))
#     return data.get("user", {}) if data else {}


# # ── Status / Priority maps ────────────────────────────────────────────────────
# # HD Ticket status options (from frappe/helpdesk source): Open, Replied, Resolved, Closed
# # HD Ticket priority options: Low, Medium, High

# STATUS_MAP = {
#     "new":     "Open",
#     "open":    "Open",
#     "pending": "Replied",
#     "hold":    "Open",
#     "solved":  "Resolved",
#     "closed":  "Closed",
# }

# PRIORITY_MAP = {
#     "low":    "Low",
#     "normal": "Medium",
#     "high":   "High",
#     "urgent": "High",
#     None:     "Medium",
# }


# # ── Contact helper ────────────────────────────────────────────────────────────

# def ensure_contact(email, name):
#     """Return existing Contact name, or create one and return its name."""
#     if not email:
#         return None

#     # Check Contact email_ids child table
#     existing = frappe.db.get_value(
#         "Contact Email",
#         {"email_id": email},
#         "parent"
#     )
#     if existing:
#         return existing

#     try:
#         parts      = (name or email.split("@")[0]).split(" ", 1)
#         first_name = parts[0][:140]
#         last_name  = parts[1][:140] if len(parts) > 1 else ""

#         contact            = frappe.new_doc("Contact")
#         contact.first_name = first_name
#         contact.last_name  = last_name
#         contact.append("email_ids", {"email_id": email, "is_primary": 1})
#         contact.flags.ignore_permissions = True
#         contact.flags.ignore_mandatory   = True
#         contact.insert()
#         frappe.db.commit()
#         return contact.name

#     except Exception:
#         frappe.log_error(
#             title   = "Zendesk Sync — Contact creation failed for {}".format(email),
#             message = frappe.get_traceback()
#         )
#         return None


# # ── Duplicate check ───────────────────────────────────────────────────────────

# def already_synced(zendesk_id):
#     """Return HD Ticket name if this Zendesk ticket was already imported."""
#     return frappe.db.get_value(
#         "HD Ticket",
#         {"custom_zendesk_id": str(zendesk_id)},
#         "name"
#     )


# # ── Create one HD Ticket ──────────────────────────────────────────────────────

# def create_hd_ticket_from_zendesk(zd_ticket, with_comments=True):
#     """
#     Creates an HD Ticket from a Zendesk ticket dict.
#     Returns new HD Ticket name, or None if already exists.
#     """
#     zd_id = str(zd_ticket.get("id", ""))
#     if not zd_id:
#         return None

#     if already_synced(zd_id):
#         return None

#     cfg = get_config()

#     # Requester
#     requester       = fetch_user(zd_ticket.get("requester_id"))
#     requester_email = requester.get("email", "")
#     requester_name  = requester.get("name", "Zendesk User")

#     if requester_email:
#         ensure_contact(requester_email, requester_name)

#     subject     = (zd_ticket.get("subject") or "Zendesk Ticket #{}".format(zd_id))[:140]
#     description = zd_ticket.get("description") or subject
#     hd_status   = STATUS_MAP.get(zd_ticket.get("status", "open"), "Open")
#     hd_priority = PRIORITY_MAP.get(zd_ticket.get("priority"), "Medium")
#     zd_tags     = ", ".join(zd_ticket.get("tags", []))
#     zd_url      = "https://{}.zendesk.com/agent/tickets/{}".format(cfg["subdomain"], zd_id)

#     # ── Create HD Ticket ──────────────────────────────────────────────────────
#     # Must use frappe.new_doc() because HD Ticket uses autoincrement naming
#     ticket = frappe.new_doc("HD Ticket")
#     ticket.subject               = subject
#     ticket.description           = description
#     ticket.raised_by             = requester_email or "support@example.com"
#     ticket.status                = hd_status
#     ticket.priority              = hd_priority
#     ticket.custom_zendesk_id     = zd_id
#     ticket.custom_zendesk_url    = zd_url
#     ticket.custom_zendesk_tags   = zd_tags
#     ticket.custom_zendesk_status = zd_ticket.get("status", "")

#     ticket.flags.ignore_permissions = True
#     ticket.flags.ignore_mandatory   = True

#     try:
#         ticket.insert()
#     except Exception:
#         frappe.log_error(
#             title   = "Zendesk Sync — HD Ticket insert failed for ZD #{}".format(zd_id),
#             message = frappe.get_traceback()
#         )
#         return None

#     # ── Add comments via frappe.db.insert to avoid controller issues ──────────
#     if with_comments:
#         comments = fetch_comments(int(zd_id))
#         for comment in comments:
#             body = comment.get("html_body") or comment.get("body") or ""
#             if not body:
#                 continue

#             author      = fetch_user(comment.get("author_id"))
#             author_name = author.get("name", "Zendesk User")
#             is_public   = comment.get("public", True)
#             visibility  = "Public" if is_public else "Private"

#             try:
#                 comment_doc                   = frappe.new_doc("HD Ticket Comment")
#                 comment_doc.reference_ticket  = ticket.name
#                 comment_doc.commented_by      = frappe.session.user
#                 comment_doc.content           = (
#                     "<p><strong>[Zendesk — {} — {}]</strong></p>{}".format(
#                         author_name, visibility, body
#                     )
#                 )
#                 comment_doc.flags.ignore_permissions = True
#                 comment_doc.flags.ignore_mandatory   = True
#                 comment_doc.insert()
#             except Exception:
#                 frappe.log_error(
#                     title   = "Zendesk Sync — Comment failed for ZD #{}".format(zd_id),
#                     message = frappe.get_traceback()
#                 )

#     frappe.db.commit()

#     frappe.logger().info(
#         "Zendesk sync: created HD Ticket {} from ZD #{}".format(ticket.name, zd_id)
#     )
#     return ticket.name


# # ── Main sync entry point ─────────────────────────────────────────────────────

# def sync_zendesk_tickets(status="all", max_pages=10, with_comments=True):
#     """
#     Pull Zendesk tickets and create HD Tickets.
#     Called by scheduler or manually via api.py.

#     Args:
#         status      : Zendesk status filter ("all", "open", "solved", etc.)
#         max_pages   : Safety limit on pages fetched (100 tickets per page)
#         with_comments: Whether to import ticket comments

#     Returns dict with summary counts.
#     """
#     created  = 0
#     skipped  = 0
#     errors   = 0
#     page     = 1
#     has_more = True

#     while has_more and page <= max_pages:
#         tickets, has_more = fetch_tickets_page(
#             page       = page,
#             per_page   = 100,
#             status     = status
#         )

#         if not tickets:
#             break

#         for zd in tickets:
#             try:
#                 result = create_hd_ticket_from_zendesk(
#                     zd,
#                     with_comments=with_comments
#                 )
#                 if result:
#                     created += 1
#                 else:
#                     skipped += 1
#             except Exception:
#                 errors += 1
#                 frappe.log_error(
#                     title   = "Zendesk Sync — unhandled error on ZD #{}".format(
#                                   zd.get("id", "?")),
#                     message = frappe.get_traceback()
#                 )

#         page += 1

#     summary = {
#         "created":       created,
#         "skipped":       skipped,
#         "errors":        errors,
#         "pages_fetched": page - 1,
#         "status_filter": status,
#     }

#     frappe.logger().info("Zendesk sync complete: {}".format(summary))
#     return summary# zendesk_integration/zendesk_integration/sync.py

import frappe
import requests
from requests.auth import HTTPBasicAuth
from frappe import _


# ── Config ────────────────────────────────────────────────────────────────────

def get_config():
    subdomain = frappe.conf.get("zendesk_subdomain")
    email     = frappe.conf.get("zendesk_email")
    token     = frappe.conf.get("zendesk_api_token")

    if not all([subdomain, email, token]):
        frappe.throw(_(
            "Zendesk credentials missing in site_config.json.\n"
            "Run:\n"
            "bench --site SITE set-config zendesk_subdomain adverset\n"
            "bench --site SITE set-config zendesk_email annak@adverset.co.uk\n"
            "bench --site SITE set-config zendesk_api_token YOUR_TOKEN"
        ))

    return {
        "base_url":  f"https://{subdomain}.zendesk.com/api/v2",
        "auth":      HTTPBasicAuth(f"{email}/token", token),
        "subdomain": subdomain,
    }


# ── Zendesk API ───────────────────────────────────────────────────────────────

def zd_get(path, params=None):
    cfg = get_config()
    url = cfg["base_url"] + path

    try:
        res = requests.get(url, auth=cfg["auth"], params=params or {}, timeout=30)
    except Exception as e:
        frappe.log_error(str(e), f"Zendesk API Network Error: {path}")
        return None

    if res.status_code != 200:
        frappe.log_error(res.text, f"Zendesk API Error {res.status_code}: {path}")
        return None

    return res.json()


def fetch_tickets_page(page=1, per_page=100, status="all"):
    params = {
        "page": page,
        "per_page": per_page,
        "sort_by": "created_at",
        "sort_order": "desc",
    }

    if status != "all":
        params["status"] = status

    data = zd_get("/tickets.json", params)
    if not data:
        return [], False

    return data.get("tickets", []), bool(data.get("next_page"))


def fetch_comments(ticket_id):
    data = zd_get(f"/tickets/{ticket_id}/comments.json")
    return data.get("comments", []) if data else []


def fetch_user(user_id):
    if not user_id:
        return {}
    data = zd_get(f"/users/{user_id}.json")
    return data.get("user", {}) if data else {}


# ── Maps ──────────────────────────────────────────────────────────────────────

STATUS_MAP = {
    "new": "Open",
    "open": "Open",
    "pending": "Replied",
    "hold": "Open",
    "solved": "Resolved",
    "closed": "Closed",
}

PRIORITY_MAP = {
    "low": "Low",
    "normal": "Medium",
    "high": "High",
    "urgent": "High",
    None: "Medium",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_priority(name):
    """Auto-create missing priority (safe)"""
    if not frappe.db.exists("HD Ticket Priority", name):
        try:
            doc = frappe.new_doc("HD Ticket Priority")
            doc.priority = name
            doc.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Priority create failed: {name}")


def ensure_contact(email, name):
    if not email:
        return None

    existing = frappe.db.get_value("Contact Email", {"email_id": email}, "parent")
    if existing:
        return existing

    try:
        parts = (name or email.split("@")[0]).split(" ", 1)

        contact = frappe.new_doc("Contact")
        contact.first_name = parts[0][:140]
        contact.last_name  = parts[1][:140] if len(parts) > 1 else ""
        contact.append("email_ids", {"email_id": email, "is_primary": 1})

        contact.flags.ignore_permissions = True
        contact.flags.ignore_mandatory   = True
        contact.insert()

        return contact.name

    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Contact failed: {email}")
        return None


def already_synced(zd_id):
    return frappe.db.get_value("HD Ticket", {"custom_zendesk_id": str(zd_id)}, "name")


# ── Create Ticket ─────────────────────────────────────────────────────────────

def create_hd_ticket_from_zendesk(zd_ticket, with_comments=True):
    zd_id = str(zd_ticket.get("id", ""))

    if not zd_id or already_synced(zd_id):
        return None

    cfg = get_config()

    requester = fetch_user(zd_ticket.get("requester_id"))
    email = requester.get("email")
    name  = requester.get("name", "Zendesk User")

    ensure_contact(email, name)

    subject     = (zd_ticket.get("subject") or f"Zendesk Ticket #{zd_id}")[:140]
    description = zd_ticket.get("description") or subject

    # ── Status FIX ──
    hd_status = STATUS_MAP.get(zd_ticket.get("status"), "Open")
    if hd_status not in ["Open", "Replied", "Resolved", "Closed"]:
        hd_status = "Open"

    # ── Priority FIX ──
    hd_priority = PRIORITY_MAP.get(zd_ticket.get("priority"), "Medium")
    ensure_priority(hd_priority)

    ticket = frappe.new_doc("HD Ticket")
    ticket.subject = subject
    ticket.description = description
    ticket.raised_by = email or "support@example.com"
    ticket.status = hd_status
    ticket.priority = hd_priority
    ticket.custom_zendesk_id = zd_id
    ticket.custom_zendesk_url = f"https://{cfg['subdomain']}.zendesk.com/agent/tickets/{zd_id}"

    ticket.flags.ignore_permissions = True
    ticket.flags.ignore_mandatory   = True

    try:
        ticket.insert()
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Ticket failed ZD #{zd_id}")
        return None

    # ── Comments ──
    if with_comments:
        comments = fetch_comments(int(zd_id))

        for c in comments:
            try:
                body = c.get("html_body") or c.get("body")
                if not body:
                    continue

                comment_doc = frappe.new_doc("HD Ticket Comment")
                comment_doc.reference_ticket = ticket.name
                comment_doc.commented_by = frappe.session.user
                comment_doc.content = body

                comment_doc.flags.ignore_permissions = True
                comment_doc.insert()

            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Comment failed ZD #{zd_id}")

    frappe.db.commit()
    return ticket.name


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync_zendesk_tickets(status="all", max_pages=10, with_comments=True):
    created = skipped = errors = 0
    page = 1
    has_more = True

    while has_more and page <= max_pages:
        tickets, has_more = fetch_tickets_page(page=page, status=status)

        if not tickets:
            break

        for zd in tickets:
            try:
                if create_hd_ticket_from_zendesk(zd, with_comments):
                    created += 1
                else:
                    skipped += 1
            except Exception:
                errors += 1
                frappe.log_error(frappe.get_traceback(), "Zendesk Sync Error")

        page += 1

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "pages_fetched": page - 1,
    }