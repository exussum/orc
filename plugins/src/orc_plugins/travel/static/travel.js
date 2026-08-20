const API = "/api/travel/jobs/";
const fmt = (iso) => new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
const fmtTime = (iso) => new Date(iso).toLocaleString([], { hour: "2-digit", minute: "2-digit", hour12: true });

async function post(url, el, { method = "POST", body } = {}, onFailure = () => {}) {
    if (el) el.disabled = true;
    try {
        const response = await fetch(url, {
            method,
            headers: body !== undefined ? { "Content-Type": "application/json" } : {},
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        if (!response.ok) {
            onFailure((await response.json()).error);
            return false;
        }
        return true;
    } catch (error) {
        console.error(error.message);
        onFailure();
        return false;
    } finally {
        if (el) el.disabled = false;
    }
}

const TEMPLATES = `
<template id="travel-dialog-tpl">
    <dialog id="travel-dialog" class="orc-card text-white p-6 fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 overflow-visible backdrop:bg-black/50">
        <h2 class="text-xl font-semibold mb-3">Travel</h2>
        <ul id="travel-list" class="space-y-1 mb-4"></ul>
        <form id="travel-form" class="flex flex-col gap-2">
            <input name="target" class="orc-input" placeholder="Flight (AA657) or address" list="travel-places">
            <datalist id="travel-places"></datalist>
            <input name="arrive" type="text" class="orc-input" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other">
            <details id="travel-extras-details">
                <summary class="cursor-pointer mb-1">Extras</summary>
                <div id="travel-extras" class="flex flex-col gap-1"></div>
            </details>
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
        <div data-summary class="flex flex-col"></div>
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
        dateFormat: "Y-m-d h:i K",
        defaultDate: "today",
        static: true,
        monthSelectorType: "static",
        minuteIncrement: 10,
        onOpen: (selected) => {
            arrivePrior = selected[0] || null;
        },
        onReady: (selected, str, fp) => {
            fp.calendarContainer.appendChild(arriveButtons(fp));
            fp.calendarContainer.querySelectorAll("input.numInput").forEach((el) => (el.readOnly = true));
        },
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
        const head = j.airport ? `${j.summary} → ${j.airport}` : j.summary;
        const lines = j.late
            ? [head, "LATE — leave now", `Arrive ~${fmtTime(j.eta)} (delayed)`]
            : [head, `Leave ${j.leave_at ? fmt(j.leave_at) : "-"}`, `Arrive ${fmt(j.arrive)}`];
        const summary = li.querySelector("[data-summary]");
        summary.replaceChildren(
            ...lines.map((t, i) => {
                const div = document.createElement("div");
                if (j.late && i > 0) div.className = "text-red-400";
                div.textContent = t;
                return div;
            }),
        );
        const delBtn = li.querySelector("[data-del]");
        delBtn.onclick = async () => {
            await post(API + encodeURIComponent(j.id), delBtn, { method: "DELETE" });
            refresh();
        };
        list.appendChild(li);
    }
}

async function refresh() {
    const now = new Date();
    now.setSeconds(0, 0);
    arrivePicker.set("minDate", now);
    const rounded = new Date(now);
    rounded.setMinutes(Math.ceil(now.getMinutes() / 10) * 10);
    arrivePicker.setDate(rounded);
    const err = dialog.querySelector("#travel-error");
    const data = await get(API, null, () => {
        err.textContent = "Failed to load trips.";
        err.classList.remove("hidden");
    }, false);
    if (!data) return;
    const { jobs, extras, places } = data;
    renderExtras(extras || []);
    renderPlaces(places || []);
    renderJobs(jobs);
}

async function submit(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = {
        target: fd.get("target") || null,
        arrive: arrivePicker.selectedDates[0] ? arrivePicker.selectedDates[0].toISOString() : null,
        extras: fd.getAll("extra"),
    };
    const err = dialog.querySelector("#travel-error");
    const ok = await post(API, e.submitter, { body }, (message) => {
        err.textContent = message || "Failed";
        err.classList.remove("hidden");
    });
    if (!ok) return;
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
