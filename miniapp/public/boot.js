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

    function currentStage() {
      try {
        return (JSON.parse(localStorage.getItem('tmusic.lastBoot') || '{}') || {}).stage || 'html';
      } catch (e) {
        return 'html';
      }
    }

    // Errors before the app's own handlers exist: a bundle the web view cannot parse,
    // a script that fails to load. File and line go with it (a cross-origin script
    // only ever says "Script error.", which is why Telegram's bridge is now local).
    var early = 0;
    window.addEventListener(
      'error',
      function (event) {
        if (window.__tmAppStarted || early >= 5) return;
        early += 1;
        var target = event.target;
        var failed = target && target !== window && (target.src || target.href);
        send({
          kind: 'error',
          stage: currentStage(),
          message: failed
            ? 'failed to load ' + String(failed).split('?')[0]
            : String(event.message || 'error') + ' @ ' + String(event.filename || '').split('?')[0] + ':' + event.lineno + ':' + event.colno,
          stack: event.error && event.error.stack ? String(event.error.stack).slice(0, 4000) : '',
        });
      },
      true
    );

    // Closing the app while it is still loading is not a crash.
    window.addEventListener('pagehide', function () {
      if (window.__tmAppStarted) return;
      try {
        localStorage.setItem('tmusic.lastBoot', JSON.stringify({ session: session, stage: 'closed', at: Date.now(), path: '' }));
      } catch (e) {
        /* ignore */
      }
    });

    // Still not started after a while: say what the page was waiting for.
    setTimeout(function () {
      if (window.__tmAppStarted) return;
      var slow = [];
      try {
        var entries = performance.getEntriesByType('resource');
        for (var i = 0; i < entries.length; i += 1) {
          var entry = entries[i];
          if (!/\.(js|css)(\?|$)/.test(entry.name)) continue;
          slow.push(entry.name.split('?')[0].replace(location.origin, '') + ' ' + Math.round(entry.duration) + 'ms ' + (entry.transferSize || 0) + 'B');
        }
      } catch (e) {
        /* no resource timing */
      }
      send({
        kind: 'error',
        stage: currentStage(),
        message: 'app did not start in 15 s (readyState ' + document.readyState + ')',
        stack: slow.join('\n').slice(0, 4000),
      });
    }, 15000);
  } catch (e) {
    /* diagnostics must never become the problem */
  }
})();
