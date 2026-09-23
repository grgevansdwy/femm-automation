import Link from "next/link";
import type { ReactNode } from "react";
import { codeHref } from "@/lib/repo";

export type Layer = "front" | "agent" | "engine" | "solver" | "data";
export const LAYER_NAME: Record<Layer, string> = { front: "Frontend", agent: "Agent", engine: "Engine", solver: "Solver", data: "Data" };

export function Callout({ kind = "plain", title, children }: { kind?: "plain" | "why" | "watch" | "real"; title?: string; children: ReactNode }) {
  const t = title ?? { plain: "In plain words", why: "Why it's built this way", watch: "Worth knowing", real: "Real, from your data" }[kind];
  return <div className={`callout ${kind}`}><div className="t">{t}</div>{children}</div>;
}

export function Pill({ layer, children }: { layer?: Layer | "good" | "bad" | "warn" | "gray"; children: ReactNode }) {
  return <span className={`pill ${layer ?? "gray"}`}>{children}</span>;
}

/** A link into the in-app code viewer, at the line where `find` is defined. */
export function FileRef({ file, find, label }: { file: string; find?: string; label?: string }) {
  return <Link className="fileref" href={codeHref(file, find)} title={`Open ${file}${find ? ` at ${find}` : ""}`}>{label ?? file.split("/").slice(-2).join("/")}</Link>;
}

export function PageHead({ eyebrow, title, lede }: { eyebrow: ReactNode; title: string; lede: ReactNode }) {
  return <header><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p className="lede">{lede}</p></header>;
}

export function ErrorBox({ error, hint }: { error: string; hint?: string }) {
  return <div className="err">Could not reach the backend bridge.{hint ? `\n${hint}` : ""}{"\n\n"}{error}</div>;
}

export * from "@/lib/format";
