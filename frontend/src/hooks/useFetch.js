// useFetch — small helper that wraps useApi().get for the common
// loading/error/data lifecycle every list/detail page needs. Returns
// { data, loading, error, reload, setData }.
import { useState, useEffect, useCallback, useRef } from "react";
import { useApi } from "./useApi.js";

export function useFetch(path, deps = []) {
  const api = useApi();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [error, setError] = useState(null);
  const reqId = useRef(0);

  const load = useCallback(async () => {
    if (!path) {
      setLoading(false);
      return;
    }
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await api.get(path);
      if (id === reqId.current) setData(result);
    } catch (err) {
      if (id === reqId.current) setError(err);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, api]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps.length ? deps : [path]);

  return { data, loading, error, reload: load, setData };
}
