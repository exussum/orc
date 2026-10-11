orc.hooks.register({
    async onPress(buttonName) {
        if (buttonName !== "Example") return true;
        return false;
    },
});
