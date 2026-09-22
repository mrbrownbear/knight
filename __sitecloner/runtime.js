(() => {
  const sourceHosts = new Set(["scfo.de", "www.scfo.de"]);
  const blocked = "/__sitecloner/blocked";
  const allowedApi = new Set(["https://elraogzemjimssikyufa.supabase.co/functions/v1/scfo-submit-lead"]);

  const parse = value => {
    try {
      const raw = String(value && value.url ? value.url : value);
      if (!raw || raw.startsWith("data:") || raw.startsWith("blob:") || raw.startsWith("about:")) return null;
      return { raw, url: new URL(raw, location.href) };
    } catch {
      return null;
    }
  };

  const localSourceUrl = value => {
    const parsed = parse(value);
    if (!parsed) return value;
    const { raw, url } = parsed;
    if (sourceHosts.has(url.hostname)) return `${url.pathname}${url.search}${url.hash}` || "/";
    if (url.origin === location.origin) return raw;
    return raw;
  };

  const resourceUrl = value => {
    const parsed = parse(value);
    if (!parsed) return value;
    const { raw, url } = parsed;
    if (sourceHosts.has(url.hostname)) return `${url.pathname}${url.search}` || "/";
    if (url.origin === location.origin) return raw;
    if (allowedApi.has(`${url.origin}${url.pathname}`)) return raw;
    if (["http:", "https:", "ws:", "wss:"].includes(url.protocol)) return blocked;
    return raw;
  };

  const originalFetch = window.fetch;
  window.fetch = (input, init) => originalFetch.call(window, resourceUrl(input), init);

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    return originalOpen.call(this, method, resourceUrl(url), ...rest);
  };

  for (const [Ctor, name] of [[window.Worker, "Worker"], [window.SharedWorker, "SharedWorker"]]) {
    try {
      if (Ctor) window[name] = function(url, options) { return new Ctor(resourceUrl(url), options); };
    } catch {}
  }

  try {
    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
      const mapped = resourceUrl(url);
      if (mapped === blocked) throw new DOMException("External network access blocked for localized build", "SecurityError");
      return new OriginalWebSocket(mapped, protocols);
    };
  } catch {}

  try {
    const OriginalEventSource = window.EventSource;
    window.EventSource = function(url, options) {
      const mapped = resourceUrl(url);
      if (mapped === blocked) throw new DOMException("External network access blocked for localized build", "SecurityError");
      return new OriginalEventSource(mapped, options);
    };
  } catch {}

  try {
    const originalBeacon = navigator.sendBeacon && navigator.sendBeacon.bind(navigator);
    if (originalBeacon) navigator.sendBeacon = (url, data) => {
      const mapped = resourceUrl(url);
      return mapped === blocked ? false : originalBeacon(mapped, data);
    };
  } catch {}

  try {
    const sw = navigator.serviceWorker;
    if (sw && sw.register) {
      const register = sw.register.bind(sw);
      sw.register = (url, options) => {
        const mapped = resourceUrl(url);
        return mapped === blocked
          ? Promise.reject(new DOMException("External service worker blocked for localized build", "SecurityError"))
          : register(mapped, options);
      };
    }
  } catch {}

  for (const [Ctor, prop] of [
    [HTMLImageElement, "src"],
    [HTMLScriptElement, "src"],
    [HTMLLinkElement, "href"],
    [HTMLVideoElement, "src"],
    [HTMLAudioElement, "src"],
    [HTMLSourceElement, "src"],
    [HTMLIFrameElement, "src"]
  ]) {
    try {
      const descriptor = Object.getOwnPropertyDescriptor(Ctor.prototype, prop);
      if (descriptor && descriptor.set) {
        Object.defineProperty(Ctor.prototype, prop, {
          ...descriptor,
          set(value) { return descriptor.set.call(this, resourceUrl(value)); }
        });
      }
    } catch {}
  }

  const originalSetAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {
    const key = String(name).toLowerCase();
    if (["src", "data", "action"].includes(key)) value = resourceUrl(value);
    if (key === "href" && !(this instanceof HTMLAnchorElement)) value = resourceUrl(value);
    if (key === "href" && this instanceof HTMLAnchorElement) value = localSourceUrl(value);
    return originalSetAttribute.call(this, name, value);
  };

  document.addEventListener("click", event => {
    const anchor = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!anchor) return;
    const mapped = localSourceUrl(anchor.href);
    if (mapped !== anchor.href && sourceHosts.has(new URL(anchor.href, location.href).hostname)) anchor.href = mapped;
  }, true);

  try {
    const originalWindowOpen = window.open.bind(window);
    window.open = (url, ...rest) => originalWindowOpen(localSourceUrl(url), ...rest);
  } catch {}
})();
