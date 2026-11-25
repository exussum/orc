import { get, pauseJob, wire } from "./orc.js";

async function runJob(el) {
    if (await get(`/api/schedule/${el.dataset.queue}/${el.dataset.id}/run`, el)) setTimeout(() => location.reload(), 1000);
}

wire(".orc-job-run", "click", runJob);
wire(".orc-enable", "change", pauseJob);
