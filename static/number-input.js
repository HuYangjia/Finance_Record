// Number fields keep native validation, but scrolling the page must never edit them.
document.addEventListener(
  "wheel",
  (event) => {
    const input = event.target;
    if (input?.matches?.('input[type="number"]') && document.activeElement === input) {
      input.blur();
    }
  },
  { capture: true, passive: true },
);
