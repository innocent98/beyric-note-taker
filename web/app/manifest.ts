import type { MetadataRoute } from "next";
import { BRAND } from "@/lib/brand";
import { SITE_DESCRIPTION, SITE_NAME } from "@/lib/site";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: SITE_NAME,
    short_name: "Beyric Notes",
    description: SITE_DESCRIPTION,
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: BRAND.bgLight,
    theme_color: BRAND.teal,
    // Lets the installed app appear in Android's share sheet: sharing a Google Meet or Zoom link (or
    // the invitation text around it) to Beyric Notes opens /share. POST, so what is shared goes in
    // a request body and not into a URL that nginx logs; see app/share-target/route.ts. Chrome
    // offers it only once the app is installed, and the fields are all optional: Android puts the
    // link in `text`.
    share_target: {
      action: "/share-target",
      method: "POST",
      enctype: "multipart/form-data",
      params: { title: "title", text: "text", url: "url" },
    },
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
