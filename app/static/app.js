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

// Forms marked data-disable-on-submit lock their buttons once submitted, so a slow request
// (rewriting notes can take minutes) is not sent twice.
document.addEventListener("submit", function (event) {
  var form = event.target.closest("form[data-disable-on-submit]");
  if (!form) return;
  setTimeout(function () {
    form.querySelectorAll("button").forEach(function (button) { button.disabled = true; });
  }, 0);
});
window.addEventListener("pageshow", function (event) {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-disable-on-submit] button").forEach(function (button) {
    button.disabled = false;
  });
});
