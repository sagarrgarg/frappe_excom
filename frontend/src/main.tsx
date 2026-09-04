import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { EXCOM_RELAY_PROJECT_NAME } from "./pushRelay";

// @ts-ignore -- plain JS module from public/
import FrappePushNotification from "../public/frappe-push-notification";

function registerServiceWorker() {
  // @ts-ignore
  window.frappePushNotification = new FrappePushNotification(
    EXCOM_RELAY_PROJECT_NAME
  );

  if ("serviceWorker" in navigator) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window.frappePushNotification as any)
      ?.appendConfigToServiceWorkerURL("/assets/excom/excom/sw.js")
      .then((url: string) => {
        navigator.serviceWorker
          .register(url, { type: "classic" })
          .then((registration: ServiceWorkerRegistration) => {
            // @ts-ignore
            window.frappePushNotification.initialize(registration).then(() => {
              console.info("Excom Push Notification initialized");
            });
          });
      })
      .catch((err: unknown) => {
        // Push is optional: users without the relay permission or browsers that block SW just don't get push.
        console.debug("Excom push not available:", err);
      });
  }
}

if (import.meta.env.DEV) {
  fetch("/api/method/excom.www.excom.get_context_for_dev", {
    method: "POST",
  })
    .then((response) => response.json())
    .then((values) => {
      const v = JSON.parse(values.message);
      // @ts-expect-error
      if (!window.frappe) window.frappe = {};
      // @ts-expect-error
      window.frappe.boot = v;
      // @ts-expect-error
      window.excomServerTimezone =
        v?.time_zone?.system || v?.sysdefaults?.time_zone || "";
      registerServiceWorker();
      ReactDOM.createRoot(
        document.getElementById("root") as HTMLElement
      ).render(
        <React.StrictMode>
          <App />
        </React.StrictMode>
      );
    });
} else {
  registerServiceWorker();
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
