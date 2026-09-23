"use client";
import { useEffect, useState } from "react";

export function useApi<T = any>(url: string | null): { data: T | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: !!url });
  useEffect(() => {
    if (!url) return;
    let dead = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetch(url).then(async (r) => {
      const j = await r.json();
      if (dead) return;
      if (j && j.error && Object.keys(j).length === 1) setState({ data: null, error: String(j.error), loading: false });
      else setState({ data: j, error: null, loading: false });
    }).catch((e) => !dead && setState({ data: null, error: String(e), loading: false }));
    return () => { dead = true; };
  }, [url]);
  return state;
}

export type ConfigRow = { id: string; name: string; builtin: boolean; turns: number[]; layers: number[]; ferrite: boolean; metal: boolean };
