javascript:(function () {
  document.location.hash = "advanced/param";
  setTimeout(function () {
    let filter = document.getElementById("filterInput");
    filter.value = "AccessLeve";
    filter.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "l",
        bubbles: true,
        cancelable: true,
        keyCode: 108,
      })
    );
  }, 500);
})();