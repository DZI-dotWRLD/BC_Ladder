(() => {
    const navigation = document.querySelector(".nav-disclosure");
    if (navigation) {
        const desktopNavigation = window.matchMedia("(min-width: 64rem)");
        const syncNavigation = (event) => { navigation.open = event.matches; };
        syncNavigation(desktopNavigation);
        desktopNavigation.addEventListener("change", syncNavigation);
    }

    // Compose only server-rendered, same-origin fragments. Mutations remain normal Django POSTs.
    const requests = new Map();
    const loadDocument = (url) => {
        if (!requests.has(url)) {
            requests.set(url, (async () => {
                const controller = new AbortController();
                const timeout = window.setTimeout(() => controller.abort(), 15000);
                try {
                    const response = await fetch(url, {
                        credentials: "same-origin",
                        cache: "no-store",
                        signal: controller.signal,
                        headers: { "Accept": "text/html" },
                    });
                    if (!response.ok) throw new Error("Section unavailable");
                    return new DOMParser().parseFromString(await response.text(), "text/html");
                } finally { window.clearTimeout(timeout); }
            })());
        }
        return requests.get(url);
    };
    const sectionRoutes = new Map();
    const sections = [...document.querySelectorAll("[data-compose-url]")];
    const loadSection = async (slot) => {
        const url = new URL(slot.dataset.composeUrl, window.location.origin);
        const name = slot.dataset.composeFragment;
        if (url.origin !== window.location.origin || !/^[a-z-]+$/.test(name)) return;
        slot.setAttribute("aria-busy", "true");
        try {
            const source = await loadDocument(url.href);
            const fragment = source.querySelector('[data-fragment="' + name + '"]');
            if (!fragment) throw new Error("Section unavailable");
            const content = document.importNode(fragment, true);
            content.querySelectorAll("script").forEach((script) => script.remove());
            content.querySelectorAll("form").forEach((form) => {
                if (!form.hasAttribute("action")) form.setAttribute("action", url.pathname);
            });
            slot.replaceChildren(content);
            slot.classList.add("is-composed");
        } catch {
            const status = slot.querySelector("[data-compose-status]");
            if (status) status.textContent = "This section could not load. Use the link below to open it.";
        } finally { slot.removeAttribute("aria-busy"); }
    };

    Promise.all(sections.map(loadSection)).then(() => {
        for (const name of ["availability", "suggestions", "matches", "team"]) {
            const target = document.getElementById(name);
            if (target?.querySelector('[data-fragment="' + name + '"]')) {
                sectionRoutes.set("/" + name + "/", "#" + name);
            }
        }
        document.querySelectorAll(".journey-page a[href], .dashboard-page a[href]").forEach((link) => {
            const url = new URL(link.getAttribute("href"), window.location.href);
            if (url.origin === window.location.origin && !url.search && sectionRoutes.has(url.pathname)) {
                link.setAttribute("href", sectionRoutes.get(url.pathname));
            }
        });
        const initial = window.location.hash || sectionRoutes.get(window.location.pathname);
        if (initial && (window.location.hash || initial !== "#availability")) {
            // Native hash positioning can run after composition and move the target
            // beneath the sticky header. Position only after load and layout settle.
            const positionSection = () => window.requestAnimationFrame(() => {
                window.requestAnimationFrame(() => {
                    document.getElementById(initial.slice(1))?.scrollIntoView({ behavior: "instant", block: "start" });
                });
            });
            if (document.readyState === "complete") positionSection();
            else window.addEventListener("load", positionSection, { once: true });
        }
    });
})();
