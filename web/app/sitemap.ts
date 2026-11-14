import type { MetadataRoute } from "next";
import { SITE_URL } from "@/lib/site";

// Public pages only. `/` is the landing page for visitors who are not logged in (and the owner
// app for the owner); the other owner app pages are left out.
export default function sitemap(): MetadataRoute.Sitemap {
  return ["/", "/pricing", "/privacy", "/terms"].map((path) => ({ url: `${SITE_URL}${path}` }));
}
