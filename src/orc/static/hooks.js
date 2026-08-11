class Hooks {
    constructor() {
        this.plugins = [];
        this.ensures = [];
    }

    register(plugin) {
        if (!plugin || typeof plugin.onPress !== "function") {
            throw new TypeError("Plugin must be an object with an onPress(buttonName, el) function");
        }
        this.plugins.push(plugin);
        return this;
    }

    ensure(hook) {
        if (!hook || typeof hook.onCommand !== "function") {
            throw new TypeError("Ensure hook must be an object with an onCommand(what, el) function");
        }
        this.ensures.push(hook);
        return this;
    }

    async onPress(buttonName, el) {
        for (const plugin of this.plugins) {
            if (await plugin.onPress(buttonName, el) !== true) return false;
        }
        return true;
    }

    async onCommand(what, el, url) {
        for (const hook of this.ensures) {
            if (await hook.onCommand(what, el, url) !== true) return false;
        }
        return true;
    }
}

window.orcHooks = new Hooks();
