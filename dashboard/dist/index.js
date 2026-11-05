// Inbox Chief — web dashboard stub. The dashboard/ folder exists to mount the backend
// (plugin_api.py) that the Hermes Desktop panel (desktop/plugin.js) reads; the tab is hidden.
// If someone opens /inbox-chief in the web dashboard, point them at Desktop.
(function () {
  var sdk = window.__HERMES_PLUGIN_SDK__;
  var plugins = window.__HERMES_PLUGINS__;
  if (!sdk || !plugins) return;
  var h = sdk.React.createElement;
  function InboxChiefPage() {
    return h(
      "div",
      { style: { padding: "1.5rem", maxWidth: "40rem" } },
      h("h2", null, "Inbox Chief"),
      h("p", null, "The inbox queue and go / no-go panel lives in Hermes Desktop (right-side pane \u201cInbox\u201d). In chat, use /inbox.")
    );
  }
  plugins.register("inbox-chief", InboxChiefPage);
})();
