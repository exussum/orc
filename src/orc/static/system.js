import { get, wire } from "./orc.js";

async function runJob(el) {
    if (await get(`/api/schedule/${el.dataset.queue}/${el.dataset.id}/run`, el)) setTimeout(() => location.reload(), 1000);
}

async function pauseJob(el) {
    const done = await get(`/api/schedule/${el.dataset.queue}/${el.dataset.id}/pause?paused=${el.checked ? 0 : 1}`, el, () => {
        el.checked = !el.checked;
    });
    const state = el.closest("tr").querySelector(".orc-job-state");
    if (done && state) state.textContent = el.checked ? state.dataset.state : "disabled";
}

wire(".orc-job-run", "click", runJob);
wire(".orc-enable", "change", pauseJob);
