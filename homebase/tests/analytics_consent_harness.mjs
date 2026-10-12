import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const analyticsSource = readFileSync(
  join(here, "../../static/js/analytics.js"),
  "utf8"
);

function createPage(initialCookie) {
  const cookieJar = [];
  const listeners = { click: null };

  if (initialCookie) {
    cookieJar.push(`ap_analytics_consent=${initialCookie}`);
  }

  const config = {
    projectKey: "phc_test",
    apiHost: "https://eu.i.posthog.com",
    uiHost: "https://eu.posthog.com",
    app: "homebase",
    cookieDomain: "",
  };

  const banner = {
    id: "analytics-consent-banner",
    hidden: true,
    addEventListener(type, fn) {
      listeners[type] = fn;
    },
    getBoundingClientRect() {
      return { height: 72 };
    },
  };

  const document = {
    getElementById(id) {
      if (id === "analytics-config") {
        return { id, textContent: JSON.stringify(config) };
      }
      if (id === "analytics-consent-banner") {
        return banner;
      }
      if (id === "analytics-preferences") {
        return { id, addEventListener() {} };
      }
      return null;
    },
    createElement() {
      return { type: "", crossOrigin: "", async: false, src: "" };
    },
    getElementsByTagName() {
      return [{ parentNode: { insertBefore() {} } }];
    },
    addEventListener() {},
  };

  Object.defineProperty(document, "cookie", {
    get() {
      return cookieJar.join("; ");
    },
    set(raw) {
      const pair = raw.split(";")[0];
      const name = pair.split("=")[0];
      if (raw.includes("Max-Age=0")) {
        const idx = cookieJar.findIndex((c) => c.startsWith(name + "="));
        if (idx >= 0) {
          cookieJar.splice(idx, 1);
        }
        return;
      }
      const idx = cookieJar.findIndex((c) => c.startsWith(name + "="));
      if (idx >= 0) {
        cookieJar[idx] = pair;
      } else {
        cookieJar.push(pair);
      }
    },
  });

  const documentElement = {
    classList: {
      add() {},
      remove() {},
    },
    style: { setProperty() {}, removeProperty() {} },
  };

  let optedOut = true;
  const sdk = {
    __loaded: false,
    register() {},
    opt_in_capturing() {
      optedOut = false;
    },
    opt_out_capturing() {
      optedOut = true;
    },
    has_opted_out_capturing() {
      return optedOut;
    },
  };

  const window = {
    location: { protocol: "http:" },
    matchMedia() {
      return { matches: true };
    },
    posthog: null,
    addEventListener() {},
    requestAnimationFrame(fn) {
      fn();
    },
  };

  function runScript() {
    const context = {
      window,
      document,
      console,
      setTimeout,
      clearTimeout,
      requestAnimationFrame: window.requestAnimationFrame,
    };
    context.window.document = document;
    document.documentElement = documentElement;
    vm.createContext(context);
    vm.runInContext(analyticsSource, context);
  }

  function clickConsent(value) {
    if (!listeners.click) {
      throw new Error("banner click listener missing");
    }
    listeners.click({
      target: {
        closest(sel) {
          if (sel !== "[data-analytics-consent]") {
            return null;
          }
          return { getAttribute: () => value };
        },
      },
    });
  }

  function finishSdkLoad() {
    const queue = window.posthog && window.posthog._i;
    if (!queue || !queue.length) {
      throw new Error("SDK init queue empty");
    }
    const options = queue[queue.length - 1][1];
    options.loaded(sdk);
    sdk.__loaded = true;
    window.posthog = sdk;
  }

  function readConsent() {
    const match = document.cookie.match(/ap_analytics_consent=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  return {
    runScript,
    clickConsent,
    finishSdkLoad,
    readConsent,
    isOptedOut() {
      return sdk.has_opted_out_capturing();
    },
  };
}

function testDeclineWhileLoading() {
  const page = createPage();
  page.runScript();
  page.clickConsent("granted");
  page.clickConsent("denied");
  page.finishSdkLoad();
  if (page.readConsent() !== "denied" || !page.isOptedOut()) {
    throw new Error(
      `declined while loading: cookie=${page.readConsent()} optedOut=${page.isOptedOut()}`
    );
  }
}

function testReacceptInPlace() {
  const page = createPage();
  page.runScript();
  page.clickConsent("granted");
  page.finishSdkLoad();
  page.clickConsent("denied");
  page.clickConsent("granted");
  if (page.isOptedOut()) {
    throw new Error("reaccept: still opted out");
  }
}

function testReacceptAfterReload() {
  const page = createPage("granted");
  page.runScript();
  page.finishSdkLoad();
  page.clickConsent("denied");
  const reload = createPage("granted");
  reload.runScript();
  reload.finishSdkLoad();
  if (reload.isOptedOut()) {
    throw new Error("reaccept after reload: still opted out");
  }
}

testDeclineWhileLoading();
testReacceptInPlace();
testReacceptAfterReload();

console.log("analytics consent harness: ok");
