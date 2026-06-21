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

async function call(url, el, onFailure = () => {}) {
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
    const banner = document.getElementById("orc-version-error");
    spinner.style.display = "block";
    banner.style.display = "none";
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
        banner.style.display = "block";
    } finally {
        spinner.style.display = "none";
    }
}

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
