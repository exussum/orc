import { get, wire } from "./orc.js";

async function setPresence(el, action) {
    if (await get(`/api/presence/${encodeURIComponent(el.dataset.id)}/${action}`, el)) location.reload();
}

async function runCheck(el) {
    if (await get("/api/presence/run", el)) location.reload();
}

wire(".orc-checkin", "click", (el) => setPresence(el, "checkin"));
wire(".orc-expire", "click", (el) => setPresence(el, "expire"));
wire("#orc-presence-run", "click", runCheck);
