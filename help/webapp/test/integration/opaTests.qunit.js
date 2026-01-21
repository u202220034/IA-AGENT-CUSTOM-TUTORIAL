/* global QUnit */
QUnit.config.autostart = false;

sap.ui.require(["help/test/integration/AllJourneys"
], function () {
	QUnit.start();
});
