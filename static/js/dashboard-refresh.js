(function () {
  const script = document.currentScript;
  const seconds = Number(script?.dataset.refreshSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return;
  }

  async function refreshInPlace() {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    try {
      const response = await fetch(window.location.href, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
      });
      if (!response.ok) {
        return;
      }
      const html = await response.text();
      const doc = new DOMParser().parseFromString(html, "text/html");
      const nextMain = doc.querySelector("main");
      const main = document.querySelector("main");
      if (!nextMain || !main) {
        return;
      }
      main.replaceWith(nextMain);
      document.title = doc.title;
      window.scrollTo(scrollX, scrollY);
    } catch {
      // Network blips should not interrupt reading the dashboard.
    }
  }

  window.setInterval(refreshInPlace, seconds * 1000);
})();
