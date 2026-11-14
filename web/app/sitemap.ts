import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

// Public pages only. `/` is the landing page for everyone; the owner app pages (`/meetings` and the
// rest) are left out.
export default function sitemap(): MetadataRoute.Sitemap {
  return ["/", "/privacy", "/terms"].map((path) => ({ url: `${SITE_URL}${path}` }));
}
