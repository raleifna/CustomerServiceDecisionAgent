import { MouseEvent, useSyncExternalStore } from "react";

// A deliberately tiny History-API router: the shop has a handful of fixed
// routes, and nginx already falls back to index.html for any path.

const NAVIGATION_EVENT = "shopcare:navigate";

function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  window.addEventListener(NAVIGATION_EVENT, callback);
  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(NAVIGATION_EVENT, callback);
  };
}

function snapshot() {
  return window.location.pathname + window.location.search;
}

export function navigate(to: string, options: { replace?: boolean } = {}) {
  if (to === snapshot()) return;
  if (options.replace) window.history.replaceState({}, "", to);
  else window.history.pushState({}, "", to);
  window.dispatchEvent(new Event(NAVIGATION_EVENT));
  if (!options.replace) window.scrollTo({ top: 0 });
}

export type Location = { path: string; segments: string[]; query: URLSearchParams };

export function useLocation(): Location {
  const href = useSyncExternalStore(subscribe, snapshot);
  const url = new URL(href, window.location.origin);
  return {
    path: url.pathname,
    segments: url.pathname.split("/").filter(Boolean).map(decodeURIComponent),
    query: url.searchParams,
  };
}

// For <a href> elements: keep real links (middle-click, copy link) while
// doing client-side navigation on a plain left click.
export function linkHandler(to: string) {
  return (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(to);
  };
}
