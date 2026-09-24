import { get, wire } from "./orc.js";

wire(".orc-pause", "click", async (el) => {
    if (await get(`/api/schedule/${el.dataset.id}/pause`, el)) location.reload();
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
        if (element) element.style.display = start <= now && now <= finish ? "block" : "none";
    });
}

highlight();
setInterval(highlight, 60000);
