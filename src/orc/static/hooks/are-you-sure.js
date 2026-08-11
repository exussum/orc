orcHooks.ensure({
    onCommand(what, el, url) {
        if (new Date().getHours() >= 9) return true;
        const soundOrLight =
            url.startsWith("/api/run/")
            || url.startsWith("/api/room/")
            || (url.startsWith("/api/device/") && !url.startsWith("/api/device/ac/"));
        return !soundOrLight || confirm(`It's after hours.  Go ahead with: ${what}?`);
    },
});
