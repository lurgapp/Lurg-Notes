import "./globals.css";
import "./public.css";
import type { Viewport } from "next";
import { buildMetadata } from "@/lib/seo";
import SiteMotion from "@/components/SiteMotion";

export const metadata = buildMetadata({ site: "main" });

export const viewport: Viewport = {
  themeColor: "#ffffff",
  colorScheme: "light",
};

export default function Layout({children}:{children:React.ReactNode}){return <html lang="ru"><body><SiteMotion />{children}</body></html>}
