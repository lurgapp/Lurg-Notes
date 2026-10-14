import type { Metadata } from "next";
import { createElement } from "react";

export const SEO = {
  main: {
    host: "lurg.pro",
    url: "https://lurg.pro",
    name: "Lurg",
    title: "Lurg — ваша личная экосистема",
    description:
      "Заметки, личные знания и повседневные инструменты. Lurg Notes, Graph, Nearby, Browser и NetFix в одной экосистеме.",
    ogAlt: "Lurg — ваша личная экосистема",
  },
  core: {
    host: "core.lurg.pro",
    url: "https://core.lurg.pro",
    name: "Lurg Core",
    title: "Lurg Core - технологическая основа экосистемы",
    description:
      "Lurg Core объединяет общие контракты, идентификацию, безопасность и инфраструктуру для сервисов экосистемы Lurg.",
    ogAlt: "Lurg Core - технологическая основа экосистемы",
  },
  graph: {
    host: "graph.lurg.pro",
    url: "https://graph.lurg.pro",
    name: "Lurg Graph",
    title: "Lurg Graph - персональный граф знаний",
    description:
      "Lurg Graph превращает личную информацию в связанный граф знаний с контролем владельца и приватным контекстом.",
    ogAlt: "Lurg Graph - персональный граф знаний",
  },
  docs: {
    host: "docs.lurg.pro",
    url: "https://docs.lurg.pro",
    name: "Lurg Documentation",
    title: "Lurg Documentation - документация для разработчиков",
    description:
      "Документация Lurg: архитектура Core, Graph, connector platform, релизы и инженерные принципы экосистемы.",
    ogAlt: "Lurg Documentation - документация для разработчиков",
  },
  api: {
    host: "api.lurg.pro",
    url: "https://api.lurg.pro",
    name: "Lurg API",
    title: "Lurg API - OpenAPI-документация",
    description:
      "OpenAPI-документация Lurg Core API для Identity, Graph и сервисных контрактов экосистемы.",
    ogAlt: "Lurg API - OpenAPI-документация",
  },
  auth: {
    host: "auth.lurg.pro",
    url: "https://auth.lurg.pro",
    name: "Lurg ID",
    title: "Lurg ID - единая учетная запись Lurg",
    description:
      "Защищенный вход Lurg ID для сервисов экосистемы Lurg.",
    ogAlt: "Lurg ID - единая учетная запись Lurg",
  },
} as const;

export type SeoSiteKey = keyof typeof SEO;

export const indexedHosts = [SEO.main.host, SEO.core.host, SEO.graph.host, SEO.docs.host];

export function siteUrl(key: SeoSiteKey, path = "/") {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${SEO[key].url}${cleanPath === "/" ? "" : cleanPath}`;
}

export function ogImageUrl(key: SeoSiteKey) {
  return `${SEO[key].url}/og?site=${key}&v=20261003`;
}

export function buildMetadata({
  site,
  path = "/",
  title,
  description,
  index = true,
  type = "website",
}: {
  site: SeoSiteKey;
  path?: string;
  title?: string;
  description?: string;
  index?: boolean;
  type?: "website" | "article";
}): Metadata {
  const config = SEO[site];
  const canonical = siteUrl(site, path);
  const resolvedTitle = title || config.title;
  const resolvedDescription = description || config.description;
  const image = ogImageUrl(site);

  return {
    metadataBase: new URL(config.url),
    title: resolvedTitle,
    description: resolvedDescription,
    alternates: {
      canonical,
    },
    robots: index
      ? {
          index: true,
          follow: true,
          googleBot: {
            index: true,
            follow: true,
            "max-image-preview": "large",
            "max-snippet": -1,
            "max-video-preview": -1,
          },
        }
      : "noindex, nofollow, noarchive",
    openGraph: {
      title: resolvedTitle,
      description: resolvedDescription,
      url: canonical,
      siteName: config.name,
      type,
      locale: "ru_RU",
      images: [
        {
          url: image,
          width: 1200,
          height: 630,
          alt: config.ogAlt,
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: resolvedTitle,
      description: resolvedDescription,
      images: [image],
    },
    icons: {
      icon: [
        { url: "/favicon.ico", sizes: "any" },
        { url: "/icon.svg", type: "image/svg+xml" },
      ],
      apple: [{ url: "/apple-touch-icon.svg", type: "image/svg+xml" }],
    },
    manifest: "/manifest.webmanifest",
  };
}

export function organizationJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": `${SEO.main.url}/#organization`,
    name: "Lurg",
    url: SEO.main.url,
    logo: `${SEO.main.url}/icon.svg`,
    sameAs: [SEO.docs.url, SEO.core.url, SEO.graph.url],
  };
}

export function websiteJsonLd(site: SeoSiteKey, path = "/") {
  return {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": `${siteUrl(site, path)}#website`,
    name: SEO[site].name,
    url: siteUrl(site, path),
    inLanguage: "ru-RU",
    publisher: { "@id": `${SEO.main.url}/#organization` },
  };
}

export function webApplicationJsonLd(site: "core" | "graph") {
  return {
    "@context": "https://schema.org",
    "@type": "WebApplication",
    "@id": `${SEO[site].url}/#application`,
    name: SEO[site].name,
    url: SEO[site].url,
    applicationCategory: site === "graph" ? "KnowledgeManagementApplication" : "DeveloperApplication",
    operatingSystem: "Web",
    description: SEO[site].description,
    publisher: { "@id": `${SEO.main.url}/#organization` },
  };
}

export function breadcrumbJsonLd(items: { name: string; url: string }[]) {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.name,
      item: item.url,
    })),
  };
}

export function jsonLdScript(data: object | object[]) {
  return createElement("script", {
    type: "application/ld+json",
    dangerouslySetInnerHTML: {
      __html: JSON.stringify(data).replace(/</g, "\\u003c"),
    },
  });
}
