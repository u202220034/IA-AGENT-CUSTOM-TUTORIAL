sap.ui.define([
    "sap/ui/core/UIComponent"
], function (UIComponent) {
    "use strict";

    // OJO AQUÍ: Debe decir "help.Component" para coincidir con tu manifest
    return UIComponent.extend("help.Component", { 
        metadata: {
            manifest: "json"
        },

        init: function () {
            UIComponent.prototype.init.apply(this, arguments);
            
            // Lógica del botón
            var oRenderer = sap.ushell.Container.getRenderer("fiori2");
            if (oRenderer) {
                oRenderer.addHeaderItem({
                    id: "GlobalHelpBtn",
                    icon: "sap-icon://sys-help",
                    tooltip: "Ir a Cloud Foundry",
                    text: "Help",
                    position: "end",
                    press: function () {
                        var sUrl = "https://btpassistantNTT.cfapps.us10-001.hana.ondemand.com";
                        window.open(sUrl, "_blank");
                    }
                }, true, true);
            }
        }
    });
});