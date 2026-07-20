(function () {
  "use strict";

  const RESULT_ID = "__spiritkin_pdd_result__";
  const REQUEST_EVENT = "spiritkin-pdd-extract-request";
  const RESULT_EVENT = "spiritkin-pdd-extract-result";

  function publish(payload) {
    let target = document.getElementById(RESULT_ID);
    if (!target) {
      target = document.createElement("script");
      target.id = RESULT_ID;
      target.type = "application/json";
      (document.documentElement || document.head).appendChild(target);
    }
    target.textContent = JSON.stringify(payload);
    window.dispatchEvent(new CustomEvent(RESULT_EVENT));
  }

  function extract() {
    try {
      const product = window.SpiritKinPddNormalizer.normalizeRawData(window.rawData, window.location.href);
      publish({ ok: true, product });
    } catch (error) {
      publish({
        ok: false,
        error: String(error && error.message ? error.message : error),
        errorCode: String(error && error.code ? error.code : "raw_data_error")
      });
    }
  }

  window.addEventListener(REQUEST_EVENT, extract);
})();
