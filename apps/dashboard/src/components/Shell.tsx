"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { FeaturesProvider } from "@/lib/features";
import { SkeletonLoader } from "./SkeletonLoader";

const NAV: { href: string; label: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/calls", label: "Calls" },
  { href: "/callbacks", label: "Callbacks" },
  { href: "/resolve", label: "Resolve" },
  { href: "/cases", label: "Cases" },
  { href: "/evidence", label: "Evidence" },
  { href: "/complaints", label: "Complaints" },
  { href: "/campaigns", label: "Campaigns (later)" },
  { href: "/rules", label: "Rules" },
  { href: "/settings", label: "Settings" },
  { href: "/health", label: "System Health" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

function Nav({ pathname, onNavigate }: { pathname: string; onNavigate: () => void }) {
  return (
    <nav aria-label="Primary">
      <ul className="space-y-1">
        {NAV.map((item) => {
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={`flex min-h-[44px] items-center rounded-md px-3 text-sm ${
                  active ? "bg-brand font-semibold text-brand-ink" : "hover:bg-surface"
                }`}
              >
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { status, me, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (status === "anon") router.replace("/login");
  }, [status, router]);

  if (status !== "authed") {
    return (
      <div className="mx-auto max-w-md p-6">
        <SkeletonLoader label="Checking your session" />
      </div>
    );
  }

  return (
    <FeaturesProvider>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-panel focus:p-3"
      >
        Skip to main content
      </a>
      <div className="min-h-screen lg:flex">
        <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2 lg:hidden">
          <span className="font-semibold">CallShield</span>
          <button
            type="button"
            className="btn"
            aria-expanded={open}
            aria-controls="sidebar"
            onClick={() => setOpen((v) => !v)}
          >
            Menu
          </button>
        </header>
        <aside
          id="sidebar"
          aria-label="Sidebar"
          className={`${open ? "block" : "hidden"} border-b border-line bg-panel p-3 lg:sticky lg:top-0 lg:block lg:h-screen lg:w-60 lg:shrink-0 lg:overflow-y-auto lg:border-b-0 lg:border-r`}
        >
          <p className="mb-3 hidden px-3 text-lg font-semibold lg:block">CallShield</p>
          <Nav pathname={pathname} onNavigate={() => setOpen(false)} />
          <div className="mt-4 border-t border-line px-3 pt-3 text-xs text-muted">
            <p className="break-all">{me?.email}</p>
            <p>{me?.role}</p>
            <button type="button" className="btn mt-2 w-full" onClick={logout}>
              Sign out
            </button>
          </div>
        </aside>
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 p-4 sm:p-6">
          {children}
        </main>
      </div>
    </FeaturesProvider>
  );
}
