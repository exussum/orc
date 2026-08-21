function askDelay(what) {
    return new Promise((resolve) => {
        const dialog = document.getElementById("orc-delay-dialog");
        const keep = document.getElementById("orc-delay-dialog-keep");
        document.getElementById("orc-delay-dialog-text").textContent = `Run "${what}" now?`;
        keep.checked = true;
        dialog.returnValue = "";
        dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok" ? keep.checked : null), { once: true });
        dialog.showModal();
    });
}

function askConfirm(text) {
    return new Promise((resolve) => {
        const dialog = document.getElementById("orc-confirm-dialog");
        document.getElementById("orc-confirm-dialog-text").textContent = text;
        dialog.returnValue = "";
        dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
        dialog.showModal();
    });
}

orcHooks.ensure({
    async onCommand(what, el, url) {
        const delay = el?.dataset.delay && el.dataset.delay !== "0:00:00";
        if (delay) {
            const keepDelay = await askDelay(what);
            if (keepDelay === null) return false;
            if (!keepDelay) el.dataset.skipDelay = "1";
        } else if (new Date().getHours() < 9) {
            const disruptive =
                url.startsWith("/api/run/")
                || url.startsWith("/api/room/")
                || (url.startsWith("/api/device/") && !url.startsWith("/api/device/ac/"));
            if (disruptive && !(await askConfirm(`It's after hours.  Go ahead with: ${what}?`))) return false;
        }
        return true;
    },
});
