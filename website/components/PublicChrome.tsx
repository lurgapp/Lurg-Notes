import { ArrowUpRight } from "lucide-react";
export function LurgMark() { return <span className="lurgMark" aria-hidden="true"><i /><i /><i /><i /></span>; }
export function PublicHeader({ product }: { product?: string }) {
  return <header className="publicHeader"><div className="siteWidth publicHeaderInner"><a className="publicBrand" href="https://lurg.pro"><LurgMark /><span>Lurg</span>{product && <b>{product}</b>}</a><nav aria-label="Основная навигация"><a href="https://lurg.pro/#products">Приложения</a><a href="https://lurg.pro/#privacy">Приватность</a><a href="https://docs.lurg.pro">Документация</a></nav><a className="headerAccount" href="https://auth.lurg.pro">Lurg ID <ArrowUpRight size={14} /></a><details className="publicMobileMenu"><summary aria-label="Открыть меню"><span /><span /></summary><nav aria-label="Мобильная навигация"><a href="https://lurg.pro/#products">Приложения</a><a href="https://lurg.pro/#privacy">Приватность</a><a href="https://docs.lurg.pro">Документация</a><a href="https://auth.lurg.pro">Lurg ID</a></nav></details></div></header>;
}
export function PublicFooter() {
  return <footer className="publicFooter siteWidth"><div><a className="publicBrand" href="https://lurg.pro"><LurgMark /> Lurg</a><span>Личные инструменты. Общая экосистема.</span></div><nav aria-label="Ссылки в подвале"><a href="https://docs.lurg.pro">Документация</a><a href="https://lurg.pro/privacy">Приватность</a><a href="https://lurg.pro/terms">Условия</a></nav><small>© 2026 Lurg</small></footer>;
}
