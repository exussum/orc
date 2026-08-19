const API = "/api/travel/jobs/";
const fmt = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

const TEMPLATES = `
<template id="travel-dialog-tpl">
    <dialog id="travel-dialog" class="orc-card text-white p-6 fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 overflow-visible backdrop:bg-black/50">
        <h2 class="text-xl font-semibold mb-3">Travel</h2>
        <ul id="travel-list" class="space-y-1 mb-4"></ul>
        <form id="travel-form" class="flex flex-col gap-2">
            <input name="flight" class="orc-input" placeholder="Flight (e.g. AA657)">
            <input name="destination" class="orc-input" placeholder="Address" list="travel-places">
            <datalist id="travel-places"></datalist>
            <input name="arrive" type="text" class="orc-input" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
            <div>
                <p class="mb-1">Extras</p>
                <div id="travel-extras" class="flex flex-col gap-1"></div>
            </div>
            <p id="travel-error" class="text-red-400 text-sm hidden"></p>
            <div class="flex justify-end gap-2">
                <button type="button" id="travel-close" class="orc-btn">Close</button>
                <button id="travel-add" class="orc-btn">Add trip</button>
            </div>
        </form>
    </dialog>
</template>
<template id="travel-item-tpl">
    <li class="flex items-center justify-between border border-white/10 rounded px-2 py-1">
        <span data-summary></span>
        <button type="button" data-del class="text-red-400 ml-2" aria-label="Delete">
            <svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2m-9 0v14a2 2 0 002 2h6a2 2 0 002-2V6"/></svg>
        </button>
    </li>
</template>
<template id="travel-extra-tpl">
    <label class="flex items-center gap-2"><input type="checkbox" name="extra"><span data-name></span></label>
</template>`;

let dialog;
let templates;
let arrivePicker;
let arrivePrior;

function clone(id) {
    return templates.querySelector(`#${id}`).content.firstElementChild.cloneNode(true);
}

function placeholder(tag, text) {
    const el = document.createElement(tag);
    el.className = "text-gray-400 text-sm";
    el.textContent = text;
    return el;
}

function build() {
    const holder = document.createElement("div");
    holder.innerHTML = TEMPLATES;
    templates = holder;
    dialog = clone("travel-dialog-tpl");
    document.body.appendChild(dialog);
    dialog.querySelector("#travel-close").onclick = () => dialog.close();
    dialog.querySelector("#travel-form").addEventListener("submit", submit);
    arrivePicker = flatpickr(dialog.querySelector("[name=arrive]"), {
        enableTime: true,
        dateFormat: "Y-m-d H:i",
        defaultDate: "today",
        static: true,
        monthSelectorType: "static",
        onOpen: (selected) => {
            arrivePrior = selected[0] || null;
        },
        onReady: (selected, str, fp) => fp.calendarContainer.appendChild(arriveButtons(fp)),
    });
}

function renderExtras(extras) {
    const box = dialog.querySelector("#travel-extras");
    box.replaceChildren();
    if (!extras.length) {
        box.appendChild(placeholder("span", "None configured."));
        return;
    }
    for (const { name, minutes } of extras) {
        const label = clone("travel-extra-tpl");
        label.querySelector("input").value = name;
        label.querySelector("[data-name]").textContent = `${name} (+${minutes} min)`;
        box.appendChild(label);
    }
}

function renderPlaces(places) {
    const list = dialog.querySelector("#travel-places");
    list.replaceChildren();
    for (const name of places) {
        const opt = document.createElement("option");
        opt.value = name;
        list.appendChild(opt);
    }
}

function renderJobs(jobs) {
    const list = dialog.querySelector("#travel-list");
    list.replaceChildren();
    if (!jobs.length) {
        list.appendChild(placeholder("li", "No upcoming trips."));
        return;
    }
    for (const j of jobs) {
        const li = clone("travel-item-tpl");
        li.querySelector("[data-summary]").textContent = `${j.summary}${j.airport ? " → " + j.airport : ""} · ${fmt(j.arrive)}`;
        li.querySelector("[data-del]").onclick = async () => {
            await fetch(API + encodeURIComponent(j.id), { method: "DELETE" });
            refresh();
        };
        list.appendChild(li);
    }
}

async function refresh() {
    const { jobs, extras, places } = await (await fetch(API, { cache: "no-store" })).json();
    const now = new Date();
    arrivePicker.set("minDate", now);
    arrivePicker.setDate(now);
    renderExtras(extras || []);
    renderPlaces(places || []);
    renderJobs(jobs);
}

async function submit(e) {
    e.preventDefault();
    const err = dialog.querySelector("#travel-error");
    const fd = new FormData(e.target);
    const body = {
        flight: fd.get("flight") || null,
        destination: fd.get("destination") || null,
        arrive: arrivePicker.selectedDates[0] ? arrivePicker.selectedDates[0].toISOString() : null,
        extras: fd.getAll("extra"),
    };
    const res = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) { err.textContent = (await res.json()).error || "Failed"; err.classList.remove("hidden"); return; }
    e.target.reset();
    refresh();
}

orcHooks.register({
    async onPress(buttonName) {
        if (buttonName !== "Travel") return true;
        if (!dialog) build();
        dialog.querySelector("#travel-error").classList.add("hidden");
        await refresh();
        dialog.showModal();
        return false;
    },
});

function arriveButtons(fp) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "orc-btn";
    cancel.textContent = "Cancel";
    cancel.onclick = () => {
        fp.setDate(arrivePrior, false);
        fp.close();
    };
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "orc-btn";
    ok.textContent = "OK";
    ok.onclick = () => fp.close();
    const bar = document.createElement("div");
    bar.className = "flatpickr-orc-buttons";
    bar.append(cancel, ok);
    return bar;
}
