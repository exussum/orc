orc.hooks.register({
    onPress(buttonName) {
        if (buttonName !== "Calendar") return true;
        window.location.assign("/api/calendar/choose/");
        return false;
    },
});
