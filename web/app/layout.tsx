import type { Metadata, Viewport } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";
import { Behaviors } from "@/components/behaviors";
import { BRAND } from "@/lib/brand";
import { OG_IMAGES, SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";
import "../styles/tokens.css";
import "../styles/components.css";
import "../styles/pages.css";
import "../styles/public.css";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: { default: SITE_NAME, template: `%s - ${SITE_NAME}` },
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    locale: "en_GB",
    images: OG_IMAGES,
  },
  twitter: { card: "summary_large_image", images: ["/og.png"] },
  icons: { apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = {
  themeColor: BRAND.bgLight,
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Pages render per request so Next can put the proxy's CSP nonce on its scripts.
  await connection();
  return (
    <html lang="en">
      <head>
        <link rel="preload" href="/fonts/geist-latin-wght-normal.woff2" as="font" type="font/woff2" crossOrigin="anonymous" />
      </head>
      <body>
        {children}
        <Behaviors />
      </body>
    </html>
  );
}
