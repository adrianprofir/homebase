(function () {
  const CONSENT_COOKIE = "ap_analytics_consent";
  const CONSENT_MAX_AGE = 15552000;

  const configEl = document.getElementById("analytics-config");
  if (!configEl) {
    return;
  }

  let config;
  try {
    config = JSON.parse(configEl.textContent);
  } catch {
    return;
  }

  const banner = document.getElementById("analytics-consent-banner");
  const preferences = document.getElementById("analytics-preferences");
  let posthogLoaded = false;
  let capturingActive = false;

  function readConsent() {
    const match = document.cookie.match(
      new RegExp("(?:^|; )" + CONSENT_COOKIE + "=([^;]*)")
    );
    if (!match) {
      return null;
    }
    const value = decodeURIComponent(match[1]);
    if (value === "granted" || value === "denied") {
      return value;
    }
    return null;
  }

  function setConsent(value) {
    let cookie =
      CONSENT_COOKIE +
      "=" +
      encodeURIComponent(value) +
      "; Path=/; Max-Age=" +
      CONSENT_MAX_AGE +
      "; SameSite=Lax";
    if (window.location.protocol === "https:") {
      cookie += "; Secure";
    }
    if (config.cookieDomain) {
      cookie += "; Domain=" + config.cookieDomain;
    }
    document.cookie = cookie;
  }

  function usesFixedBanner() {
    return window.matchMedia("(min-width: 641px)").matches;
  }

  function clearBannerPadding() {
    document.documentElement.classList.remove("analytics-banner-open");
    document.documentElement.style.removeProperty("--analytics-banner-pad");
  }

  function setBannerOpen(open) {
    if (!banner) {
      return;
    }
    if (open) {
      banner.hidden = false;
      if (usesFixedBanner()) {
        document.documentElement.classList.add("analytics-banner-open");
        requestAnimationFrame(syncBannerPadding);
      } else {
        clearBannerPadding();
      }
    } else {
      banner.hidden = true;
      clearBannerPadding();
    }
  }

  function syncBannerPadding() {
    if (!banner || banner.hidden || !usesFixedBanner()) {
      if (!usesFixedBanner()) {
        clearBannerPadding();
      }
      return;
    }
    const height = banner.getBoundingClientRect().height;
    document.documentElement.style.setProperty(
      "--analytics-banner-pad",
      Math.ceil(height) + "px"
    );
  }

  function reconcileOptIn(ph) {
    if (!ph || typeof ph.opt_in_capturing !== "function") {
      return;
    }
    ph.opt_in_capturing();
    capturingActive = true;
  }

  function loadPosthog(onReady) {
    if (window.posthog && window.posthog.__loaded) {
      if (readConsent() === "granted") {
        window.posthog.register({ app: config.app });
        reconcileOptIn(window.posthog);
      }
      if (onReady) {
        onReady(window.posthog);
      }
      return;
    }
    if (posthogLoaded) {
      return;
    }
    posthogLoaded = true;

    /* PostHog snippet loader (eu cloud assets host derived from api_host). */
    (function (t, e) {
      var o, n, p, r;
      e.__SV ||
        ((window.posthog = e),
        (e._i = []),
        (e.init = function (i, s, a) {
          function g(t, e) {
            var o = e.split(".");
            2 == o.length && ((t = t[o[0]]), (e = o[1]));
            t[e] = function () {
              t.push([e].concat(Array.prototype.slice.call(arguments, 0)));
            };
          }
          ((p = t.createElement("script")).type = "text/javascript"),
            (p.crossOrigin = "anonymous"),
            (p.async = !0),
            (p.src =
              s.api_host.replace(".i.posthog.com", "-assets.i.posthog.com") +
              "/static/array.js"),
            (r = t.getElementsByTagName("script")[0]).parentNode.insertBefore(
              p,
              r
            );
          var u = e;
          for (
            void 0 !== a ? (u = e[a] = []) : (a = "posthog"),
              u.people = u.people || [],
              u.toString = function (t) {
                var e = "posthog";
                return "posthog" !== a && (e += "." + a), e;
              },
              u.people.toString = function () {
                return u.toString(1) + ".people (stub)";
              },
              o =
                "init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagPayload isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_out_capturing has_opted_out_capturing opt_in_capturing clear_opt_in_out_capturing debug".split(
                  " "
                ),
              n = 0;
            n < o.length;
            n++
          ) {
            g(u, o[n]);
          }
          e._i.push([i, s, a]);
        }),
        (e.__SV = 1));
    })(document, window.posthog || []);

    window.posthog.init(config.projectKey, {
      api_host: config.apiHost,
      ui_host: config.uiHost,
      person_profiles: "always",
      cross_subdomain_cookie: true,
      persistence: "localStorage+cookie",
      session_recording: {
        maskAllInputs: true,
      },
      loaded: function (ph) {
        ph.__loaded = true;
        ph.register({ app: config.app });
        if (readConsent() === "granted") {
          reconcileOptIn(ph);
        }
        if (onReady) {
          onReady(ph);
        }
      },
    });
  }

  function startCapturing() {
    loadPosthog();
  }

  function stopCapturing() {
    if (window.posthog && capturingActive) {
      window.posthog.opt_out_capturing();
      capturingActive = false;
    }
  }

  function onChoice(value) {
    const previous = readConsent();
    setConsent(value);
    setBannerOpen(false);
    if (value === "granted") {
      startCapturing();
      return;
    }
    if (previous === "granted") {
      stopCapturing();
    }
  }

  if (banner) {
    banner.addEventListener("click", function (event) {
      const button = event.target.closest("[data-analytics-consent]");
      if (!button) {
        return;
      }
      onChoice(button.getAttribute("data-analytics-consent"));
    });
    window.addEventListener("resize", syncBannerPadding);
  }

  if (preferences) {
    preferences.addEventListener("click", function () {
      setBannerOpen(true);
    });
  }

  const consent = readConsent();
  if (consent === "granted") {
    startCapturing();
  } else if (consent === "denied") {
    setBannerOpen(false);
  } else {
    setBannerOpen(true);
  }
})();
