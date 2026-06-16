const startEl = document.querySelector("#orc-theme-select-start");
const endEl = document.querySelector("#orc-theme-select-end");
const selectEl = document.querySelector("#orc-theme-select");
const scheduleEl = document.querySelectorAll(".orc-theme-schedule");

async function set_theme() {
    fetch("/api/schedule/set_theme", {
        method: "POST",
        headers: { "orc-version": version },
        body: new URLSearchParams({ start: startEl.value, end: endEl.value, theme: selectEl.value }),
    }).then(() => location.reload());
}

async function run(el) {
    await call(`/api/schedule/${el.dataset.id}/run`, el);
}

async function pause(el) {
    await call(`/api/schedule/${el.dataset.id}/pause`, el, () => {
        el.checked = !el.checked;
    });
}

function todayDate() {
    const now = new Date();
    return now.getFullYear() + "-" + (now.getMonth() + 1) + "-" + now.getDate();
}

function formUpdated() {
    document.querySelector("#orc-theme-submit").disabled = selectEl.value && !(startEl.value && endEl.value);
}

document.querySelectorAll(".orc-runner").forEach((el) => {
    el.addEventListener("click", (e) => run(e.currentTarget));
});

document.querySelectorAll(".orc-enable").forEach((el) => {
    el.addEventListener("change", (e) => pause(e.currentTarget));
});

document.querySelectorAll(".orc-theme-changer").forEach((el) => {
    el.addEventListener("change", formUpdated);
});

selectEl.addEventListener("change", (e) => {
    scheduleEl.forEach((el) => {
        el.style.display = e.target.value === "" ? "none" : "block";
    });
    if (e.target.value === "") {
        startEl.value = "";
        endEl.value = "";
    }
});

selectEl.value = window.orcThemeName;
selectEl.dispatchEvent(new Event("change"));

startEl.min = todayDate();
startEl.addEventListener("change", (e) => {
    if (e.target.value) {
        endEl.disabled = false;
        endEl.value = endEl.min = e.target.value;
    } else {
        endEl.disabled = true;
        endEl.value = null;
    }
    formUpdated();
});
