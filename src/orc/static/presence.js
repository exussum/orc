async function checkin(el) {
    el.disabled = true;
    fetch(`/api/presence/${encodeURIComponent(el.dataset.name)}/checkin`, {
        method: "POST",
        headers: { "orc-version": version },
    }).then(() => location.reload());
}

async function expire(el) {
    el.disabled = true;
    fetch(`/api/presence/${encodeURIComponent(el.dataset.name)}/expire`, {
        method: "POST",
        headers: { "orc-version": version },
    }).then(() => location.reload());
}

async function runCheck(el) {
    el.disabled = true;
    fetch(`/api/presence/run`, {
        method: "POST",
        headers: { "orc-version": version },
    }).then(() => location.reload());
}

document.querySelectorAll(".orc-checkin").forEach((el) => {
    el.addEventListener("click", (e) => checkin(e.currentTarget));
});

document.querySelectorAll(".orc-expire").forEach((el) => {
    el.addEventListener("click", (e) => expire(e.currentTarget));
});

document.querySelector("#orc-presence-run").addEventListener("click", (e) => runCheck(e.currentTarget));
