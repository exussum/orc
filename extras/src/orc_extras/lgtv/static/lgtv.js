async function notifyPairing(durationSec) {
    if (!("Notification" in window)) return () => {};
    let permission = Notification.permission;
    if (permission === "default") permission = await Notification.requestPermission();
    if (permission !== "granted") return () => {};
    const notification = new Notification("Pair LG TV", {
        body: "Accept the pairing prompt on the TV.",
        tag: "orc-pair-lg-tv",
    });
    notification.onclick = () => notification.close();
    const timer = setTimeout(() => notification.close(), durationSec * 1000);
    return () => { clearTimeout(timer); notification.close(); };
}

orcHooks.register({
    async onPress(buttonName, el) {
        if (buttonName !== "Pair LG TV") return true;
        const dismiss = await notifyPairing(parseFloat(el.dataset.duration));
        const q = el.dataset.device ? `?device=${encodeURIComponent(el.dataset.device)}` : "";
        await get(`/api/run/Pair LG TV${q}`, el);
        dismiss?.();
        return false;
    },
});
