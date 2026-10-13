const API = "/api/memleak/memory/";
const fmtTime = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

const TEMPLATES = `
<template id="memleak-dialog-tpl">
    <dialog id="memleak-dialog" class="orc-dialog max-w-full" style="top:2rem;translate:-50% 0;max-height:calc(100vh - 4rem);overflow-y:auto;width:48rem" autofocus>
        <h2 class="text-xl font-semibold mb-1">Memory</h2>
        <p id="memleak-since" class="text-gray-400 text-xs mb-3"></p>
        <div class="overflow-x-auto mb-4">
            <table class="text-sm w-full">
                <thead class="text-gray-400 text-left">
                    <tr><th class="pr-3">Location</th><th class="pr-3 text-right">KB</th><th class="pr-3 text-right">KB/h</th><th class="pr-3 text-right">Grown</th><th class="text-right">Since boot</th></tr>
                </thead>
                <tbody id="memleak-rows"></tbody>
            </table>
        </div>
        <p id="memleak-error" class="text-red-400 text-sm hidden"></p>
        <div class="flex justify-end">
            <button type="button" id="memleak-close" class="orc-btn">Close</button>
        </div>
    </dialog>
</template>`;

let dialog;

function fail(message) {
    const err = dialog.querySelector("#memleak-error");
    err.textContent = message;
    err.classList.remove("hidden");
}

function build() {
    const holder = document.createElement("div");
    holder.innerHTML = TEMPLATES;
    dialog = holder.querySelector("#memleak-dialog-tpl").content.firstElementChild.cloneNode(true);
    document.body.appendChild(dialog);
    dialog.querySelector("#memleak-close").onclick = () => dialog.close();
}

function cell(text, right) {
    const td = document.createElement("td");
    td.className = right ? "pr-3 text-right whitespace-nowrap" : "pr-3 break-all";
    td.textContent = text;
    return td;
}

function render(data) {
    dialog.querySelector("#memleak-since").textContent = `${data.samples} samples since ${fmtTime(data.since)}`;
    const body = dialog.querySelector("#memleak-rows");
    body.replaceChildren();
    if (!data.rows.length) {
        const tr = document.createElement("tr");
        const td = cell(`Nothing has grown more than ${data.threshold_kb} KB since boot. Next snapshot at ${fmtTime(data.next_snapshot)}.`, false);
        td.className = "text-gray-400";
        td.colSpan = 5;
        tr.appendChild(td);
        body.appendChild(tr);
        return;
    }
    for (const row of data.rows) {
        const tr = document.createElement("tr");
        tr.append(
            cell(row.location, false),
            cell(row.kb.toFixed(0), true),
            cell(row.kb_per_hour.toFixed(1), true),
            cell(`${row.grown}/${row.intervals}`, true),
            cell(`+${row.since_boot_kb.toFixed(0)}`, true),
        );
        body.appendChild(tr);
    }
}

orc.hooks.register({
    async onPress(buttonName) {
        if (buttonName !== "Memory Leak") return true;
        if (!dialog) build();
        dialog.querySelector("#memleak-error").classList.add("hidden");
        dialog.showModal();
        const data = await orc.get(API, null, () => fail("Failed to load memory stats."));
        if (data) render(data);
        return false;
    },
});
