// Copying instructions never submits a payment or records a payment as received.
(function () {
  "use strict";
  document.querySelectorAll("[data-payment-panel]").forEach(function (panel) {
    const status = panel.querySelector("[data-payment-status]");
    panel.querySelectorAll("[data-payment-copy]").forEach(function (button) {
      const value = panel.querySelector("#" + button.dataset.paymentCopy);
      if (!value || !status) return;
      button.hidden = false;
      button.addEventListener("click", async function () {
        try {
          await navigator.clipboard.writeText(value.textContent.trim());
          status.textContent = button.dataset.copyLabel + " copied.";
        } catch (_) {
          const selection = window.getSelection();
          if (selection) {
            const range = document.createRange();
            range.selectNodeContents(value);
            selection.removeAllRanges();
            selection.addRange(range);
          }
          status.textContent = "Automatic copying is unavailable. Select and copy the " +
            button.dataset.copyLabel.toLowerCase() + " shown above.";
        }
      });
    });
  });
}());
