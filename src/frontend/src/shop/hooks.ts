import { DependencyList, useEffect, useState } from "react";

import { Customer, CustomerOrder, fetchJson, isAbort, ProductCard } from "./api";

export type AsyncState<T> = { data: T | null; error: string | null; loading: boolean };

export function useFetch<T>(path: string | null, deps: DependencyList = [path]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: Boolean(path) });
  useEffect(() => {
    if (!path) {
      setState({ data: null, error: null, loading: false });
      return;
    }
    const controller = new AbortController();
    setState((current) => ({ data: current.data, error: null, loading: true }));
    fetchJson<T>(path, controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => {
        if (isAbort(error)) return;
        setState({ data: null, error: error instanceof Error ? error.message : "加载失败", loading: false });
      });
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

// The whole catalog is small; load it once per page load and share it
// between the home page, the cart and the header search suggestions.
let catalogPromise: Promise<ProductCard[]> | null = null;
let accountsPromise: Promise<Customer[]> | null = null;

export function useCatalog(): ProductCard[] {
  const [products, setProducts] = useState<ProductCard[]>([]);
  useEffect(() => {
    let alive = true;
    catalogPromise ??= fetchJson<ProductCard[]>("/products").catch((error) => {
      catalogPromise = null;
      throw error;
    });
    catalogPromise.then((data) => alive && setProducts(data)).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  return products;
}

/** The demo-login account picker (empty when demo login is disabled). */
export function useDemoAccounts(enabled = true): Customer[] {
  const [accounts, setAccounts] = useState<Customer[]>([]);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    accountsPromise ??= fetchJson<Customer[]>("/auth/demo-accounts").catch((error) => {
      accountsPromise = null;
      throw error;
    });
    accountsPromise.then((data) => alive && setAccounts(data)).catch(() => undefined);
    return () => { alive = false; };
  }, [enabled]);
  return accounts;
}

/** The signed-in customer's own profile (re-fetched when the account changes). */
export function useMyProfile(customerId: string | null): Customer | undefined {
  return useFetch<Customer>(customerId ? "/auth/me" : null, [customerId]).data ?? undefined;
}

export function useCustomerOrders(customerId: string | null): AsyncState<CustomerOrder[]> {
  return useFetch<CustomerOrder[]>(customerId ? "/me/orders" : null, [customerId]);
}
