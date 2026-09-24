import { get, wire } from "./orc.js";

async function checkin(el) {
    if (await get(`/api/presence/${encodeURIComponent(el.dataset.id)}/checkin`, el)) location.reload();
}

async function expire(el) {
    if (await get(`/api/presence/${encodeURIComponent(el.dataset.id)}/expire`, el)) location.reload();
}

async function runCheck(el) {
    if (await get("/api/presence/run", el)) location.reload();
}

wire(".orc-checkin", "click", checkin);
wire(".orc-expire", "click", expire);
wire("#orc-presence-run", "click", runCheck);
