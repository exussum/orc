async function send(url, el) {
    el.disabled = true;
    try { await fetch(url); }
    finally { el.disabled = false; }
}

const acSelections = {};

function acQuery(group, id) {
    return document.querySelectorAll(`[data-ac-${group}="${id}"]`);
}

function selectOne(group, id, el) {
    acQuery(group, id).forEach(btn => btn.classList.remove('orc-selected'));
    el.classList.add('orc-selected');
}

function resetGroup(group, id, disabled) {
    acQuery(group, id).forEach(btn => {
        btn.disabled = disabled;
        btn.classList.remove('orc-selected');
    });
}

function setAcOff(id) {
    resetGroup('mode', id, true);
    resetGroup('fan', id, true);
    acQuery('set', id).forEach(btn => { btn.disabled = true; });
    const temp = document.querySelector(`input[data-ac-ctrl="${id}"]`);
    if (temp) { temp.disabled = true; temp.classList.remove('orc-selected'); }
}

function startAcWizard(id) {
    acSelections[id] = {};
    resetGroup('mode', id, false);
    resetGroup('fan', id, true);
    acQuery('set', id).forEach(btn => btn.disabled = true);
    const temp = document.querySelector(`input[data-ac-ctrl="${id}"]`);
    if (temp) temp.disabled = false;
}

const acGroups = {
    power: {
        idKey: 'acPower',
        onSelect(id, el) {
            if (el.dataset.state === 'off') setAcOff(id);
            else startAcWizard(id);
            send(`/api/device/ac/${id}?state=${el.dataset.state}`, el);
        },
    },
    mode: {
        idKey: 'acMode',
        onSelect(id, el) {
            const mode = el.dataset.value;
            acSelections[id] = { ...acSelections[id], mode, fan: undefined };
            if (mode === 'dry') {
                resetGroup('fan', id, true);
                acQuery('set', id).forEach(btn => btn.disabled = false);
            } else {
                resetGroup('fan', id, false);
                acQuery('set', id).forEach(btn => btn.disabled = true);
            }
        },
    },
    fan: {
        idKey: 'acFan',
        onSelect(id, el) {
            acSelections[id] = { ...acSelections[id], fan: el.dataset.value };
            acQuery('set', id).forEach(btn => btn.disabled = false);
        },
    },
};

Object.entries(acGroups).forEach(([group, { idKey, onSelect }]) => {
    document.querySelectorAll(`[data-ac-${group}]`).forEach(el => {
        el.addEventListener('pointerdown', () => selectOne(group, el.dataset[idKey], el));
        el.addEventListener('click', () => onSelect(el.dataset[idKey], el));
    });
});

document.querySelectorAll('[data-ac-set]').forEach(el => {
    el.addEventListener('click', () => {
        const id = el.dataset.acSet;
        const temp = document.querySelector(`input[data-ac-ctrl="${id}"]`)?.value;
        const { fan, mode } = acSelections[id] || {};
        send(`/api/device/ac/${id}?state=on&mode=${mode}&fan=${fan}&temp=${temp}`, el);
        ['power', 'fan', 'mode'].forEach(g => acQuery(g, id).forEach(btn => btn.classList.remove('orc-selected')));
    });
});

document.querySelectorAll("[data-ac-power][data-state='off']").forEach(el => setAcOff(el.dataset.acPower));

function selectRunner(btn) {
    if (!btn.classList.contains("orc-toggle")) return;
    btn.parentElement.querySelectorAll(".orc-toggle").forEach((sib) => sib.classList.remove("orc-selected"));
    btn.classList.add("orc-selected");
}

document.querySelectorAll(".orc-runner").forEach((el) => {
    el.addEventListener("click", (e) => {
        selectRunner(e.currentTarget);
        run(e.currentTarget);
    });
});

document.querySelectorAll('[data-device-input]').forEach(slider => {
    slider.addEventListener('change', () => send(`/api/device/${slider.dataset.deviceInput}?state=${slider.value}`, slider));
});

document.querySelectorAll('input[data-ac-ctrl]').forEach(slider => {
    const display = document.querySelector(`[data-ac-temp="${slider.dataset.acCtrl}"]`);
    if (display) slider.addEventListener('input', () => display.textContent = slider.value);
});

document.querySelectorAll('[data-device-toggle]').forEach(btn => {
    btn.addEventListener('click', async () => {
        const on = btn.dataset.on === '1';
        await send(`/api/device/${btn.dataset.deviceToggle}?state=${on ? 'off' : 'on'}`, btn);
        btn.dataset.on = on ? '' : '1';
        btn.querySelector('use').setAttribute('href', `/static/icons.svg#${btn.dataset.icon}${on ? '-outline' : ''}`);
    });
});

const sliderTip = document.createElement('div');
sliderTip.className = 'fixed z-50 -translate-x-1/2 -translate-y-full px-1.5 py-0.5 text-xs rounded bg-black/80 text-white pointer-events-none hidden';
document.body.appendChild(sliderTip);

function showSliderTip(slider) {
    const rect = slider.getBoundingClientRect();
    const min = +slider.min || 0, max = +slider.max || 100;
    const pct = (slider.value - min) / (max - min);
    const thumb = 16;
    sliderTip.textContent = slider.value;
    sliderTip.style.left = `${rect.left + thumb / 2 + pct * (rect.width - thumb)}px`;
    sliderTip.style.top = `${rect.top - 6}px`;
    sliderTip.classList.remove('hidden');
}

document.querySelectorAll('input[type="range"]').forEach(slider => {
    slider.addEventListener('input', () => showSliderTip(slider));
    ['change', 'pointerup', 'blur'].forEach(ev => slider.addEventListener(ev, () => sliderTip.classList.add('hidden')));
});
