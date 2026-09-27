import { get } from "./orc.js";

const button = document.getElementById("orc-push-subscribe");
const label = document.getElementById("orc-push-label");

function toBytes(base64url) {
    const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "=");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function register(subscription, greet) {
    const response = await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...subscription.toJSON(), greet }),
    });
    return response.ok;
}

function sameKey(subscription, key) {
    const current = new Uint8Array(subscription.options.applicationServerKey);
    return current.length === key.length && current.every((byte, i) => byte === key[i]);
}

async function enroll(registration, greet) {
    try {
        const key = toBytes((await get("/api/push/key")).key);
        let subscription = await registration.pushManager.getSubscription();
        if (subscription && !sameKey(subscription, key)) {
            await subscription.unsubscribe();
            subscription = null;
        }
        subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
        if (await register(subscription, greet)) label.textContent = "Notifying this device";
    } catch (error) {
        console.error(error.message);
    }
}

async function subscribe() {
    button.disabled = true;
    try {
        if ((await Notification.requestPermission()) !== "granted") return;
        await enroll(await navigator.serviceWorker.register("/static/push-sw.js"), true);
    } finally {
        button.disabled = false;
    }
}

async function resync() {
    const registration = await navigator.serviceWorker.getRegistration("/static/push-sw.js");
    if (registration && (await registration.pushManager.getSubscription())) await enroll(registration, false);
}

if ("serviceWorker" in navigator && "PushManager" in window) {
    button.addEventListener("click", subscribe);
    resync();
} else {
    button.disabled = true;
    label.textContent = "Notifications unsupported";
}
