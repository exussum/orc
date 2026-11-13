import { get, wire } from "./orc.js";

const acSelections = {};

function acQuery(group, id) {
    return document.querySelectorAll(`.orc-ac-${group}[data-id="${id}"]`);
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
    const temp = document.querySelector(`input.orc-ac-ctrl[data-id="${id}"]`);
    if (temp) { temp.disabled = true; temp.classList.remove('orc-selected'); }
}

function startAcWizard(id) {
    acSelections[id] = {};
    resetGroup('mode', id, false);
    resetGroup('fan', id, true);
    acQuery('set', id).forEach(btn => btn.disabled = true);
    const temp = document.querySelector(`input.orc-ac-ctrl[data-id="${id}"]`);
    if (temp) temp.disabled = false;
}

const acGroups = {
    power: {
        onSelect(id, el) {
            if (el.dataset.state === 'off') setAcOff(id);
            else startAcWizard(id);
            get(`/api/device/${el.dataset.kind}/${id}?state=${el.dataset.state}`, el);
        },
    },
    mode: {
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
        onSelect(id, el) {
            acSelections[id] = { ...acSelections[id], fan: el.dataset.value };
            acQuery('set', id).forEach(btn => btn.disabled = false);
        },
    },
};

Object.entries(acGroups).forEach(([group, { onSelect }]) => {
    document.querySelectorAll(`.orc-ac-${group}`).forEach(el => {
        el.addEventListener('pointerdown', () => selectOne(group, el.dataset.id, el));
        el.addEventListener('click', () => onSelect(el.dataset.id, el));
    });
});

document.querySelectorAll('.orc-ac-set').forEach(el => {
    el.addEventListener('click', () => {
        const id = el.dataset.id;
        const temp = document.querySelector(`input.orc-ac-ctrl[data-id="${id}"]`)?.value;
        const { fan = "", mode } = acSelections[id] || {};
        get(`/api/device/${el.dataset.kind}/${id}?state=${mode}:${fan}:${temp}`, el);
        ['power', 'fan', 'mode'].forEach(g => acQuery(g, id).forEach(btn => btn.classList.remove('orc-selected')));
    });
});

document.querySelectorAll(".orc-ac-power[data-state='off']").forEach(el => setAcOff(el.dataset.id));

const lockedNames = new Set();

function lockGroup(el, selector) {
    if (!lockedNames.has(el.dataset.name)) return [el];
    return document.querySelectorAll(`${selector}[data-name="${CSS.escape(el.dataset.name)}"]`);
}

wire('.orc-device-slider', 'change', (slider) => {
    lockGroup(slider, '.orc-device-slider').forEach(member => {
        member.value = slider.value;
        get(`/api/device/${member.dataset.kind}/${member.dataset.id}?state=${slider.value}`, member);
    });
});

document.querySelectorAll('input.orc-ac-ctrl').forEach(slider => {
    const display = document.querySelector(`.orc-ac-temp[data-id="${slider.dataset.id}"]`);
    if (display) slider.addEventListener('input', () => display.textContent = slider.value);
});

function showToggle(btn, on) {
    btn.dataset.on = on ? '1' : '';
    btn.querySelector('use').setAttribute('href', `/static/icons.svg#${btn.dataset.icon}${on ? '' : '-outline'}`);
}

async function setToggle(btn, on) {
    if (await get(`/api/device/${btn.dataset.kind}/${btn.dataset.id}?state=${on ? 'on' : 'off'}`, btn)) showToggle(btn, on);
}

document.querySelectorAll('.orc-device-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
        const on = btn.dataset.on !== '1';
        lockGroup(btn, '.orc-device-toggle').forEach(member => setToggle(member, on));
    });
});

const colourDialog = document.getElementById('orc-colour-dialog');
const colourWheel = new iro.ColorPicker('#orc-colour-wheel', { width: 200, color: '#ffffff', layout: [{ component: iro.ui.Wheel }] });
let colourTarget = null;

function sendColour(state) {
    lockGroup(colourTarget, '.orc-colour-swatch').forEach(swatch => {
        get(`/api/device/${swatch.dataset.kind}/${swatch.dataset.id}?state=${state}`, swatch);
    });
}

document.querySelectorAll('.orc-colour-swatch').forEach(swatch => {
    const { hue, saturation } = swatch.dataset;
    if (hue && saturation) {
        swatch.style.background = new iro.Color({ h: hue * 3.6, s: saturation, v: 100 }).hexString;
    } else if (swatch.dataset.kelvin) {
        swatch.style.background = '#ffffff';
    }
    swatch.addEventListener('click', () => {
        colourTarget = swatch;
        document.getElementById('orc-colour-title').textContent = swatch.dataset.name;
        colourWheel.color.hsv = hue && saturation ? { h: hue * 3.6, s: saturation, v: 100 } : { h: 0, s: 0, v: 100 };
        document.getElementById('orc-colour-kelvin').value = swatch.dataset.kelvin || 4000;
        colourDialog.showModal();
    });
});

colourWheel.on('input:end', (color) => {
    lockGroup(colourTarget, '.orc-colour-swatch').forEach(swatch => { swatch.style.background = color.hexString; });
    sendColour(`hue:${Math.round(color.hue / 3.6)}:${Math.round(color.saturation)}`);
});

document.getElementById('orc-colour-kelvin').addEventListener('change', (e) => sendColour(`ct:${e.currentTarget.value}`));

document.querySelectorAll('.orc-lock').forEach(box => {
    box.addEventListener('change', () => {
        if (box.checked) lockedNames.add(box.dataset.name);
        else lockedNames.delete(box.dataset.name);
        document.querySelectorAll(`.orc-lock[data-name="${CSS.escape(box.dataset.name)}"]`).forEach(other => { other.checked = box.checked; });
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
