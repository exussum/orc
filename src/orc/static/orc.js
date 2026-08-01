let version = window.orcVersion;

function hardRefresh() {
    window.location.href = window.location.pathname + "?_=" + Date.now();
}

function isInvalidResponse(response) {
    return !response || (response.status >= 400 && response.status < 500);
}

function startProgress(seconds) {
    if (!seconds || seconds <= 2) return;
    const container = document.getElementById("orc-progress");
    const bar = document.getElementById("orc-progress-bar");
    container.style.display = "flex";
    bar.style.transition = "none";
    bar.style.width = "0%";
    void bar.offsetWidth;
    bar.style.transition = `width ${seconds}s linear`;
    bar.style.width = "100%";
    return container;
}


async function notifyPairing(durationSec) {
    if (!("Notification" in window)) return () => {};
    let permission = Notification.permission;
    if (permission === "default") permission = await Notification.requestPermission();
    if (permission !== "granted") return () => {};
    const notification = new Notification("Pair LG TV", {
        body: "Accept the pairing prompt on the TV.",
        tag: "orc-pair-lg-tv",
    });
    notification.onclick = () => notification.close();
    const timer = setTimeout(() => notification.close(), durationSec * 1000);
    return () => { clearTimeout(timer); notification.close(); };
}

async function get(url, el, onFailure = () => {}) {
    if (new Date().getHours() < 9) {
        const what = el.dataset.id || el.dataset.deviceToggle || el.dataset.deviceInput || "this device";
        if (!confirm(`It's after hours.  Go ahead with: ${what}?`)) {
            onFailure();
            return false;
        }
    }
    el.disabled = true;
    const container = startProgress(parseFloat(el.dataset.duration || "0"));
    let response = null;

    try {
        response = await fetch(url, { headers: { "orc-version": version } });
        if (!response.ok) {
            throw Error(`Response status: ${response.status}`);
        }
        version = (await response.json()).version;
        return true;
    } catch (error) {
        console.error(error.message);
        if (isInvalidResponse(response)) {
            hardRefresh();
        }
        onFailure();
        return false;
    } finally {
        el.disabled = false;
        if (container) container.style.display = "none";
    }
}


async function checkVersion() {
    const spinner = document.getElementById("orc-version-spinner");
    spinner.style.display = "block";
    try {
        const response = await fetch("/api/version", {
            signal: AbortSignal.timeout(2000),
            redirect: "manual",
        });
        if (isInvalidResponse(response) || response.type === "opaqueredirect") {
            hardRefresh();
            return;
        }
        const { version: serverVersion } = await response.json();
        if (serverVersion !== version) location.reload();
    } catch {
        // unreachable server: the per-action version check catches stale pages later
    } finally {
        spinner.style.display = "none";
    }
}

async function runAction(el) {
    const params = new URLSearchParams();
    if (el.dataset.state) params.set("state", el.dataset.state);
    if (el.dataset.device) params.set("device", el.dataset.device);
    const query = params.size ? `?${params}` : "";
    await get(`/api/${el.dataset.type || "run"}/${el.dataset.id}${query}`, el);
}

document.addEventListener("click", (e) => {
    e.target.closest(".orc-log-action")?.classList.toggle("truncate");
});

document.getElementById("orc-navbar-toggle")?.addEventListener("click", (e) => {
    const menu = document.getElementById("admin-navbar-collapse");
    const open = menu.classList.toggle("hidden");
    e.currentTarget.setAttribute("aria-expanded", String(!open));
});

if (window.matchMedia("(display-mode: standalone)").matches) window.resizeTo(400, 670);

if (!performance.getEntriesByType("navigation")[0]?.transferSize) window.addEventListener("load", checkVersion);
window.addEventListener("pageshow", (e) => {
    if (e.persisted) checkVersion();
});
