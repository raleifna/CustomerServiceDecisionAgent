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
let customersPromise: Promise<Customer[]> | null = null;

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

export function useCustomers(): Customer[] {
  const [customers, setCustomers] = useState<Customer[]>([]);
  useEffect(() => {
    let alive = true;
    customersPromise ??= fetchJson<Customer[]>("/customers").catch((error) => {
      customersPromise = null;
      throw error;
    });
    customersPromise.then((data) => alive && setCustomers(data)).catch(() => undefined);
    return () => { alive = false; };
  }, []);
  return customers;
}

export function useCustomerOrders(customerId: string | null): AsyncState<CustomerOrder[]> {
  return useFetch<CustomerOrder[]>(customerId ? `/customers/${customerId}/orders` : null);
}
