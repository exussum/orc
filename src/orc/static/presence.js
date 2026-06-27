async function checkin(el) {
    if (await get(`/api/presence/${encodeURIComponent(el.dataset.name)}/checkin`, el)) location.reload();
}

async function expire(el) {
    if (await get(`/api/presence/${encodeURIComponent(el.dataset.name)}/expire`, el)) location.reload();
}

async function runCheck(el) {
    if (await get("/api/presence/run", el)) location.reload();
}

document.querySelectorAll(".orc-checkin").forEach((el) => {
    el.addEventListener("click", (e) => checkin(e.currentTarget));
});

document.querySelectorAll(".orc-expire").forEach((el) => {
    el.addEventListener("click", (e) => expire(e.currentTarget));
});

document.querySelector("#orc-presence-run").addEventListener("click", (e) => runCheck(e.currentTarget));
