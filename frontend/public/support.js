// Copy buttons for the wallet addresses on support.html. External file so
// script-src 'self' (no inline scripts) holds site-wide.
document.querySelectorAll("button.copy").forEach(function (b) {
  b.addEventListener("click", function () {
    var text = document.getElementById(b.dataset.for).textContent;
    navigator.clipboard.writeText(text).then(function () {
      var old = b.textContent;
      b.textContent = "copied";
      setTimeout(function () { b.textContent = old; }, 1200);
    });
  });
});
