function toBytes(base64url) {
    const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "=");
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function send(method, body) {
    const response = await fetch("/api/push/subscribe", { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return response.ok;
}

function sameKey(subscription, key) {
    const current = new Uint8Array(subscription.options.applicationServerKey);
    return current.length === key.length && current.every((byte, i) => byte === key[i]);
}

async function enroll(registration, greet) {
    try {
        const key = toBytes((await orc.get("/api/push/key")).key);
        let subscription = await registration.pushManager.getSubscription();
        if (subscription && !sameKey(subscription, key)) {
            await send("DELETE", { endpoint: subscription.endpoint });
            await subscription.unsubscribe();
            subscription = null;
        }
        subscription ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
        await send("POST", { ...subscription.toJSON(), greet });
    } catch (error) {
        console.error(error.message);
    }
}

async function subscribe() {
    if ((await Notification.requestPermission()) !== "granted") return;
    await enroll(await navigator.serviceWorker.register("/static/push-sw.js"), true);
    if (navigator.userAgent.includes("Macintosh")) document.getElementById("orc-meet-dialog").showModal();
}

async function unsubscribe() {
    try {
        const registration = await navigator.serviceWorker.getRegistration("/static/push-sw.js");
        const subscription = registration && (await registration.pushManager.getSubscription());
        if (subscription) {
            await send("DELETE", { endpoint: subscription.endpoint });
            await subscription.unsubscribe();
        }
    } catch (error) {
        console.error(error.message);
    }
}

async function resync() {
    const registration = await navigator.serviceWorker.getRegistration("/static/push-sw.js");
    if (registration && (await registration.pushManager.getSubscription())) await enroll(registration, false);
}

if ("serviceWorker" in navigator && "PushManager" in window) {
    orc.hooks.register({
        async onPress(buttonName) {
            if (buttonName === "Enable Notifications") await subscribe();
            else if (buttonName === "Disable Notifications") await unsubscribe();
            else return true;
            return false;
        },
    });
    if (window.location.pathname === "/system/") resync();
}
