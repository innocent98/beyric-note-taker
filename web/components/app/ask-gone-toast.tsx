"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { ASK_CHIP_FLAG, ASK_GONE_MESSAGE } from "@/lib/ask";

// On the not-found page: when the visitor got here by clicking a source chip on Ask Beyric for this
// very path, say why (the designer's toast "That meeting is no longer available"). Read once, then
// cleared. A flag for another path is stale and is cleared without a message.
export function AskGoneToast() {
  const pathname = usePathname();
  const [toast, flash] = useToast(3200);
  useEffect(() => {
    try {
      const target = sessionStorage.getItem(ASK_CHIP_FLAG);
      if (target === null) return;
      sessionStorage.removeItem(ASK_CHIP_FLAG);
      if (target === pathname) flash(ASK_GONE_MESSAGE);
    } catch {
      // Private mode: the 404 page is shown without the toast.
    }
  }, [pathname, flash]);
  return <Toast message={toast} />;
}
