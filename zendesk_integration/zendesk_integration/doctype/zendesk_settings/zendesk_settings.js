// Copyright (c) 2026, Ali Naqi and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Zendesk Settings", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Zendesk Settings', {
    refresh(frm) {
        frm.add_custom_button("Sync Tickets", function() {
            frappe.call({
                method: "zendesk_integration.api.tickets.sync_zendesk_tickets",
                callback: function(r){
                    frappe.msgprint(r.message);
                }
            });
        });
    }
});