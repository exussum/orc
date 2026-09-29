const API = "/api/react/rules/";
const fmtTime = (iso) => new Date(iso).toLocaleString([], { hour: "2-digit", minute: "2-digit", hour12: true });

const TEMPLATES = `
<template id="react-dialog-tpl">
    <dialog id="react-dialog" class="orc-dialog w-96 max-w-full" style="top:2rem;translate:-50% 0;max-height:calc(100vh - 4rem);overflow-y:auto" autofocus>
        <h2 class="text-xl font-semibold mb-3">React</h2>
        <ul id="react-list" class="space-y-1 mb-4"></ul>
        <p id="react-error" class="text-red-400 text-sm hidden"></p>
        <div class="flex justify-end">
            <button type="button" id="react-close" class="orc-btn">Close</button>
        </div>
    </dialog>
</template>
<template id="react-item-tpl">
    <li class="flex items-center justify-between gap-3 border border-white/10 rounded px-2 py-1">
        <div class="flex flex-col min-w-0">
            <span data-name class="text-sm break-words"></span>
            <span data-until class="text-gray-400 text-xs hidden"></span>
        </div>
        <label class="inline-flex items-center cursor-pointer shrink-0">
            <input type="checkbox" class="sr-only peer">
            <div class="orc-switch"></div>
        </label>
    </li>
</template>`;

let dialog;
let templates;

function clone(id) {
    return templates.querySelector(`#${id}`).content.firstElementChild.cloneNode(true);
}

function fail(message) {
    const err = dialog.querySelector("#react-error");
    err.textContent = message;
    err.classList.remove("hidden");
}

async function send(url, method, el) {
    el.disabled = true;
    try {
        const response = await fetch(url, { method });
        if (!response.ok) fail(`Failed (${response.status})`);
    } catch (error) {
        console.error(error.message);
        fail("Failed");
    } finally {
        el.disabled = false;
    }
}

function build() {
    const holder = document.createElement("div");
    holder.innerHTML = TEMPLATES;
    templates = holder;
    dialog = clone("react-dialog-tpl");
    document.body.appendChild(dialog);
    dialog.querySelector("#react-close").onclick = () => dialog.close();
}

function renderRules(rules) {
    const list = dialog.querySelector("#react-list");
    list.replaceChildren();
    if (!rules.length) {
        const li = document.createElement("li");
        li.className = "text-gray-400 text-sm";
        li.textContent = "No react rules configured.";
        list.appendChild(li);
        return;
    }
    for (const rule of rules) {
        const li = clone("react-item-tpl");
        li.querySelector("[data-name]").textContent = rule.name;
        const until = li.querySelector("[data-until]");
        if (rule.sleeping_until) {
            until.textContent = `Sleeping until ${fmtTime(rule.sleeping_until)}`;
            until.classList.remove("hidden");
        }
        const toggle = li.querySelector("input");
        toggle.checked = !rule.sleeping_until;
        toggle.onchange = async () => {
            await send(API + rule.id + "/sleep", toggle.checked ? "DELETE" : "POST", toggle);
            refresh();
        };
        list.appendChild(li);
    }
}

async function refresh() {
    const data = await orc.get(API, null, () => fail("Failed to load rules."));
    if (data) renderRules(data.rules);
}

orc.hooks.register({
    async onPress(buttonName) {
        if (buttonName !== "React") return true;
        if (!dialog) build();
        dialog.querySelector("#react-error").classList.add("hidden");
        dialog.showModal();
        await refresh();
        return false;
    },
});
