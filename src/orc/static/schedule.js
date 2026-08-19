const startEl = document.querySelector("#orc-theme-select-start");
const endEl = document.querySelector("#orc-theme-select-end");
const selectEl = document.querySelector("#orc-theme-select");
const scheduleEl = document.querySelectorAll(".orc-theme-schedule");

const startPicker = flatpickr(startEl, {
    dateFormat: "Y-m-d",
    minDate: "today",
    monthSelectorType: "static",
    onChange: () => {
        if (startEl.value) {
            endEl.disabled = false;
            endPicker.set("minDate", startEl.value);
            endPicker.setDate(startEl.value);
        } else {
            endEl.disabled = true;
            endPicker.clear();
        }
        formUpdated();
    },
});
const endPicker = flatpickr(endEl, { dateFormat: "Y-m-d", monthSelectorType: "static", onChange: formUpdated });

async function set_theme() {
    const el = document.getElementById("orc-theme-submit");
    el.disabled = true;
    const container = startProgress(parseFloat(el.dataset.duration));
    try {
        const response = await fetch("/api/schedule/set_theme", {
            method: "POST",
            headers: { "orc-version": version },
            body: new URLSearchParams({ start: startEl.value, end: endEl.value, theme: selectEl.value }),
        });
        if (response.status === 412) hardRefresh();
        else if (response.ok) location.reload();
    } finally {
        if (container) container.style.display = "none";
        el.disabled = false;
    }
}

async function pause(el) {
    await get(`/api/schedule/${el.dataset.id}/pause`, el, () => {
        el.checked = !el.checked;
    });
}

function formUpdated() {
    document.querySelector("#orc-theme-submit").disabled = selectEl.value && !(startEl.value && endEl.value);
}

document.querySelectorAll(".orc-runner").forEach((el) => {
    el.addEventListener("click", (e) => runAction(e.currentTarget));
});

document.querySelectorAll(".orc-enable").forEach((el) => {
    el.addEventListener("change", (e) => pause(e.currentTarget));
});

selectEl.addEventListener("change", (e) => {
    scheduleEl.forEach((el) => {
        el.style.display = e.target.value === "" ? "none" : "block";
    });
    if (e.target.value === "") {
        startPicker.clear();
        endPicker.clear();
    }
});

selectEl.value = window.orcThemeName;
selectEl.dispatchEvent(new Event("change"));
