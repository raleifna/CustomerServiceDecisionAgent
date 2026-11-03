import { useSyncExternalStore } from "react";

// Signed-in demo account and shopping cart. Both are per-browser
// conveniences only (localStorage, best-effort): orders and after-sales
// records always come from the backend, never from here.

const CUSTOMER_KEY = "shopcare:active-customer";
const CART_KEY = "shopcare:cart:v1";
const CHANGE_EVENT = "shopcare:store";

export type CartLine = {
  product_id: string;
  sku_id: string;
  sku_name: string;
  quantity: number;
  selected: boolean;
};

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // best-effort persistence only
  }
  cache.delete(key);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

// useSyncExternalStore needs referentially stable snapshots.
const cache = new Map<string, unknown>();
function cached<T>(key: string, fallback: T): T {
  if (!cache.has(key)) cache.set(key, read(key, fallback));
  return cache.get(key) as T;
}

function subscribe(callback: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === CUSTOMER_KEY || event.key === CART_KEY) {
      cache.clear();
      callback();
    }
  };
  window.addEventListener(CHANGE_EVENT, callback);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, callback);
    window.removeEventListener("storage", onStorage);
  };
}

export function useActiveCustomerId(): string | null {
  return useSyncExternalStore(subscribe, () => cached<string | null>(CUSTOMER_KEY, null));
}

export function setActiveCustomerId(customerId: string | null) {
  write(CUSTOMER_KEY, customerId);
}

export function useCart(): CartLine[] {
  return useSyncExternalStore(subscribe, () => cached<CartLine[]>(CART_KEY, []));
}

function cartLines(): CartLine[] {
  return cached<CartLine[]>(CART_KEY, []);
}

export function addToCart(line: Omit<CartLine, "selected">) {
  const lines = cartLines();
  const existing = lines.find((item) => item.sku_id === line.sku_id);
  write(CART_KEY, existing
    ? lines.map((item) => item.sku_id === line.sku_id ? { ...item, quantity: Math.min(99, item.quantity + line.quantity) } : item)
    : [...lines, { ...line, selected: true }]);
}

export function updateCartLine(skuId: string, patch: Partial<CartLine>) {
  write(CART_KEY, cartLines().map((item) => item.sku_id === skuId ? { ...item, ...patch } : item));
}

export function removeCartLines(skuIds: string[]) {
  write(CART_KEY, cartLines().filter((item) => !skuIds.includes(item.sku_id)));
}

export function setAllCartSelected(selected: boolean) {
  write(CART_KEY, cartLines().map((item) => ({ ...item, selected })));
}
