// Loaded first by every page of the local site (build.py injects it; it gets
// window.__CRAFT_LOCAL__ prepended). It makes the stock web builds work offline from one origin:
//  - GitHub release API calls and screenshot downloads are answered from _local/;
//  - a launcher download button (github.com/<repo>/releases/download/...) opens /<slug>/ instead;
//  - localStorage is namespaced per app, so apps sharing localhost don't overwrite each other's settings.
(function () {
  "use strict";
  var M = window.__CRAFT_LOCAL__;
  if (!M) return;
  var script = document.currentScript;
  var localBase = new URL("./", script.src).href; // .../_local/
  var siteRoot = new URL("../", localBase).href; // .../
  var slugByRepo = {};
  var slugs = {};
  M.apps.forEach(function (a) {
    if (a.web) {
      slugByRepo[a.repo.toLowerCase()] = a.slug;
      slugs[a.slug] = true;
    }
  });
  var known = {};
  known[M.launcher.repo.toLowerCase()] = true;
  M.apps.forEach(function (a) { known[a.repo.toLowerCase()] = true; });

  // Which app this page is: the first path segment under the site root.
  var rest = location.href.slice(siteRoot.length).split(/[/?#]/)[0];
  var space = slugs[rest] ? rest : "launcher";

  // --- localStorage, one namespace per app -------------------------------------------------
  try {
    var proto = Storage.prototype;
    var get = proto.getItem, set = proto.setItem, del = proto.removeItem;
    var prefix = "craft-local/" + space + "/";
    proto.getItem = function (k) { return this === window.localStorage ? get.call(this, prefix + k) : get.call(this, k); };
    proto.setItem = function (k, v) { return this === window.localStorage ? set.call(this, prefix + k, v) : set.call(this, k, v); };
    proto.removeItem = function (k) { return this === window.localStorage ? del.call(this, prefix + k) : del.call(this, k); };
  } catch (e) { /* storage disabled: nothing to namespace */ }

  // --- fetch: GitHub release API and screenshots from _local/ ----------------------------
  var realFetch = window.fetch.bind(window);
  var api = /^https:\/\/api\.github\.com\/repos\/([^/]+\/[^/?#]+)\/releases(\/latest)?\/?(\?.*)?$/i;
  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input instanceof URL ? input.href : input && input.url;
    if (url) {
      var m = api.exec(url);
      if (m && known[m[1].toLowerCase()]) {
        var file = m[1].replace("/", "__") + (m[2] ? ".json" : ".list.json");
        return realFetch(localBase + "releases/" + file);
      }
      if (url.indexOf(M.shotsBase) === 0) {
        return realFetch(localBase + "screenshots/" + url.slice(M.shotsBase.length));
      }
    }
    return realFetch(input, init);
  };

  // --- window.open: a download of a known app opens its local web version -----------------
  var realOpen = window.open.bind(window);
  var dl = /^https:\/\/github\.com\/([^/]+\/[^/]+)\/releases\/download\//i;
  window.open = function (url) {
    try {
      var m = dl.exec(String(url));
      var slug = m && slugByRepo[m[1].toLowerCase()];
      if (slug) {
        location.href = siteRoot + slug + "/";
        return null;
      }
    } catch (e) { /* fall through to the real opener */ }
    return realOpen.apply(window, arguments);
  };
})();
