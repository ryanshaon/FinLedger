import { useCallback, useEffect, useState } from "react";
import { ApiError, api, errorMessage, needsMfa } from "@/api";
import { LoginScreen } from "@/components/LoginScreen";
import { Shell } from "@/components/Shell";
import { AcceptInvite } from "@/pages/AcceptInvite";
import { ClientsPage } from "@/pages/Clients";
import { Dashboard } from "@/pages/Dashboard";
import { InvoicePage } from "@/pages/Invoice";
import { MfaPage } from "@/pages/Mfa";
import { PageHeader } from "@/pages/Placeholder";
import { QueuePage } from "@/pages/Queue";
import { StaffPage } from "@/pages/Staff";
import { TransactionsPage } from "@/pages/Transactions";
import { parseRoute, QUEUES, type Route } from "@/router";
import type { Session } from "@/types";

type Phase = "checking" | "anon" | "in" | "down";

function routeKey(route: Route) {
  if (route.name === "queue") return `queue:${route.clientId}:${route.queue}`;
  if (route.name === "invoice") return `invoice:${route.clientId}:${route.documentId}`;
  if (route.name === "client") return `client:${route.clientId}`;
  return route.name;
}

function heading(route: Route): { title: string; description: string } {
  if (route.name === "home") return { title: "Dashboard", description: "Accounts payable across the firm." };
  if (route.name === "clients") return { title: "Clients", description: "Companies on the firm’s books." };
  if (route.name === "client") return { title: "Client", description: "Bills and status for this client." };
  if (route.name === "transactions") return { title: "Reconciliation", description: "Bank lines against purchase bills." };
  if (route.name === "queue") {
    const queue = QUEUES.find((item) => item.key === route.queue) ?? QUEUES[1];
    return { title: queue.label, description: queue.context };
  }
  if (route.name === "invoice") return { title: "Invoice", description: "Review the bill, then approve it." };
  if (route.name === "staff") return { title: "Staff", description: "Invite someone at the firm and choose the clients they can work on." };
  if (route.name === "mfa") return { title: "Two-step verification", description: "Enter the code from your authenticator app before you open a client." };
  if (route.name === "accept") return { title: "Set your password", description: "Choose a password, then sign in." };
  return { title: "This page is not on the desk", description: "Choose a client from the sidebar." };
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname, window.location.search));
  const [session, setSession] = useState<Session | null>(null);
  const [phase, setPhase] = useState<Phase>("checking");
  const [bootError, setBootError] = useState("");

  const go = useCallback((url: string) => {
    const next = new URL(url, window.location.origin);
    const there = next.pathname + next.search;
    const here = window.location.pathname + window.location.search;
    if (there !== here) history.pushState({}, "", there);
    setRoute(parseRoute(next.pathname, next.search));
  }, []);

  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.pathname, window.location.search));
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  useEffect(() => {
    if (route.name === "accept") return;
    let live = true;
    const fail = (error: unknown) => {
      if (!live) return;
      setSession(null);
      if (error instanceof ApiError && (error.status === 401 || error.status === 404)) {
        if (route.name !== "login") go("/login");
        else setPhase("anon");
        return;
      }
      setBootError(errorMessage(error, "The desk could not reach the control server."));
      setPhase("down");
    };
    if (route.name === "login") {
      api.session().then((next) => {
        if (!live) return;
        setSession(next);
        go(needsMfa(next) ? "/mfa" : "/");
      }).catch(fail);
      return () => { live = false; };
    }
    api.session().then((next) => {
      if (!live) return;
      setSession(next);
      setPhase("in");
      if (needsMfa(next) && route.name !== "mfa") go("/mfa");
      else if (!needsMfa(next) && route.name === "mfa") go("/");
    }).catch(fail);
    return () => { live = false; };
  }, [routeKey(route), go, route.name]);

  async function signOut() {
    try { await api.logout(); } catch { /* leave the desk even if the server is quiet */ }
    setSession(null);
    go("/login");
  }

  useEffect(() => {
    const { title } = heading(route);
    document.title = route.name === "login" ? "Sign in · FinLedger" : `${title} · FinLedger`;
  }, [route]);

  if (route.name === "accept") return <AcceptInvite onDone={() => go("/login")} />;
  if (phase === "down") {
    return <div className="grid min-h-screen place-items-center bg-background px-6 text-center text-foreground"><p role="alert">{bootError}</p></div>;
  }
  if (route.name === "login") {
    if (phase !== "anon") return <Splash />;
    return <LoginScreen onSignedIn={() => go("/")} />;
  }
  if (!session || phase === "checking") return <Splash />;

  const page = heading(route);
  let body = <PageHeader title={page.title} description={page.description} />;
  if (route.name === "home") body = <Dashboard onNavigate={go} />;
  else if (route.name === "clients") body = <ClientsPage onNavigate={go} />;
  else if (route.name === "client") body = <QueuePage key={`queue:${route.clientId}`} clientId={route.clientId} queue="needs_review" onNavigate={go} />;
  else if (route.name === "transactions") body = <TransactionsPage onNavigate={go} />;
  else if (route.name === "queue") body = <QueuePage key={`queue:${route.clientId}`} clientId={route.clientId} queue={route.queue} onNavigate={go} />;
  else if (route.name === "invoice") body = <InvoicePage key={`invoice:${route.clientId}:${route.documentId}`} clientId={route.clientId} documentId={route.documentId} onNavigate={go} />;
  else if (route.name === "staff") body = <StaffPage />;
  else if (route.name === "mfa") body = <MfaPage onVerified={() => go("/")} />;
  return (
    <Shell session={session} route={route} onNavigate={go} onSignOut={() => { void signOut(); }}>
      {body}
    </Shell>
  );
}

function Splash() {
  return (
    <div className="grid min-h-screen place-items-center bg-background text-foreground">
      <img src="/finledger-mark.png" alt="FinLedger" className="size-12 rounded-xl" />
    </div>
  );
}
