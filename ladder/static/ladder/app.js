// Progressive enhancement only: every page works without this file.
(() => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Scroll reveal: cards and lists rise into place as they enter the viewport.
    const revealables = document.querySelectorAll(".reveal, .stagger");
    const countUp = (root) => root.querySelectorAll("[data-count]").forEach((node) => {
        const target = Number(node.textContent);
        if (reduceMotion || !Number.isFinite(target) || target === 0 || node.dataset.counted) return;
        node.dataset.counted = "true";
        const started = performance.now();
        const step = (now) => {
            const progress = Math.min(1, (now - started) / 900);
            node.textContent = String(Math.round(target * (1 - Math.pow(1 - progress, 3))));
            if (progress < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    });
    if (!("IntersectionObserver" in window) || reduceMotion) {
        revealables.forEach((node) => node.classList.add("is-visible"));
    } else {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                entry.target.classList.add("is-visible");
                countUp(entry.target);
                observer.unobserve(entry.target);
            });
        }, { rootMargin: "0px 0px -2% 0px", threshold: 0 });
        revealables.forEach((node) => observer.observe(node));
    }

    // Click ripple from the pointer position on buttons, presets and day chips.
    document.addEventListener("pointerdown", (event) => {
        if (reduceMotion) return;
        const target = event.target.closest(".btn, .preset, .day-chip");
        if (!target || target.getAttribute("aria-busy") === "true") return;
        const rect = target.getBoundingClientRect();
        const ripple = document.createElement("span");
        ripple.className = "ripple";
        ripple.setAttribute("aria-hidden", "true");
        ripple.style.left = `${event.clientX - rect.left}px`;
        ripple.style.top = `${event.clientY - rect.top}px`;
        target.append(ripple);
        ripple.addEventListener("animationend", () => ripple.remove(), { once: true });
    });

    // Popover-style disclosures (account menu, help tips) close on outside click and Escape.
    const popovers = () => document.querySelectorAll("details.account-menu[open], details.help[open]");
    document.addEventListener("click", (event) => {
        popovers().forEach((details) => { if (!details.contains(event.target)) details.open = false; });
    });
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        popovers().forEach((details) => {
            details.open = false;
            details.querySelector("summary")?.focus();
        });
    });
    // Opening one popover closes the others.
    document.querySelectorAll("details.account-menu, details.help").forEach((details) => {
        details.addEventListener("toggle", () => {
            if (details.open) popovers().forEach((other) => { if (other !== details) other.open = false; });
        });
    });

    // Toasts: dismiss button, success messages leave on their own.
    const dismiss = (toast) => {
        if (reduceMotion) { toast.remove(); return; }
        toast.classList.add("is-leaving");
        toast.addEventListener("animationend", () => toast.remove(), { once: true });
    };
    document.querySelectorAll(".toast").forEach((toast) => {
        const close = toast.querySelector(".toast-close");
        if (close) {
            close.hidden = false;
            close.addEventListener("click", () => dismiss(toast));
        }
        if (toast.hasAttribute("data-autodismiss")) window.setTimeout(() => dismiss(toast), 5000);
    });

    // Submit feedback: show progress and ignore repeated clicks while the request is in flight.
    document.querySelectorAll("form[method='post']").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (form.dataset.submitting) { event.preventDefault(); return; }
            form.dataset.submitting = "true";
            const button = event.submitter || form.querySelector("button[type='submit']");
            if (button) button.setAttribute("aria-busy", "true");
        });
    });
    // Restore forms when the page is shown again from the back/forward cache.
    window.addEventListener("pageshow", (event) => {
        if (!event.persisted) return;
        document.querySelectorAll("form[data-submitting]").forEach((form) => {
            delete form.dataset.submitting;
            form.querySelectorAll("[aria-busy]").forEach((button) => button.removeAttribute("aria-busy"));
        });
    });

    // Header gains a shadow once content scrolls beneath it.
    const header = document.querySelector(".site-header");
    if (header) {
        const syncHeader = () => header.classList.toggle("is-scrolled", window.scrollY > 4);
        syncHeader();
        window.addEventListener("scroll", syncHeader, { passive: true });
    }

    // Availability: replace the two native datetime fields with a day + time picker that writes back into them.
    const availability = document.querySelector("[data-availability-form]");
    if (availability) buildAvailabilityPicker(availability);

    function buildAvailabilityPicker(form) {
        const start = form.querySelector("[name='starts_at']");
        const end = form.querySelector("[name='ends_at']");
        const native = form.querySelector("[data-native-fields]");
        if (!start || !end || !native) return;

        const pad = (value) => String(value).padStart(2, "0");
        const isoDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
        const minutesLabel = (minutes) => {
            const hours = Math.floor(minutes / 60), mins = minutes % 60;
            return `${((hours + 11) % 12) + 1}:${pad(mins)} ${hours < 12 ? "AM" : "PM"}`;
        };
        const toMinutes = (value) => { const [h, m] = value.split(":").map(Number); return h * 60 + m; };
        const toTime = (minutes) => `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
        const el = (tag, attrs = {}, text) => {
            const node = document.createElement(tag);
            Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
            if (text !== undefined) node.textContent = text;
            return node;
        };
        // Picker controls must not be submitted with the form; they only write into the native fields.
        const detach = (node) => { node.setAttribute("form", "availability-picker-controls"); return node; };

        const FIRST = 6 * 60, LAST = 22 * 60, STEP = 30;
        const presets = [["Morning", 8 * 60, 10 * 60], ["Midday", 12 * 60, 14 * 60], ["Afternoon", 15 * 60, 17 * 60], ["Evening", 18 * 60, 20 * 60]];
        const state = { day: null, from: 18 * 60, to: 20 * 60 };
        if (start.value) {
            const [day, time] = start.value.split("T");
            state.day = day; state.from = toMinutes(time);
            if (end.value && end.value.startsWith(day)) state.to = toMinutes(end.value.split("T")[1]);
        }
        const snap = (minutes, low, high) => Math.max(low, Math.min(high, Math.round(minutes / STEP) * STEP));
        state.from = snap(state.from, FIRST, LAST - STEP);
        state.to = snap(state.to, state.from + STEP, LAST);

        const picker = el("div", { class: "picker" });

        // Step 1: day chips for the next two weeks.
        const dayGroup = el("fieldset", { class: "picker-step" });
        dayGroup.append(el("legend", {}, "Day"));
        const dayRow = el("div", { class: "day-row", role: "radiogroup", "aria-label": "Day" });
        const today = new Date();
        for (let offset = 0; offset < 14; offset++) {
            const date = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
            const value = isoDay(date);
            const label = el("label", { class: "day-chip" });
            const radio = detach(el("input", { type: "radio", name: "picker-day", value, class: "visually-hidden" }));
            if (state.day === value || (!state.day && offset === 0)) { radio.checked = true; state.day = value; }
            radio.addEventListener("change", () => { state.day = value; sync(); });
            label.append(radio,
                el("span", { class: "day-chip-dow" }, offset === 0 ? "Today" : date.toLocaleDateString(undefined, { weekday: "short" })),
                el("strong", {}, String(date.getDate())),
                el("span", { class: "day-chip-month" }, date.toLocaleDateString(undefined, { month: "short" })));
            dayRow.append(label);
        }
        dayGroup.append(dayRow);

        // Step 2: one-tap presets plus exact from/to selects.
        const timeGroup = el("fieldset", { class: "picker-step" });
        timeGroup.append(el("legend", {}, "Time"));
        const presetRow = el("div", { class: "preset-row" });
        const presetButtons = presets.map(([name, from, to]) => {
            const button = el("button", { type: "button", class: "preset", "aria-pressed": "false" });
            button.append(el("strong", {}, name), el("span", {}, `${minutesLabel(from)} – ${minutesLabel(to)}`));
            button.addEventListener("click", () => { state.from = from; state.to = to; sync(); });
            presetRow.append(button);
            return [button, from, to];
        });
        const range = el("div", { class: "time-range" });
        const makeSelect = (id, labelText) => {
            const wrap = el("div", { class: "field" });
            const select = detach(el("select", { id }));
            wrap.append(el("label", { for: id }, labelText), select);
            return [wrap, select];
        };
        const [fromWrap, fromSelect] = makeSelect("picker-from", "From");
        const [toWrap, toSelect] = makeSelect("picker-to", "To");
        for (let minutes = FIRST; minutes < LAST; minutes += STEP) fromSelect.append(el("option", { value: minutes }, minutesLabel(minutes)));
        fromSelect.addEventListener("change", () => {
            const length = state.to - state.from;
            state.from = Number(fromSelect.value);
            state.to = Math.min(LAST, state.from + (length > 0 ? length : 120));
            sync();
        });
        toSelect.addEventListener("change", () => { state.to = Number(toSelect.value); sync(); });
        range.append(fromWrap, el("span", { class: "time-dash", "aria-hidden": "true" }, "–"), toWrap);
        timeGroup.append(presetRow, range);

        const summary = el("p", { class: "picker-summary", "aria-live": "polite" });
        picker.append(dayGroup, timeGroup, summary);

        function sync() {
            fromSelect.value = String(state.from);
            toSelect.replaceChildren();
            for (let minutes = state.from + STEP; minutes <= LAST; minutes += STEP) toSelect.append(el("option", { value: minutes }, minutesLabel(minutes)));
            if (state.to <= state.from) state.to = Math.min(LAST, state.from + 120);
            toSelect.value = String(state.to);
            presetButtons.forEach(([button, from, to]) => button.setAttribute("aria-pressed", String(from === state.from && to === state.to)));
            start.value = `${state.day}T${toTime(state.from)}`;
            end.value = `${state.day}T${toTime(state.to)}`;
            const day = new Date(`${state.day}T00:00`).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
            summary.textContent = `${day} · ${minutesLabel(state.from)} – ${minutesLabel(state.to)}`;
        }

        native.hidden = true;
        native.after(picker);
        form.classList.add("is-enhanced");
        sync();
    }

    // Scorecard: jump between single-digit set fields and highlight the tie-break only when sets are split.
    const scoreForm = document.querySelector("[data-score-form]");
    if (scoreForm) {
        const order = ["set1_team_a", "set1_team_b", "set2_team_a", "set2_team_b", "set3_team_a", "set3_team_b"];
        const fields = order.map((name) => scoreForm.querySelector(`[name='${name}']`));
        const hint = scoreForm.querySelector("[data-tiebreak-hint]");
        const value = (index) => (fields[index]?.value === "" ? null : Number(fields[index].value));
        const update = () => {
            const [a1, b1, a2, b2] = [0, 1, 2, 3].map(value);
            if ([a1, b1, a2, b2].some((item) => item === null)) { scoreForm.dataset.tiebreak = "off"; return; }
            const split = (a1 > b1) !== (a2 > b2);
            scoreForm.dataset.tiebreak = split ? "on" : "off";
            if (hint) hint.textContent = split ? "Sets are split — enter the deciding match tie-break." : "Straight sets — no tie-break needed.";
        };
        fields.forEach((field, index) => {
            field?.addEventListener("input", () => {
                update();
                if (index < 3 && field.value.length === 1 && fields[index + 1]) fields[index + 1].focus();
                if (index === 3 && field.value.length === 1 && scoreForm.dataset.tiebreak === "on") fields[4]?.focus();
            });
        });
        update();
    }
})();
