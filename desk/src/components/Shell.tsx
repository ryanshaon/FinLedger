import { Bell, Building2, ChevronDown, LayoutDashboard, LogOut, Menu, Moon, PanelLeft, Search, Sun, Users } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { api, needsMfa } from "@/api";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { filterRows, matchesClient } from "@/deskSearch";
import { cn } from "@/lib/utils";
import { clientIdOf, queueHref, QUEUES, type QueueKey, type Route } from "@/router";
import { currentTheme, toggleTheme } from "@/theme";
import type { ClientRow, Session } from "@/types";

export function Shell({
  session,
  route,
  onNavigate,
  onSignOut,
  children,
}: {
  session: Session;
  route: Route;
  onNavigate: (url: string) => void;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [clients, setClients] = useState<ClientRow[]>([]);
  const [clientsState, setClientsState] = useState<"loading" | "ready" | "failed">("loading");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [night, setNight] = useState(currentTheme() === "night");
  const searchId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const clientId = clientIdOf(route);
  const allMatches = filterRows(clients, query, matchesClient);
  const matches = allMatches.slice(0, 6);

  useEffect(() => {
    setMobileOpen(false);
    setQuery("");
    setSearchOpen(false);
  }, [route]);

  useEffect(() => {
    if (needsMfa(session)) return;
    let live = true;
    setClientsState("loading");
    api.clients()
      .then((data) => { if (live) { setClients(data.clients); setClientsState("ready"); } })
      .catch(() => { if (live) { setClients([]); setClientsState("failed"); } });
    return () => { live = false; };
  }, [session.aal, session.mfa_required, session.user_id]);

  function follow(event: MouseEvent, url: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    onNavigate(url);
  }

  function openFirstMatch(event: FormEvent) {
    event.preventDefault();
    if (matches[0]) onNavigate(`/clients/${matches[0].id}`);
  }

  function moveInResults(event: KeyboardEvent<HTMLDivElement>) {
    const links = Array.from(event.currentTarget.querySelectorAll("a"));
    const at = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (event.key === "ArrowDown" && at < links.length - 1) {
      event.preventDefault();
      links[at + 1].focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (at > 0) links[at - 1].focus();
      else inputRef.current?.focus();
    } else if (event.key === "Escape") {
      setQuery("");
      setSearchOpen(false);
      inputRef.current?.focus();
    }
  }

  const nav = (
    <SidebarNav
      session={session}
      route={route}
      collapsed={collapsed}
      onFollow={follow}
      onSignOut={onSignOut}
    />
  );

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <a
        href="#main"
        onClick={(event) => { event.preventDefault(); document.getElementById("main")?.focus(); }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        Skip to main content
      </a>
      <aside className={cn("sticky top-0 hidden h-screen shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex lg:flex-col", collapsed ? "w-[76px]" : "w-64")}>
        {nav}
      </aside>
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent aria-describedby={undefined}>
          <SheetTitle className="sr-only">Menu</SheetTitle>
          <SidebarNav session={session} route={route} collapsed={false} onFollow={follow} onSignOut={onSignOut} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-2 border-b border-border bg-card/90 px-3 backdrop-blur sm:px-4">
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Menu" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}>
            <Menu />
          </Button>
          <Button variant="ghost" size="icon" className="hidden lg:inline-flex" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={() => setCollapsed((value) => !value)}>
            <PanelLeft />
          </Button>
          <div
            className="relative ml-auto hidden min-w-0 md:block"
            onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setSearchOpen(false); }}
          >
            <form role="search" onSubmit={openFirstMatch}>
              <label htmlFor={searchId} className="sr-only">Search companies</label>
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                id={searchId}
                ref={inputRef}
                type="search"
                autoComplete="off"
                value={query}
                placeholder="Search companies or GSTIN"
                onChange={(event) => { setQuery(event.target.value); setSearchOpen(true); }}
                onFocus={() => setSearchOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") { setQuery(""); setSearchOpen(false); }
                  else if (event.key === "ArrowDown") {
                    event.preventDefault();
                    resultsRef.current?.querySelector("a")?.focus();
                  }
                }}
                className="h-9 w-56 pl-9 xl:w-72"
              />
            </form>
            {searchOpen && query.trim() ? (
              <div
                ref={resultsRef}
                onMouseDown={(event) => event.preventDefault()}
                onKeyDown={moveInResults}
                className="absolute right-0 top-full z-30 mt-1 w-80 rounded-xl border border-border bg-card p-1 shadow-lg"
              >
                {clientsState === "loading" ? (
                  <p className="px-3 py-2 text-sm text-muted-foreground">Loading companies…</p>
                ) : clientsState === "failed" ? (
                  <p className="px-3 py-2 text-sm text-muted-foreground">Companies could not be loaded.</p>
                ) : matches.length === 0 ? (
                  <p className="px-3 py-2 text-sm text-muted-foreground">No assigned company matches “{query.trim()}”.</p>
                ) : (
                  <ul aria-label="Matching companies">
                    {matches.map((client) => (
                      <li key={client.id}>
                        <a
                          href={`/clients/${client.id}`}
                          onClick={(event) => follow(event, `/clients/${client.id}`)}
                          className="flex items-center justify-between gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
                        >
                          <span className="truncate font-medium">{client.name}</span>
                          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{client.gstin || "No GSTIN"}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                )}
                {allMatches.length > matches.length ? (
                  <p className="px-3 py-1.5 text-xs text-muted-foreground">Showing the first {matches.length} of {allMatches.length}. Keep typing to narrow.</p>
                ) : null}
              </div>
            ) : null}
            <p role="status" className="sr-only">
              {query.trim() && clientsState === "ready"
                ? (allMatches.length === 0 ? "No assigned company matches." : `${allMatches.length} ${allMatches.length === 1 ? "company matches" : "companies match"}.`)
                : ""}
            </p>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="hidden max-w-48 lg:inline-flex">
                <span className="truncate">{clients.find((client) => client.id === clientId)?.name ?? "All clients"}</span>
                <ChevronDown />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Client</DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => onNavigate("/clients")}>All clients</DropdownMenuItem>
              {clients.map((client) => (
                <DropdownMenuItem key={client.id} onSelect={() => onNavigate(`/clients/${client.id}`)}>
                  {client.name}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Notifications">
                <Bell />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Notifications</DropdownMenuLabel>
              <DropdownMenuItem disabled>No alerts right now.</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="sm" aria-pressed={night} onClick={() => setNight(toggleTheme() === "night")}>
            {night ? <Sun /> : <Moon />}
            <span className="hidden sm:inline">{night ? "Day" : "Night"}</span>
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="sm" className="gap-2 px-2">
                <img src="/finledger-mark.png" alt="" className="size-7 rounded-md" />
                <span className="hidden sm:inline">{session.firm_admin ? "Firm admin" : "Staff"}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>{session.firm_admin ? "Firm admin" : "Staff"}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onSignOut}>
                <LogOut />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 focus:outline-none sm:px-6">
          {children}
        </main>
      </div>
    </div>
  );
}

function SidebarNav({
  session, route, collapsed, onFollow, onSignOut,
}: {
  session: Session;
  route: Route;
  collapsed: boolean;
  onFollow: (event: MouseEvent, url: string) => void;
  onSignOut: () => void;
}) {
  const clientId = clientIdOf(route);
  const activeQueue = rememberedQueue(route);
  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex h-16 items-center px-4", collapsed && "lg:justify-center lg:px-2")}>
        <a href="/" onClick={(event) => onFollow(event, "/")} aria-label="FinLedger" className="flex items-center gap-2">
          <img src="/finledger-mark.png" alt="" className="size-9 rounded-md" />
          <span className={cn("text-sm font-semibold tracking-tight text-sidebar-foreground", collapsed && "lg:hidden")}>FinLedger</span>
        </a>
      </div>
      <nav id="desk-menu" aria-label="Pages" className="flex flex-1 flex-col gap-1 overflow-y-auto px-3 pb-4">
        <NavItem href="/" current={route.name === "home"} collapsed={collapsed} icon={<LayoutDashboard className="size-4" />} onClick={(event) => onFollow(event, "/")}>Dashboard</NavItem>
        <NavItem href="/clients" current={route.name === "clients" || route.name === "client"} collapsed={collapsed} icon={<Building2 className="size-4" />} onClick={(event) => onFollow(event, "/clients")}>Clients</NavItem>
        <p className={cn("mt-4 px-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground", collapsed && "lg:sr-only")}>Review</p>
        {clientId ? (
          <div role="group" aria-label="Review" className="flex flex-col gap-0.5">
            {QUEUES.map((queue) => (
              <NavItem
                key={queue.key}
                href={queueHref(clientId, queue.key)}
                current={activeQueue === queue.key}
                collapsed={collapsed}
                nested
                onClick={(event) => onFollow(event, queueHref(clientId, queue.key))}
              >
                {queue.label}
              </NavItem>
            ))}
          </div>
        ) : (
          <p className={cn("px-2 text-xs text-muted-foreground", collapsed && "lg:hidden")}>Open a client to see queues.</p>
        )}
        <div className="mt-auto flex flex-col gap-1 pt-6">
          {session.firm_admin && (
            <NavItem href="/staff" current={route.name === "staff"} collapsed={collapsed} icon={<Users className="size-4" />} onClick={(event) => onFollow(event, "/staff")}>Staff</NavItem>
          )}
          <button type="button" onClick={onSignOut} className="flex h-10 items-center gap-2 rounded-md px-2 text-left text-sm hover:bg-sidebar-accent">
            <LogOut className="size-4" />
            <span className={cn(collapsed && "lg:sr-only")}>Sign out</span>
          </button>
        </div>
      </nav>
    </div>
  );
}

function NavItem({
  href, current, collapsed, nested, icon, children, onClick,
}: {
  href: string;
  current: boolean;
  collapsed: boolean;
  nested?: boolean;
  icon?: ReactNode;
  children: string;
  onClick: (event: MouseEvent) => void;
}) {
  return (
    <a
      href={href}
      aria-current={current ? "page" : undefined}
      onClick={onClick}
      className={cn(
        "flex h-10 items-center gap-2 rounded-md px-2 text-sm hover:bg-sidebar-accent",
        nested && "pl-3",
        current && "bg-sidebar-accent font-medium",
        collapsed && "lg:justify-center lg:px-0",
      )}
    >
      {icon}
      <span className={cn("truncate", collapsed && "lg:sr-only")}>{children}</span>
    </a>
  );
}

function rememberedQueue(route: Route): QueueKey | null {
  if (route.name === "queue") return route.queue;
  if (route.name !== "invoice") return null;
  try {
    const raw = JSON.parse(sessionStorage.getItem("finledger-queue") || "") as { clientId?: string; queue?: QueueKey };
    return raw.clientId === route.clientId ? raw.queue ?? null : null;
  } catch {
    return null;
  }
}
