orc.hooks.ensure({
    async onCommand(what, el, url) {
        if (window.location.pathname !== "/") return true;
        if (!url.startsWith("/api/run/")) return true;
        let state = null;
        try {
            state = await (await fetch("/api/presence/state")).json();
        } catch {
            return true;
        }
        if (state.present) return true;
        try {
            await fetch("/api/presence/run");
        } catch {
        }
        return true;
    },
});
