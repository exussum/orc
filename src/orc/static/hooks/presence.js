orcHooks.ensure({
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
            const response = await fetch("/api/presence/run?ignore-version=1");
            if (response.ok) version = (await response.json()).version;
        } catch {
        }
        return true;
    },
});
