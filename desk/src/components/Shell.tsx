import { Bell, Building2, ChevronDown, LayoutDashboard, LogOut, Menu, Moon, PanelLeft, Search, Sun, Users } from "lucide-react";
import { useEffect, useId, useState, type MouseEvent, type ReactNode } from "react";
import { api, needsMfa } from "@/api";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
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
  const [query, setQuery] = useState("");
  const [night, setNight] = useState(currentTheme() === "night");
  const searchId = useId();
  const clientId = clientIdOf(route);

  useEffect(() => { setMobileOpen(false); }, [route]);

  useEffect(() => {
    if (needsMfa(session)) return;
    let live = true;
    api.clients().then((data) => { if (live) setClients(data.clients); }).catch(() => { if (live) setClients([]); });
    return () => { live = false; };
  }, [session.aal, session.mfa_required, session.user_id]);

  function follow(event: MouseEvent, url: string) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    onNavigate(url);
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
          <label htmlFor={searchId} className="relative ml-auto hidden min-w-0 md:block">
            <span className="sr-only">Search</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input id={searchId} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" className="h-9 w-56 pl-9" />
          </label>
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
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
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
