(function () {
  "use strict";

  const navigationKeys = new Set([
    "ArrowDown",
    "ArrowRight",
    "ArrowUp",
    "ArrowLeft",
    "Home",
    "End",
  ]);

  document.addEventListener("keydown", function (event) {
    const current = event.target.closest(
      '.rs-browser-candidate-row[role="radio"]',
    );
    if (!current || !navigationKeys.has(event.key)) {
      return;
    }

    const group = current.closest('[role="radiogroup"]');
    if (!group) {
      return;
    }
    const radios = Array.from(
      group.querySelectorAll('.rs-browser-candidate-row[role="radio"]'),
    ).filter(function (radio) {
      return !radio.disabled;
    });
    const currentIndex = radios.indexOf(current);
    if (currentIndex < 0 || radios.length === 0) {
      return;
    }

    event.preventDefault();
    let nextIndex;
    if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = radios.length - 1;
    } else if (event.key === "ArrowDown" || event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % radios.length;
    } else {
      nextIndex = (currentIndex - 1 + radios.length) % radios.length;
    }

    const next = radios[nextIndex];
    next.focus();
    next.click();
  });
})();
