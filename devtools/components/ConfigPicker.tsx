"use client";
import { useApi, type ConfigRow } from "./useApi";

export function ConfigPicker({ value, onChange, distance, onDistance, distances = [3, 5, 8, 12, 20, 30] }:
  { value: string; onChange: (id: string) => void; distance: number; onDistance: (d: number) => void; distances?: number[] }) {
  const { data } = useApi<ConfigRow[]>("/api/configs");
  return (
    <div className="row">
      <label className="f">Coil design
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          {(data ?? [{ id: value, name: value, builtin: false }] as ConfigRow[]).map((c) => <option key={c.id} value={c.id}>{c.name}{c.builtin ? " (built-in)" : ""}</option>)}
        </select>
      </label>
      <label className="f">Distance to the receive coil
        <select value={distance} onChange={(e) => onDistance(Number(e.target.value))}>
          {distances.map((d) => <option key={d} value={d}>{d} mm</option>)}
        </select>
      </label>
    </div>
  );
}
