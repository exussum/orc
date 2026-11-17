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

const lastRun = document.getElementById("orc-last-run");
lastRun.classList.toggle("orc-fade-end", lastRun.scrollWidth > lastRun.clientWidth);
