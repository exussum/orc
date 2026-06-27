async function run(el) {
    if (new Date().getHours() < 9 && !confirm(`It's after hours.  Go ahead with: ${el.dataset.id}?`)) return;
    await get(`/api/${el.dataset.type}/${el.dataset.id}?state=${el.dataset.state}`, el);
}

document.querySelectorAll(".orc-runner").forEach((el) => {
    el.addEventListener("click", (e) => run(e.currentTarget));
});

document.querySelector(".orc-pause")?.addEventListener("click", (e) => {
    get(`/api/schedule/${e.currentTarget.getAttribute("data-id")}/pause`, e.currentTarget).then((ok) => { if (ok) location.reload(); });
});

const highlight_configs = window.orcHighlightConfigs.map(([name, start, end]) => [
    name,
    new Date("01/01/00 " + start),
    new Date("01/01/00 " + end),
]);

function highlight() {
    const now = new Date();
    now.setFullYear(2000, 0, 1);
    highlight_configs.forEach(([id, start, finish]) => {
        const element = document.querySelector(`.orc-runner[data-id='${id}'] .orc-ribbon`);
        element.style.display = start <= now && now <= finish ? "block" : "none";
    });
}

highlight();
setInterval(highlight, 60000);
