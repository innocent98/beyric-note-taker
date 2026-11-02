document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  var source = document.getElementById(button.dataset.copy);
  var text = source.value !== undefined ? source.value : source.textContent;
  function done(ok) {
    button.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(function () { button.textContent = button.dataset.label; }, 2000);
  }
  function fallback() {
    var box = document.createElement("textarea");
    box.value = text;
    document.body.appendChild(box);
    box.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    box.remove();
    done(ok);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
  } else {
    fallback();
  }
});
