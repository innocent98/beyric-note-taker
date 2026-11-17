import type { ReactNode } from "react";
import { PublicShell } from "@/components/site/public-shell";

// Every page in this group (the landing page at /welcome, /pricing, /privacy, /terms) is drawn inside the
// one public header and footer. Add a public page by adding a folder here: nothing else to wire.
export default function PublicLayout({ children }: { children: ReactNode }) {
  return <PublicShell>{children}</PublicShell>;
}
