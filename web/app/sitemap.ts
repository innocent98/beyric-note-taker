import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

// Public pages only. `/` is the landing page for visitors who are not logged in (FastAPI serves
// it today); the owner app and /welcome (a review copy) are left out.
export default function sitemap(): MetadataRoute.Sitemap {
  return ["/", "/privacy", "/terms"].map((path) => ({ url: `${SITE_URL}${path}` }));
}
