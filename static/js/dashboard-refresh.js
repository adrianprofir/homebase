(function () {
  const script = document.currentScript;
  const seconds = Number(script?.dataset.refreshSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return;
  }

  let refreshing = false;

  async function refreshInPlace() {
    if (refreshing || document.hidden) {
      return;
    }
    refreshing = true;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const pageUrl = window.location.href;
    try {
      const response = await fetch(pageUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
        signal: AbortSignal.timeout(60_000),
      });
      if (response.redirected || response.url !== pageUrl) {
        window.location.reload();
        return;
      }
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
    } finally {
      refreshing = false;
    }
  }

  window.setInterval(refreshInPlace, seconds * 1000);
})();
