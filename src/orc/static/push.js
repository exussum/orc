import { get } from "./orc.js";

const button = document.getElementById("orc-push-subscribe");
const label = document.getElementById("orc-push-label");

function toBytes(base64url) {
    const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "=");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function register(subscription) {
    const response = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(subscription),
    });
    return response.ok;
}

async function subscribe() {
    button.disabled = true;
    try {
        if ((await Notification.requestPermission()) !== "granted") return;
        const registration = await navigator.serviceWorker.register("/static/push-sw.js");
        const { key } = await get("/api/push/key");
        const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: toBytes(key) });
        if (await register(subscription)) label.textContent = "Notifying this device";
    } catch (error) {
        console.error(error.message);
    } finally {
        button.disabled = false;
    }
}

async function resync() {
    const registration = await navigator.serviceWorker.getRegistration("/static/push-sw.js");
    const subscription = await registration?.pushManager.getSubscription();
    if (subscription && (await register(subscription))) label.textContent = "Notifying this device";
}

if ("serviceWorker" in navigator && "PushManager" in window) {
    button.addEventListener("click", subscribe);
    resync();
} else {
    button.disabled = true;
    label.textContent = "Notifications unsupported";
}
