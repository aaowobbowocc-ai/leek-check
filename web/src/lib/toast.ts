"use client";

export type ToastTone = "info" | "warn" | "error" | "ok";

export type ToastEvent = {
  id: string;
  message: string;
  tone: ToastTone;
};

const listeners: Array<(t: ToastEvent) => void> = [];
let counter = 0;

export function toast(message: string, tone: ToastTone = "info") {
  counter += 1;
  const evt: ToastEvent = { id: `${counter}`, message, tone };
  listeners.forEach((l) => l(evt));
}

export function onToast(fn: (t: ToastEvent) => void): () => void {
  listeners.push(fn);
  return () => {
    const i = listeners.indexOf(fn);
    if (i >= 0) listeners.splice(i, 1);
  };
}
