/*
 * The very first thing the page runs: before Telegram's SDK, before the app bundle.
 *
 * If a web view dies while it is still loading the app (WebKitGTK on some Linux
 * machines), nothing in the bundle ever runs, and so nothing ever reports. This file
 * is tiny, plain ES5 and same-origin (the CSP allows no inline script), and it does
 * three things:
 *   1. if the previous start left a note that it never got to "ready", report a crash
 *      with the stage it reached ("html" = it died before the app code ran);
 *   2. leave this start's note, at stage "html";
 *   3. report the boot.
 * The app (src/lib/diagnostics.ts) picks up the same session and carries the note on.
 *
 * Nothing sensitive leaves: Telegram's launch data sits in the URL fragment, so only
 * the platform and version are read from it, never the rest.
 */
(function () {
  try {
    var meta = document.querySelector('meta[name="tm-api"]');
    var base = meta && meta.content && meta.content.charAt(0) !== '%' ? meta.content.replace(/\/$/, '') : '';
    var url = base + '/v1/telemetry/client';
    var session = '';
    for (var i = 0; i < 8; i += 1) session += ('0' + Math.floor(Math.random() * 256).toString(16)).slice(-2);

    function param(name) {
      var match = new RegExp('[#&]' + name + '=([^&]*)').exec(location.hash);
      if (!match) return '';
      try {
        return decodeURIComponent(match[1]);
      } catch (e) {
        return '';
      }
    }
    var platform = param('tgWebAppPlatform').slice(0, 40);
    var version = param('tgWebAppVersion').slice(0, 20);

    function send(event) {
      event.session = event.session || session;
      event.platform = event.platform || platform;
      event.tg_version = event.tg_version || version;
      event.ua = navigator.userAgent.slice(0, 400);
      event.path = '';
      var body = JSON.stringify(event);
      try {
        if (navigator.sendBeacon && navigator.sendBeacon(url, new Blob([body], { type: 'text/plain' }))) return;
      } catch (e) {
        /* fall through to XHR */
      }
      try {
        var xhr = new XMLHttpRequest();
        xhr.open('POST', url, true);
        xhr.setRequestHeader('Content-Type', 'text/plain');
        xhr.send(body);
      } catch (e) {
        /* nothing else to try */
      }
    }

    var previous = null;
    try {
      previous = JSON.parse(localStorage.getItem('tmusic.lastBoot') || 'null');
    } catch (e) {
      previous = null;
    }
    if (
      previous &&
      previous.stage !== 'ready' &&
      previous.stage !== 'closed' &&
      Date.now() - previous.at < 86400000
    ) {
      send({
        kind: 'crash',
        stage: String(previous.stage).slice(0, 40),
        message: 'previous start (' + previous.session + ') stopped at "' + previous.stage + '"',
      });
    }
    try {
      localStorage.setItem('tmusic.lastBoot', JSON.stringify({ session: session, stage: 'html', at: Date.now(), path: '' }));
    } catch (e) {
      /* no storage: no crash detection */
    }
    send({ kind: 'boot', stage: 'html' });
    window.__tmBoot = { session: session, platform: platform, version: version, send: send };
  } catch (e) {
    /* diagnostics must never become the problem */
  }
})();
