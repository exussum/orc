const API = "/api/example/things/";

orcHooks.register({
    async onPress(buttonName) {
        if (buttonName !== "Example") return true;
        return false;
    },
});
