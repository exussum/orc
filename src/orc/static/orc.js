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


async function get(url, el, onFailure = () => {}, useVersion = true) {
    if (!(await orcHooks.onCommand(el?.dataset.id, el, url))) {
        onFailure();
        return false;
    }
    if (el) el.disabled = true;
    const container = el ? startProgress(parseFloat(el.dataset.duration || "0")) : null;
    let response = null;

    try {
        response = await fetch(url, { headers: { "orc-version": version } });
        if (!response.ok) {
            throw Error(`Response status: ${response.status}`);
        }
        const data = await response.json();
        if (useVersion) version = data.version;
        return data;
    } catch (error) {
        console.error(error.message);
        if (isInvalidResponse(response)) {
            hardRefresh();
        }
        onFailure();
        return false;
    } finally {
        if (el) el.disabled = false;
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
    if (!(await orcHooks.onPress(el.dataset.id, el))) return;
    if ("noFunc" in el.dataset) {
        alert(
            `"${el.dataset.id}" does nothing: its plugin has no server action, and no browser hook handled the press.\n\n` +
                `Give it a --function on its plugin line in config.orc, or register a hook for it — orcHooks.register({ onPress(name) { ... } }).`,
        );
        return;
    }
    const params = new URLSearchParams();
    if (el.dataset.state) params.set("state", el.dataset.state);
    if (el.dataset.device) params.set("device", el.dataset.device);
    const query = params.size ? `?${params}` : "";
    await get(`/api/${el.dataset.type || "run"}/${el.dataset.id}${query}`, el);
}

document.querySelectorAll(".orc-config-runner").forEach((el) => {
    el.addEventListener("click", (e) => runAction(e.currentTarget));
});

function revealOverflowCarets(root) {
    for (const action of root.querySelectorAll(".orc-log-action.truncate")) {
        if (action.scrollWidth > action.clientWidth) {
            action.parentElement.querySelector(".orc-log-caret")?.classList.remove("invisible");
        }
    }
}

document.addEventListener("click", (e) => {
    const caret = e.target.closest(".orc-log-caret");
    if (!caret) return;
    const row = caret.closest("tr");
    row.querySelector(".orc-log-action")?.classList.toggle("truncate");
    if (!row.classList.contains("orc-log-parent")) return;
    const open = row.classList.toggle("orc-log-open");
    const table = row.closest("table");
    table.querySelectorAll(`[data-log-parent="${row.dataset.logId}"]`).forEach((r) => r.classList.toggle("hidden", !open));
    if (open) revealOverflowCarets(table);
});

revealOverflowCarets(document);

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
