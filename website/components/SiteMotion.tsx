"use client";

import { useEffect } from "react";

const SELECTOR = [
  ".productLanding .plHero",
  ".productLanding .plSection",
  ".productLanding .plCta",
  ".subSite .subHero",
  ".subSite main > section:not(.subHero)",
  ".notesLanding .notesHero",
  ".notesLanding .notesFeatures",
  ".docsSite .docsHome > section",
].join(",");

/** Adds quiet, one-time entrance motion without changing page semantics. */
export default function SiteMotion() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const targets = Array.from(document.querySelectorAll<HTMLElement>(SELECTOR));
    targets.forEach((target, index) => {
      target.classList.add("reveal-unit");
      target.style.setProperty("--reveal-delay", `${Math.min(index % 3, 2) * 70}ms`);
    });

    document.documentElement.classList.add("motion-ready");
    const observer = new IntersectionObserver(
      (entries) => entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }),
      { threshold: 0.12, rootMargin: "0px 0px -8%" },
    );
    targets.forEach((target) => observer.observe(target));

    return () => {
      observer.disconnect();
      document.documentElement.classList.remove("motion-ready");
    };
  }, []);

  return null;
}
