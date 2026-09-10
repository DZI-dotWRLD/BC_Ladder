(() => {
    const navigation = document.querySelector(".nav-disclosure");
    if (!navigation) {
        return;
    }

    const desktopNavigation = window.matchMedia("(min-width: 64rem)");
    const syncNavigation = (event) => {
        navigation.open = event.matches;
    };

    syncNavigation(desktopNavigation);
    desktopNavigation.addEventListener("change", syncNavigation);
})();
