frappe.ui.form.on('HD Ticket', {
    refresh: function(frm) {

        if (!frm.is_new()) {

            frm.add_custom_button('Sync Zendesk Tickets', function() {

                frappe.call({
                    method: "zendesk_integration.api.manual_sync",
                    args: {
                        status: "all",
                        max_pages: 2
                    },
                    freeze: true,
                    freeze_message: "Syncing Zendesk Tickets...",

                    callback: function(r) {
                        if (r.message) {
                            frappe.msgprint({
                                title: "Sync Complete",
                                message: JSON.stringify(r.message),
                                indicator: "green"
                            });
                        }
                    }
                });

            }, "Actions");
        }
    }
});