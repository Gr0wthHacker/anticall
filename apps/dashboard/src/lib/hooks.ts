"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "./api/errors";

export interface ResourceState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/**
 * Load a resource whenever `key` changes. Pass null to skip loading.
 * `load` may change between renders; only `key` controls refetching.
 */
export function useResource<T>(key: string | null, load: () => Promise<T>): ResourceState<T> {
  const loadRef = useRef(load);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(key !== null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    setLoading(true);
    loadRef
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export interface ActionState {
  pending: boolean;
  error: string | null;
  raw: unknown;
  run: <R>(fn: () => Promise<R>) => Promise<R | undefined>;
  clear: () => void;
}

/** Run a mutation, tracking pending state and the last error. */
export function useAction(): ActionState {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<unknown>(null);

  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setPending(true);
    setError(null);
    setRaw(null);
    try {
      return await fn();
    } catch (err) {
      setError(describeError(err));
      setRaw(err);
      return undefined;
    } finally {
      setPending(false);
    }
  }, []);

  const clear = useCallback(() => {
    setError(null);
    setRaw(null);
  }, []);
  return { pending, error, raw, run, clear };
}
