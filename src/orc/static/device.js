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
            get(`/api/device/ac/${id}?state=${el.dataset.state}`, el);
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
        const { fan, mode } = acSelections[id] || {};
        get(`/api/device/ac/${id}?state=on&mode=${mode}&fan=${fan}&temp=${temp}`, el);
        ['power', 'fan', 'mode'].forEach(g => acQuery(g, id).forEach(btn => btn.classList.remove('orc-selected')));
    });
});

document.querySelectorAll(".orc-ac-power[data-state='off']").forEach(el => setAcOff(el.dataset.id));

document.querySelectorAll(".orc-runner").forEach((el) => {
    el.addEventListener("click", (e) => runAction(e.currentTarget));
});

document.querySelectorAll('.orc-device-slider').forEach(slider => {
    slider.addEventListener('change', () => get(`/api/device/${slider.dataset.id}?state=${slider.value}`, slider));
});

document.querySelectorAll('input.orc-ac-ctrl').forEach(slider => {
    const display = document.querySelector(`.orc-ac-temp[data-id="${slider.dataset.id}"]`);
    if (display) slider.addEventListener('input', () => display.textContent = slider.value);
});

document.querySelectorAll('.orc-device-toggle').forEach(btn => {
    btn.addEventListener('click', async () => {
        const on = btn.dataset.on === '1';
        if (!await get(`/api/device/${btn.dataset.id}?state=${on ? 'off' : 'on'}`, btn)) return;
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
