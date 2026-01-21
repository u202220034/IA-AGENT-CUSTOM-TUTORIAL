sap.ui.define([
    "sap/ui/core/mvc/Controller",
    "sap/ui/model/json/JSONModel",
    "sap/m/MessageToast",
    "sap/m/MessageBox"
], function (Controller, JSONModel, MessageToast, MessageBox) {
    "use strict";

    return Controller.extend("faqv2.controller.Main", {

        onInit: function () {
            this._oDialog = null;
            this._oEditDialog = null;

            this.getView().setModel(new sap.ui.model.json.JSONModel({ items: [] }), "pending");
            this.getView().setModel(new sap.ui.model.json.JSONModel({ items: [] }), "active");
            this.getView().setModel(new sap.ui.model.json.JSONModel({ items: [] }), "deleted");

            this._sSearch = "";
            this._oDateRange = null;
            this._currentTab = "PENDING";

            this._loadPending();
            this._loadActive();
            this._loadDeleted();
        },

        _getBackendUrl: function(sEndpoint) {
            // 1. Obtenemos la ruta absoluta donde vive "faqv2" (tu app)
            // Esto resolverá algo como: /cp.portal/ui5appruntime/faqv2/~version~/
            var sAppPath = sap.ui.require.toUrl("faqv2"); 
            
            // 2. Unimos la ruta de la app con tu endpoint de backend
            // Aseguramos que no haya doble barra //
            if (sAppPath.endsWith("/")) {
                sAppPath = sAppPath.slice(0, -1);
            }
            return sAppPath + "/backend" + sEndpoint;
        },


        _getContextObject: function (oEvent) {
            const oSource = oEvent.getSource();
            const aModels = ["pending", "active", "deleted"];

            for (let sModel of aModels) {
                const oCtx = oSource.getBindingContext(sModel);
                if (oCtx) {
                    return oCtx.getObject();
                }
            }
            return null;
        },

        _loadPending: async function () {
            // USAMOS LA NUEVA FUNCIÓN
            const sUrl = this._getBackendUrl("/faq/pending");
            
            try {
                const r = await fetch(sUrl, {
                    headers: { "X-User-Role": "ADMIN" }
                });
                
                if (!r.ok) throw new Error("Error en fetch");
                
                this.getView().getModel("pending").setData({ items: await r.json() });
            } catch (e) {
                console.error("Error cargando pending:", e);
            }
        },

        _loadActive: async function () {
             const sUrl = this._getBackendUrl("/faq/active");
             const r = await fetch(sUrl, { headers: { "X-User-Role": "ADMIN" } });
             this.getView().getModel("active").setData({ items: await r.json() });
        },

        _loadDeleted: async function () {
             const sUrl = this._getBackendUrl("/faq/deleted");
             const r = await fetch(sUrl, { headers: { "X-User-Role": "ADMIN" } });
             this.getView().getModel("deleted").setData({ items: await r.json() });
        },

        onTabSelect: function (oEvent) {
            this._currentTab = oEvent.getParameter("key");

            if (this._currentTab === "PENDING") {
                this._loadPending();
            } else if (this._currentTab === "ACTIVE") {
                this._loadActive();
            } else if (this._currentTab === "DELETED") {
                this._loadDeleted();
            }
        },

        onSearch: function (oEvent) {
            this._sSearch = oEvent.getParameter("newValue");
            this._applyFilters();
        },

        onDateFilter: function (oEvent) {
            this._oDateRange = oEvent.getSource().getDateValue()
                ? {
                    from: oEvent.getSource().getDateValue(),
                    to: oEvent.getSource().getSecondDateValue()
                }
                : null;

            this._applyFilters();
        },

        _applyFilters: function () {
            let sTableId;

            if (this._currentTab === "PENDING") {
                sTableId = "pendingTable";
            } else if (this._currentTab === "ACTIVE") {
                sTableId = "activeTable";
            } else if (this._currentTab === "DELETED") {
                sTableId = "deletedTable";
            }

            const oTable = this.byId(sTableId);
            if (!oTable) {
                return;
            }

            const oBinding = oTable.getBinding("items");
            if (!oBinding) {
                return;
            }

            const aFilters = [];

            if (this._sSearch) {
                aFilters.push(
                    new sap.ui.model.Filter(
                        "question",
                        sap.ui.model.FilterOperator.Contains,
                        this._sSearch
                    )
                );
            }

            if (this._oDateRange?.from && this._oDateRange?.to) {
                aFilters.push(
                    new sap.ui.model.Filter(
                        "created_at",
                        sap.ui.model.FilterOperator.BT,
                        this._oDateRange.from.toISOString(),
                        this._oDateRange.to.toISOString()
                    )
                );
            }

            oBinding.filter(aFilters);
        },


        onAnswer: async function (oEvent) {
            const oItem = this._getContextObject(oEvent);
            if (!oItem) {
                return;
            }
            const oView = this.getView();
            
            if (!this._oDialog) {
                this._oDialog = await sap.ui.core.Fragment.load({
                    name: "faqv2.fragment.AnswerDialog",
                    controller: this
                });
                oView.addDependent(this._oDialog);
            }

            const oDialogModel = new JSONModel({
                selected: {
                    aid: oItem.aid,
                    question: oItem.question,
                    answer: ""
                }
            });

            oDialogModel.setDefaultBindingMode("TwoWay");

            this._oDialog.setModel(oDialogModel);
            this._oDialog.open();
        },


        onDelete: function (oEvent) {
            const oItem = this._getContextObject(oEvent);
            if (!oItem) {
                return;
            }

            MessageBox.confirm(
                `¿Eliminar la pregunta:\n"${oItem.question}"?`,
                {
                    title: "Confirmar eliminación",
                    actions: [MessageBox.Action.OK, MessageBox.Action.CANCEL],
                    emphasizedAction: MessageBox.Action.OK,
                    onClose: (sAction) => {
                        if (sAction !== MessageBox.Action.OK) {
                            return;
                        }

                        // CORRECCIÓN AQUÍ: Usar _getBackendUrl
                        const sUrl = this._getBackendUrl("/faq/delete");

                        fetch(sUrl, { 
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "X-User-Role": "ADMIN"
                            },
                            body: JSON.stringify({ aid: oItem.aid })
                        }).then(() => {
                            MessageToast.show("Pregunta eliminada");
                            this.onTabSelect({
                                getParameter: () => this._currentTab || "PENDING"
                            });
                        });
                    }
                }
            );
        },



        onSaveAnswer: async function () {
            const oData = this._oDialog.getModel().getProperty("/selected");
            console.log("DATA A ENVIAR:", oData);

            // CORRECCIÓN AQUÍ
            const sUrl = this._getBackendUrl("/faq/answer");

            try {
                const response = await fetch(sUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-User-Role": "ADMIN"
                    },
                    body: JSON.stringify({
                        aid: oData.aid,
                        answer: oData.answer
                    })
                });

                const result = await response.json();

                if (!response.ok || result.error) {
                    MessageToast.show(result.error || "Error al guardar respuesta");
                    return;
                }

                MessageToast.show("Respuesta guardada");
                this._oDialog.close();
                this._loadPending();

            } catch (error) {
                console.error("Error saving answer:", error);
                MessageToast.show("Error de conexión al guardar");
            }
        },

        onEdit: async function (oEvent) {
            const oItem = this._getContextObject(oEvent);
            if (!oItem) {
                return;
            }
            const oView = this.getView();

            if (!this._oEditDialog) {
                this._oEditDialog = await sap.ui.core.Fragment.load({
                    name: "faqv2.fragment.EditQuestionDialog",
                    controller: this
                });
                oView.addDependent(this._oEditDialog);
            }

            const oModel = new sap.ui.model.json.JSONModel({
                selected: {
                    aid: oItem.aid,
                    question: oItem.question
                }
            });

            oModel.setDefaultBindingMode("TwoWay");
            this._oEditDialog.setModel(oModel);
            this._oEditDialog.open();
        },

        onSaveEdit: async function () {
            const oData = this._oEditDialog.getModel().getProperty("/selected");

            // CORRECCIÓN AQUÍ
            const sUrl = this._getBackendUrl("/faq/update");

            try {
                const response = await fetch(sUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-User-Role": "ADMIN"
                    },
                    body: JSON.stringify(oData)
                });

                const result = await response.json();

                if (!response.ok || result.error) {
                    MessageToast.show(result.error || "Error al actualizar");
                    return;
                }

                MessageToast.show("Pregunta actualizada");
                this._oEditDialog.close();
                this._loadPending();

            } catch (error) {
                console.error("Error updating:", error);
                MessageToast.show("Error de conexión al actualizar");
            }
        },

        onRestore: function (oEvent) {
            const oItem = this._getContextObject(oEvent);
            if (!oItem) {
                return;
            }

            sap.m.MessageBox.confirm(
                "¿Deseas restaurar esta pregunta?",
                {
                    title: "Restaurar pregunta",
                    actions: [sap.m.MessageBox.Action.OK, sap.m.MessageBox.Action.CANCEL],
                    emphasizedAction: sap.m.MessageBox.Action.OK,
                    onClose: (sAction) => {
                        if (sAction !== sap.m.MessageBox.Action.OK) {
                            return;
                        }

                        // CORRECCIÓN AQUÍ
                        const sUrl = this._getBackendUrl("/faq/restore");

                        fetch(sUrl, {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "X-User-Role": "ADMIN"
                            },
                            body: JSON.stringify({ aid: oItem.aid })
                        }).then(() => {
                            MessageToast.show("Pregunta restaurada");
                            this._loadDeleted();
                            this._loadPending(); // vuelve a pendientes
                        });
                    }
                }
            );
        },

        formatDateTime: function (sDate) {
            if (!sDate) {
                return "";
            }

            const oDate = new Date(sDate);

            return sap.ui.core.format.DateFormat.getDateTimeInstance({
                pattern: "dd/MM/yyyy HH:mm"
            }).format(oDate);
        },

        onCloseDialog: function () {
            if (this._oDialog) {
                this._oDialog.setModel(null);
                this._oDialog.close();
            }
        },

        onCloseEditDialog: function () {
            if (this._oEditDialog) {
                this._oEditDialog.setModel(null);
                this._oEditDialog.close();
            }
        },

    });
});
