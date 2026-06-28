"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, useEffect } from "react";
import { useSession } from "@/lib/store";
import { Toaster } from "@/components/ui/toaster";
import { PwaInstallPrompt } from "@/components/pwa-install-prompt";

function AccentApplier() {
  const accent = useSession((s) => s.accentTheme);
  useEffect(() => {
    document.documentElement.setAttribute("data-accent", accent);
  }, [accent]);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );
  return (
    <QueryClientProvider client={client}>
      <AccentApplier />
      {children}
      <Toaster />
      <PwaInstallPrompt />
    </QueryClientProvider>
  );
}
